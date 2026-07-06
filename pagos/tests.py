"""
Tests de Fase 1 para `PagoMensual`: creación básica, unicidad por
(gimnasio, alumno, mes, año), autogeneración mensual, vencimiento de
pendientes atrasados y aislamiento por tenant.

Sigue el mismo criterio que `tenants/tests.py`: `django.test.TestCase` plano,
sin pytest ni factories (el proyecto es chico, KISS/YAGNI).
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from alumnos.models import Alumno
from pagos.models import PagoMensual, MedioCobro, generar_pagos_pendientes, marcar_vencidos
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class PagoMensualModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_crea_pago_y_str(self):
        pago = PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.assertEqual(pago.estado, PagoMensual.Estado.PENDIENTE)
        self.assertEqual(str(pago), "Perez, Juan - 03/2026")

    def test_unique_together_gimnasio_alumno_mes_anio(self):
        PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagoMensual.objects.create(
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
        pago_propio = PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        PagoMensual.objects.create(
            gimnasio=otro_gimnasio,
            alumno=otro_alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        pagos_del_gimnasio = PagoMensual.objects.for_gimnasio(self.gimnasio)

        self.assertEqual(list(pagos_del_gimnasio), [pago_propio])


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
            PagoMensual.objects.filter(gimnasio=self.gimnasio, mes=7, anio=2026).count(),
            2,
        )
        self.assertFalse(
            PagoMensual.objects.filter(alumno=self.inactivo, mes=7, anio=2026).exists()
        )

    def test_es_idempotente(self):
        generar_pagos_pendientes(mes=7, anio=2026)
        creados_segunda_vez = generar_pagos_pendientes(mes=7, anio=2026)

        self.assertEqual(creados_segunda_vez, 0)
        self.assertEqual(PagoMensual.objects.filter(mes=7, anio=2026).count(), 3)


class MarcarVencidosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def _crear_pendiente(self, mes, anio):
        return PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=mes,
            anio=anio,
            monto=Decimal("15000.00"),
        )

    def test_pendiente_de_mes_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=5, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, PagoMensual.Estado.VENCIDO)

    def test_pendiente_de_mes_actual_o_futuro_no_cambia(self):
        pago_actual = self._crear_pendiente(mes=7, anio=2026)
        pago_futuro = self._crear_pendiente(mes=8, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026)

        pago_actual.refresh_from_db()
        pago_futuro.refresh_from_db()
        self.assertEqual(actualizados, 0)
        self.assertEqual(pago_actual.estado, PagoMensual.Estado.PENDIENTE)
        self.assertEqual(pago_futuro.estado, PagoMensual.Estado.PENDIENTE)

    def test_pendiente_de_anio_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=12, anio=2025)

        actualizados = marcar_vencidos(mes=1, anio=2026)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, PagoMensual.Estado.VENCIDO)


class PagoMensualViewTests(TestCase):
    """Tests de Fase 2 para las vistas de gestión de pagos: acceso por rol,
    aislamiento de tenant y el flujo de confirmación (que es la única
    escritura que el staff puede hacer sobre un `PagoMensual` existente).

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

        self.pago_pendiente_a = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("0"),
        )
        self.pago_pagado_a = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=4,
            anio=2026,
            monto=Decimal("15000.00"),
            estado=PagoMensual.Estado.PAGADO,
        )
        self.pago_b = PagoMensual.objects.create(
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
        pago_vencido = PagoMensual.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=5,
            anio=2026,
            monto=Decimal("0"),
            estado=PagoMensual.Estado.VENCIDO,
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
        self.assertEqual(self.pago_pendiente_a.estado, PagoMensual.Estado.PAGADO)
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
