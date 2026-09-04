"""
Tests de Fase 1 para `Cuota`: creación básica, unicidad por
(gimnasio, alumno, mes, año), autogeneración mensual, vencimiento de
pendientes atrasados y aislamiento por tenant.

Sigue el mismo criterio que `tenants/tests.py`: `django.test.TestCase` plano,
sin pytest ni factories (el proyecto es chico, KISS/YAGNI).
"""

from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from alumnos.models import Alumno
from pagos.models import Cuota, MedioCobro, generar_pagos_pendientes, marcar_vencidos
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class CuotaModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_crea_pago_y_str(self):
        pago = Cuota.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.assertEqual(pago.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(str(pago), "Perez, Juan - 03/2026")

    def test_unique_together_gimnasio_alumno_mes_anio(self):
        Cuota.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cuota.objects.create(
                    gimnasio=self.gimnasio,
                    alumno=self.alumno,
                    mes=3,
                    anio=2026,
                    monto=Decimal("20000.00"),
                )

    def test_for_gimnasio_aisla_por_tenant(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        otro_alumno = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Ana", apellido="Gomez"
        )
        pago_propio = Cuota.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        Cuota.objects.create(
            gimnasio=otro_gimnasio,
            alumno=otro_alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        pagos_del_gimnasio = Cuota.objects.for_gimnasio(self.gimnasio)

        self.assertEqual(list(pagos_del_gimnasio), [pago_propio])

    def test_full_clean_rechaza_alumno_de_otro_gimnasio(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        alumno_de_otro = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Ana", apellido="Gomez"
        )
        pago = Cuota(
            gimnasio=self.gimnasio,
            alumno=alumno_de_otro,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        with self.assertRaises(ValidationError):
            pago.full_clean()


class GenerarPagosPendientesTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.activo_1 = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Juan",
            apellido="Perez",
            estado=Alumno.Estado.ACTIVO,
        )
        self.activo_2 = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Ana",
            apellido="Gomez",
            estado=Alumno.Estado.ACTIVO,
        )
        self.inactivo = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Luis",
            apellido="Diaz",
            estado=Alumno.Estado.INACTIVO,
        )

        self.otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        Alumno.objects.create(
            gimnasio=self.otro_gimnasio,
            nombre="Pedro",
            apellido="Ruiz",
            estado=Alumno.Estado.ACTIVO,
        )

    def test_genera_solo_para_alumnos_activos_del_gimnasio_correspondiente(self):
        creados = generar_pagos_pendientes(mes=7, anio=2026)

        self.assertEqual(creados, 3)
        self.assertEqual(
            Cuota.objects.filter(gimnasio=self.gimnasio, mes=7, anio=2026).count(),
            2,
        )
        self.assertFalse(
            Cuota.objects.filter(alumno=self.inactivo, mes=7, anio=2026).exists()
        )

    def test_es_idempotente(self):
        generar_pagos_pendientes(mes=7, anio=2026)
        creados_segunda_vez = generar_pagos_pendientes(mes=7, anio=2026)

        self.assertEqual(creados_segunda_vez, 0)
        self.assertEqual(Cuota.objects.filter(mes=7, anio=2026).count(), 3)


class GenerarPagosFechaLocalTests(TestCase):
    """El cron deriva mes/año/día de la fecha LOCAL, no de la UTC.

    `TIME_ZONE` es `America/Argentina/Buenos_Aires` (UTC-3): entre las 21:00 y
    las 23:59 la fecha UTC ya es la de mañana. Corrido a mano el último día
    del mes por la noche, el comando generaba las cuotas del mes SIGUIENTE con
    `dia=1`, o sea un mes entero de cuotas emitidas antes de tiempo.

    El horario agendado (06:30 UTC = 03:30 local) cae fuera de esa ventana,
    así que esto no afectó a ninguna corrida automática; el riesgo era la
    corrida manual, que es justo la que se hace cuando algo salió mal.
    """

    # 2026-06-01 01:00 UTC == 2026-05-31 22:00 en Buenos Aires.
    MOMENTO = datetime(2026, 6, 1, 1, 0, tzinfo=dt_timezone.utc)

    def test_usa_el_mes_local_y_no_el_utc(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        Alumno.objects.create(gimnasio=gimnasio, nombre="Ana", apellido="Gómez")

        salida = StringIO()
        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            call_command("generar_pagos", stdout=salida)

        self.assertTrue(Cuota.objects.filter(mes=5, anio=2026).exists())
        self.assertFalse(Cuota.objects.filter(mes=6, anio=2026).exists())


class MarcarVencidosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def _crear_pendiente(self, mes, anio):
        return Cuota.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=mes,
            anio=anio,
            monto=Decimal("15000.00"),
        )

    def test_pendiente_de_mes_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=5, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026, dia=15)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, Cuota.Estado.VENCIDO)

    def test_pendiente_de_mes_actual_antes_del_dia_limite_no_cambia(self):
        """`self.gimnasio` usa el default de `dia_vencimiento_pago` (10)."""
        pago_actual = self._crear_pendiente(mes=7, anio=2026)
        pago_futuro = self._crear_pendiente(mes=8, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026, dia=5)

        pago_actual.refresh_from_db()
        pago_futuro.refresh_from_db()
        self.assertEqual(actualizados, 0)
        self.assertEqual(pago_actual.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(pago_futuro.estado, Cuota.Estado.PENDIENTE)

    def test_pendiente_de_mes_actual_pasado_el_dia_limite_pasa_a_vencido(self):
        """Regresión: antes de este chequeo, `dia_vencimiento_pago` era
        solo cosmético en el portal del alumno -- un pago del mes en curso
        no pasaba a VENCIDO hasta que cambiaba el mes calendario, sin
        importar el día. `self.gimnasio` usa el default (10)."""
        pago_actual = self._crear_pendiente(mes=7, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026, dia=15)

        pago_actual.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_actual.estado, Cuota.Estado.VENCIDO)

    def test_dia_limite_se_evalua_por_gimnasio(self):
        """El día límite es un dato de `Gimnasio`, no un valor global --
        dos gimnasios con día límite distinto en el mismo día del mes
        pueden tener resultados distintos."""
        gimnasio_estricto = Gimnasio.objects.create(
            nombre="Gimnasio B", slug="gimnasio-b", dia_vencimiento_pago=5,
        )
        alumno_estricto = Alumno.objects.create(
            gimnasio=gimnasio_estricto, nombre="Ana", apellido="Gomez",
        )
        pago_flexible = self._crear_pendiente(mes=7, anio=2026)  # día límite 10
        pago_estricto = Cuota.objects.create(
            gimnasio=gimnasio_estricto,
            alumno=alumno_estricto,
            mes=7,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        actualizados = marcar_vencidos(mes=7, anio=2026, dia=8)

        pago_flexible.refresh_from_db()
        pago_estricto.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_flexible.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(pago_estricto.estado, Cuota.Estado.VENCIDO)

    def test_pendiente_de_anio_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=12, anio=2025)

        actualizados = marcar_vencidos(mes=1, anio=2026, dia=15)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, Cuota.Estado.VENCIDO)


class CuotaViewTests(TestCase):
    """Tests de Fase 2 para las vistas de gestión de pagos: acceso por rol,
    aislamiento de tenant y el flujo de confirmación (que es la única
    escritura que el staff puede hacer sobre un `Cuota` existente).

    `pagos.urls` todavía no está incluido en `config/urls.py` (lo integra
    quien reúna las apps de dominio), así que estas pruebas activan un
    urlconf propio -- ver `pagos/urls_test.py` -- en vez de tocar el
    urlconf raíz del proyecto.
    """

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Juan", apellido="Perez"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Ana", apellido="Gomez"
        )

        self.staff_user = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff_user, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.alumno_user = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno_user, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.pago_pendiente_a = Cuota.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("0"),
        )
        self.pago_pagado_a = Cuota.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=4,
            anio=2026,
            monto=Decimal("15000.00"),
            estado=Cuota.Estado.PAGADO,
        )
        self.pago_b = Cuota.objects.create(
            gimnasio=self.gimnasio_b,
            alumno=self.alumno_b,
            mes=3,
            anio=2026,
            monto=Decimal("0"),
        )

    def test_anonimo_es_redirigido_al_login(self):
        response = self.client.get(reverse("pagos:listado"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('pagos:listado')}",
        )

    def test_alumno_recibe_forbidden(self):
        self.client.login(username="alumno-a", password="clave-123456")

        response = self.client.get(reverse("pagos:listado"))

        self.assertEqual(response.status_code, 403)

    def test_staff_lista_solo_los_pagos_de_su_gimnasio(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("pagos:listado"))

        self.assertEqual(response.status_code, 200)
        pagos_listados = list(response.context["pagos"])
        self.assertIn(self.pago_pendiente_a, pagos_listados)
        self.assertIn(self.pago_pagado_a, pagos_listados)
        self.assertNotIn(self.pago_b, pagos_listados)

    def test_filtros_combinados_narrowen_el_resultado(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("pagos:listado"), {"mes": 3, "anio": 2026, "estado": "pendiente"}
        )

        pagos_listados = list(response.context["pagos"])
        self.assertEqual(pagos_listados, [self.pago_pendiente_a])

    def test_filtro_deudores_incluye_pendiente_y_vencido(self):
        self.client.login(username="staff-a", password="clave-123456")
        pago_vencido = Cuota.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=5,
            anio=2026,
            monto=Decimal("0"),
            estado=Cuota.Estado.VENCIDO,
        )

        response = self.client.get(reverse("pagos:listado"), {"estado": "deudores"})

        pagos_listados = list(response.context["pagos"])
        self.assertIn(self.pago_pendiente_a, pagos_listados)
        self.assertIn(pago_vencido, pagos_listados)
        self.assertNotIn(self.pago_pagado_a, pagos_listados)

    def test_confirmar_pago_de_otro_gimnasio_da_404(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("pagos:confirmar", args=[self.pago_b.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_confirmar_pago_pendiente_lo_marca_pagado_y_persiste_datos(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("pagos:confirmar", args=[self.pago_pendiente_a.pk]),
            {
                "monto": "15000.00",
                "fecha_pago": "2026-03-05",
                "medio_pago_texto": "Efectivo",
                "comprobante": "",
            },
        )

        self.assertRedirects(response, reverse("pagos:listado"))
        self.pago_pendiente_a.refresh_from_db()
        self.assertEqual(self.pago_pendiente_a.estado, Cuota.Estado.PAGADO)
        self.assertEqual(self.pago_pendiente_a.monto, Decimal("15000.00"))
        self.assertEqual(self.pago_pendiente_a.fecha_pago, date(2026, 3, 5))
        self.assertEqual(self.pago_pendiente_a.medio_pago_texto, "Efectivo")


class MedioCobroModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")

    def test_crea_medio_cobro_y_str(self):
        medio = MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="alias123456",
            titular="Juan Perez",
            entidad="Banco del Sudamericano",
            activo=True,
        )
        self.assertEqual(str(medio), "alias123456")
        self.assertTrue(medio.activo)

    def test_for_gimnasio_aisla_por_tenant(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        medio_propio = MedioCobro.objects.create(
            gimnasio=self.gimnasio,
            alias="alias_a",
            titular="Juan Perez",
        )
        MedioCobro.objects.create(
            gimnasio=otro_gimnasio,
            alias="alias_b",
            titular="Ana Gomez",
        )

        medios_del_gimnasio = MedioCobro.objects.for_gimnasio(self.gimnasio)

        self.assertEqual(list(medios_del_gimnasio), [medio_propio])


class MedioCobroViewTests(TestCase):
    """Tests de Task 11 para las vistas de gestión de medios de cobro:
    acceso por rol, aislamiento de tenant y el stampeo server-side de
    `gimnasio` al crear (mismo criterio que `CuotaViewTests`)."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.staff_user = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff_user, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )

        self.alumno_user = User.objects.create_user("alumno-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.alumno_user, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )

        self.medio_a = MedioCobro.objects.create(
            gimnasio=self.gimnasio_a, alias="alias_a", titular="Juan Perez"
        )
        self.medio_b = MedioCobro.objects.create(
            gimnasio=self.gimnasio_b, alias="alias_b", titular="Ana Gomez"
        )

    def test_anonimo_es_redirigido_al_login_en_listado(self):
        response = self.client.get(reverse("pagos:medios_listado"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('pagos:medios_listado')}",
        )

    def test_anonimo_es_redirigido_al_login_en_crear(self):
        response = self.client.get(reverse("pagos:medios_crear"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('pagos:medios_crear')}",
        )

    def test_anonimo_es_redirigido_al_login_en_editar(self):
        response = self.client.get(reverse("pagos:medios_editar", args=[self.medio_a.pk]))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('pagos:medios_editar', args=[self.medio_a.pk])}",
        )

    def test_alumno_recibe_forbidden_en_listado(self):
        self.client.login(username="alumno-a", password="clave-123456")

        response = self.client.get(reverse("pagos:medios_listado"))

        self.assertEqual(response.status_code, 403)

    def test_alumno_recibe_forbidden_en_crear(self):
        self.client.login(username="alumno-a", password="clave-123456")

        response = self.client.get(reverse("pagos:medios_crear"))

        self.assertEqual(response.status_code, 403)

    def test_alumno_recibe_forbidden_en_editar(self):
        self.client.login(username="alumno-a", password="clave-123456")

        response = self.client.get(reverse("pagos:medios_editar", args=[self.medio_a.pk]))

        self.assertEqual(response.status_code, 403)

    def test_staff_lista_solo_los_medios_de_su_gimnasio(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("pagos:medios_listado"))

        self.assertEqual(response.status_code, 200)
        medios_listados = list(response.context["medios"])
        self.assertIn(self.medio_a, medios_listados)
        self.assertNotIn(self.medio_b, medios_listados)

    def test_crear_medio_lo_asocia_al_gimnasio_del_staff_logueado(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("pagos:medios_crear"),
            {
                "alias": "nuevo.alias",
                "titular": "Pedro Ruiz",
                "entidad": "Banco Nuevo",
                "activo": "on",
            },
        )

        self.assertRedirects(response, reverse("pagos:medios_listado"))
        medio_creado = MedioCobro.objects.get(alias="nuevo.alias")
        self.assertEqual(medio_creado.gimnasio, self.gimnasio_a)

    def test_editar_medio_de_otro_gimnasio_da_404(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("pagos:medios_editar", args=[self.medio_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_editar_medio_permite_desactivarlo(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("pagos:medios_editar", args=[self.medio_a.pk]),
            {
                "alias": self.medio_a.alias,
                "titular": self.medio_a.titular,
                "entidad": "",
                # `activo` ausente del POST == checkbox destildado.
            },
        )

        self.assertRedirects(response, reverse("pagos:medios_listado"))
        self.medio_a.refresh_from_db()
        self.assertFalse(self.medio_a.activo)


class AlumnoComprobanteUpdateViewTests(TestCase):
    """El alumno sube el comprobante de SU PROPIO pago PENDIENTE/VENCIDO
    (`AlumnoComprobanteUpdateView`, evento 8 de `notificaciones` -- ver
    `CLAUDE.md`). No existía este flujo antes: el staff era quien subía el
    comprobante al confirmar (`ConfirmarPagoViewTests`, arriba)."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

        self.alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Bruno", apellido="Pérez"
        )
        self.alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Carla", apellido="Ruiz"
        )

        self.user_a = User.objects.create_user("alumno-a", password="clave-123456")
        self.perfil_a = Perfil.objects.create(
            usuario=self.user_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO
        )
        self.alumno_a.perfil = self.perfil_a
        self.alumno_a.save()

        self.pago_propio = Cuota.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.pago_de_otro_alumno = Cuota.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.otro_alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.pago_de_otro_gimnasio = Cuota.objects.create(
            gimnasio=self.gimnasio_b,
            alumno=self.alumno_b,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        self.client.login(username="alumno-a", password="clave-123456")

    def _archivo(self):
        return SimpleUploadedFile(
            "comprobante.jpg", b"contenido-de-prueba", content_type="image/jpeg"
        )

    def _archivo_no_permitido(self):
        return SimpleUploadedFile(
            "comprobante.pdf", b"contenido-de-prueba", content_type="application/pdf"
        )

    @patch("notificaciones.services._enviar")
    def test_sube_comprobante_a_su_propio_pago_pendiente(self, mock_enviar):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("pagos:comprobante_subir", args=[self.pago_propio.pk]),
                {"comprobante": self._archivo()},
            )

        self.assertRedirects(response, reverse("home"))
        self.pago_propio.refresh_from_db()
        self.assertTrue(self.pago_propio.comprobante)
        self.assertEqual(self.pago_propio.estado, Cuota.Estado.PENDIENTE)

    def test_sube_comprobante_a_pago_vencido(self):
        self.pago_propio.estado = Cuota.Estado.VENCIDO
        self.pago_propio.save(update_fields=["estado"])

        response = self.client.post(
            reverse("pagos:comprobante_subir", args=[self.pago_propio.pk]),
            {"comprobante": self._archivo()},
        )

        self.assertRedirects(response, reverse("home"))
        self.pago_propio.refresh_from_db()
        self.assertTrue(self.pago_propio.comprobante)
        self.assertEqual(self.pago_propio.estado, Cuota.Estado.VENCIDO)

    def test_rechaza_archivo_que_no_es_jpg_ni_png(self):
        response = self.client.post(
            reverse("pagos:comprobante_subir", args=[self.pago_propio.pk]),
            {"comprobante": self._archivo_no_permitido()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "comprobante",
            "La extensión de archivo “pdf” no está permitida. Las extensiones "
            "aceptadas son: “jpg, jpeg, png”.",
        )
        self.pago_propio.refresh_from_db()
        self.assertFalse(self.pago_propio.comprobante)

    def test_404_en_pago_de_otro_alumno_del_mismo_gimnasio(self):
        response = self.client.get(
            reverse("pagos:comprobante_subir", args=[self.pago_de_otro_alumno.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_404_en_pago_de_otro_gimnasio(self):
        response = self.client.get(
            reverse("pagos:comprobante_subir", args=[self.pago_de_otro_gimnasio.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_404_en_pago_ya_pagado(self):
        self.pago_propio.estado = Cuota.Estado.PAGADO
        self.pago_propio.save(update_fields=["estado"])

        response = self.client.get(
            reverse("pagos:comprobante_subir", args=[self.pago_propio.pk])
        )
        self.assertEqual(response.status_code, 404)

    @patch("notificaciones.services._enviar")
    def test_dispara_notificacion_al_staff_del_gimnasio_correcto(self, mock_enviar):
        from notificaciones.models import SuscripcionPush

        staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF)
        SuscripcionPush.objects.create(
            gimnasio=self.gimnasio_a,
            usuario=staff,
            endpoint="https://push.example.com/staff-a",
            p256dh="p",
            auth="a",
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("pagos:comprobante_subir", args=[self.pago_propio.pk]),
                {"comprobante": self._archivo()},
            )

        mock_enviar.assert_called_once()
        (suscripcion_llamada, _payload), _ = mock_enviar.call_args
        self.assertEqual(suscripcion_llamada.usuario, staff)
