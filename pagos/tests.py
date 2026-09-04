"""
Tests de Fase 1 para `Cuota`: creación básica, unicidad por
(gimnasio, alumno, mes, año), autogeneración mensual, vencimiento de
pendientes atrasados y aislamiento por tenant.

Sigue el mismo criterio que `tenants/tests.py`: `django.test.TestCase` plano,
sin pytest ni factories (el proyecto es chico, KISS/YAGNI).
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from pagos import acceso
from pagos.models import (
    DIAS_CICLO,
    Cuota,
    MedioCobro,
    generar_pagos_pendientes,
    marcar_vencidos,
)
from pagos.testing import crear_cuota, crear_cuota_mensual
from tenants.models import Gimnasio, Perfil

User = get_user_model()


class CuotaModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_crea_pago_y_str(self):
        pago = crear_cuota_mensual(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.assertEqual(pago.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(str(pago), "Perez, Juan - 01/03/2026")

    def test_unique_together_gimnasio_alumno_periodo_inicio(self):
        crear_cuota_mensual(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                crear_cuota_mensual(
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
        pago_propio = crear_cuota_mensual(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        crear_cuota_mensual(
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
    """Emisión de cuotas por ciclo de 28 días anclado a cada alumno."""

    def setUp(self):
        self.hoy = date(2026, 7, 15)
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.activo_1 = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez",
            estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo=date(2026, 7, 1),
        )
        self.activo_2 = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez",
            estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo=date(2026, 7, 10),
        )
        self.inactivo = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Luis", apellido="Diaz",
            estado=Alumno.Estado.INACTIVO, fecha_inicio_ciclo=date(2026, 7, 1),
        )
        self.otro_gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio B", slug="gimnasio-b"
        )
        Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Pedro", apellido="Ruiz",
            estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo=date(2026, 7, 1),
        )

    def test_genera_solo_para_alumnos_activos_del_gimnasio_correspondiente(self):
        generar_pagos_pendientes(self.hoy)

        self.assertEqual(Cuota.objects.filter(gimnasio=self.gimnasio).count(), 2)
        self.assertFalse(Cuota.objects.filter(alumno=self.inactivo).exists())
        self.assertEqual(Cuota.objects.filter(gimnasio=self.otro_gimnasio).count(), 1)

    def test_el_periodo_arranca_en_el_ancla_de_cada_alumno(self):
        """Dos alumnos del mismo gimnasio con anclas distintas cobran en
        fechas distintas: es el punto entero de la migración a ciclos."""
        generar_pagos_pendientes(self.hoy)

        self.assertEqual(
            Cuota.objects.get(alumno=self.activo_1).periodo_inicio, date(2026, 7, 1)
        )
        self.assertEqual(
            Cuota.objects.get(alumno=self.activo_2).periodo_inicio, date(2026, 7, 10)
        )

    def test_el_periodo_fin_es_inclusivo_y_dura_28_dias(self):
        generar_pagos_pendientes(self.hoy)

        cuota = Cuota.objects.get(alumno=self.activo_1)
        self.assertEqual(cuota.periodo_fin, date(2026, 7, 28))
        self.assertEqual((cuota.periodo_fin - cuota.periodo_inicio).days + 1, 28)

    def test_es_idempotente(self):
        generar_pagos_pendientes(self.hoy)
        creados_segunda_vez = generar_pagos_pendientes(self.hoy)

        self.assertEqual(creados_segunda_vez, 0)
        self.assertEqual(Cuota.objects.count(), 3)

    def test_no_emite_nada_al_alumno_sin_ancla(self):
        """Sin ancla no hay ciclo. Es un agujero de facturación posible, así
        que las señales del alta y de la primera rutina la estampan siempre;
        acá se fija que la función no invente un ciclo por su cuenta."""
        Alumno.objects.filter(pk=self.activo_1.pk).update(fecha_inicio_ciclo=None)

        generar_pagos_pendientes(self.hoy)

        self.assertFalse(Cuota.objects.filter(alumno=self.activo_1).exists())

    def test_no_emite_nada_si_el_ancla_todavia_no_llego(self):
        """REGRESIÓN. Un ancla futura da un índice de ciclo NEGATIVO
        (`(-6)//28 == -1` en Python), o sea una cuota por un período que
        arrancó semanas ANTES del alta: nace vencida y —con la tolerancia
        prendida— bloquea al alumno antes de su primer entrenamiento.

        Dos distancias a propósito: 6 días (índice -1) y 30 días (índice -2),
        que además deja el período entero en el pasado."""
        for dias in (6, 30):
            with self.subTest(dias=dias):
                Cuota.objects.all().delete()
                Alumno.objects.filter(pk=self.activo_1.pk).update(
                    fecha_inicio_ciclo=self.hoy + timedelta(days=dias)
                )
                generar_pagos_pendientes(self.hoy)
                self.assertFalse(Cuota.objects.filter(alumno=self.activo_1).exists())

    def test_emite_el_ciclo_siguiente_cuando_ya_esta_a_la_vista(self):
        """Sin la fila del ciclo que viene, el alumno que quiere pagar
        adelantado no tiene dónde subir el comprobante."""
        vispera = date(2026, 7, 1) + timedelta(days=DIAS_CICLO - 1)

        generar_pagos_pendientes(vispera)

        periodos = set(
            Cuota.objects.filter(alumno=self.activo_1).values_list(
                "periodo_inicio", flat=True
            )
        )
        self.assertEqual(periodos, {date(2026, 7, 1), date(2026, 7, 29)})

    def test_los_ciclos_son_contiguos_y_no_se_solapan(self):
        """`periodo_fin` es inclusivo, así que el ciclo siguiente arranca
        exactamente al día siguiente. Un día de diferencia acá es un día
        cobrado dos veces o uno sin cubrir."""
        generar_pagos_pendientes(date(2026, 7, 1) + timedelta(days=DIAS_CICLO - 1))

        primero, segundo = Cuota.objects.filter(alumno=self.activo_1).order_by(
            "periodo_inicio"
        )
        self.assertEqual(segundo.periodo_inicio, primero.periodo_fin + timedelta(days=1))

    def test_dos_ciclos_del_mismo_mes_calendario_no_chocan(self):
        """REGRESIÓN del `unique_together` viejo `(gimnasio, alumno, mes,
        anio)`. Con 28 días hay 13 arranques por año contra 12 meses: un
        alumno cuyo ciclo arranca el día 1 arranca otro el día 29 del MISMO
        mes. Con el unique viejo vivo eso era un `IntegrityError` que, al ir
        por `bulk_create`, abortaba la emisión del gimnasio entero."""
        Alumno.objects.filter(pk=self.activo_1.pk).update(
            fecha_inicio_ciclo=date(2026, 7, 1)
        )

        generar_pagos_pendientes(date(2026, 7, 1))
        generar_pagos_pendientes(date(2026, 7, 29))

        arranques = list(
            Cuota.objects.filter(alumno=self.activo_1)
            .order_by("periodo_inicio")
            .values_list("periodo_inicio", flat=True)
        )
        self.assertEqual(arranques, [date(2026, 7, 1), date(2026, 7, 29)])

    def test_llena_mes_y_anio_para_que_la_vuelta_atras_funcione(self):
        """`mes`/`anio` son columnas muertas pero son la red del rollback: si
        se revierte el código, el viejo filtra por ellas y formatea
        `f"{self.mes:02d}"`, que con `None` revienta. `bulk_create` no pasa
        por `save()`, así que se llenan a mano."""
        generar_pagos_pendientes(self.hoy)

        cuota = Cuota.objects.get(alumno=self.activo_2)
        self.assertEqual((cuota.mes, cuota.anio), (7, 2026))


class GenerarPagosEscalaTests(TestCase):
    """El costo en queries no puede crecer con la cantidad de alumnos.

    Es la regla que este proyecto ya pagó con un 502 en producción (el
    importador hacía N queries por fila). El test compara DOS tamaños de
    conjunto en vez de fijar un número absoluto, que se rompe con cualquier
    cambio interno de Django.
    """

    def _sembrar(self, slug, cantidad):
        gimnasio = Gimnasio.objects.create(nombre=slug, slug=slug)
        Alumno.objects.bulk_create(
            [
                Alumno(
                    gimnasio=gimnasio, nombre=f"A{i}", apellido="X",
                    estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo=date(2026, 7, 1),
                )
                for i in range(cantidad)
            ]
        )
        return gimnasio

    def test_el_costo_no_crece_con_la_cantidad_de_alumnos(self):
        hoy = date(2026, 7, 15)
        self._sembrar("chico", 3)
        with CaptureQueriesContext(connection) as chico:
            generar_pagos_pendientes(hoy)

        self._sembrar("grande", 40)
        with CaptureQueriesContext(connection) as grande:
            generar_pagos_pendientes(hoy)

        self.assertEqual(len(grande), len(chico))


class MarcarVencidosTests(TestCase):
    """Vencimiento por ciclo, con y sin tolerancia configurada."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def _pendiente(self, inicio, gimnasio=None, alumno=None):
        return crear_cuota(
            gimnasio=gimnasio or self.gimnasio,
            alumno=alumno or self.alumno,
            inicio=inicio,
            monto=Decimal("15000.00"),
        )

    def _con_tolerancia(self, dias, gimnasio=None, activacion=date(2026, 1, 1)):
        gimnasio = gimnasio or self.gimnasio
        gimnasio.dias_tolerancia_pago = dias
        gimnasio.save()
        # La señal estampa la activación en el HOY real, que es posterior a
        # todas las fechas de estos tests: se retrocede para que las cuotas
        # del fixture queden bajo el régimen de la tolerancia.
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            fecha_activacion_bloqueo=activacion
        )
        gimnasio.refresh_from_db()


    # --- sin tolerancia: el estado de TODOS los gimnasios al desplegar ---

    def test_sin_tolerancia_no_vence_mientras_el_ciclo_corre(self):
        """REGRESIÓN, y el incidente más grave que evitó este diseño. Si con
        la tolerancia vacía se aplicara el umbral como 0 días, el día del
        deploy pasaba a VENCIDO todo el padrón y salía un push masivo de «tu
        cuota está vencida» a alumnos que estaban al día."""
        cuota = self._pendiente(date(2026, 7, 1))

        actualizados = marcar_vencidos(date(2026, 7, 20))

        cuota.refresh_from_db()
        self.assertEqual(actualizados, 0)
        self.assertEqual(cuota.estado, Cuota.Estado.PENDIENTE)

    def test_sin_tolerancia_vence_recien_cuando_el_ciclo_termino(self):
        """La traducción honesta del comportamiento viejo: «vence cuando el
        mes cerró» pasa a «vence cuando el ciclo cerró»."""
        cuota = self._pendiente(date(2026, 7, 1))  # cubre hasta el 28 inclusive

        self.assertEqual(marcar_vencidos(date(2026, 7, 28)), 0)
        self.assertEqual(marcar_vencidos(date(2026, 7, 29)), 1)

        cuota.refresh_from_db()
        self.assertEqual(cuota.estado, Cuota.Estado.VENCIDO)

    # --- con tolerancia ---

    def test_con_tolerancia_vence_al_pasarse_los_dias_desde_el_arranque(self):
        """Tolerancia 3 y ciclo del 1/7: el 3/7 todavía se puede pagar, el
        4/7 ya venció -- que es el MISMO día en que `acceso.py` bloquea.
        La versión anterior de este test fijaba el 5/7, un día tarde."""
        self._con_tolerancia(3)
        cuota = self._pendiente(date(2026, 7, 1))

        self.assertEqual(marcar_vencidos(date(2026, 7, 3)), 0)
        self.assertEqual(marcar_vencidos(date(2026, 7, 4)), 1)

        cuota.refresh_from_db()
        self.assertEqual(cuota.estado, Cuota.Estado.VENCIDO)

    def test_con_tolerancia_vence_el_mismo_dia_que_bloquea(self):
        """REGRESIÓN. `marcar_vencidos` usaba `<` donde `acceso.py` usa `<=`:
        con tolerancia 3, el día 4 el alumno estaba bloqueado (sin rutina ni
        turnos) y el panel y el portal le decían «Pendiente». Es exactamente
        el día que el docstring de `marcar_vencidos` promete que no existe."""
        self._con_tolerancia(3)
        self._pendiente(date(2026, 7, 1))
        hoy = date(2026, 7, 4)

        self.assertIsNotNone(acceso.bloqueo_de(self.alumno, hoy=hoy))
        self.assertEqual(marcar_vencidos(hoy), 1)

    def test_las_cuotas_anteriores_a_la_activacion_no_vencen_por_tolerancia(self):
        """REGRESIÓN. `acceso.py` ignora las cuotas anteriores a
        `fecha_activacion_bloqueo`, pero `marcar_vencidos` no: el día que un
        dueño prendía la tolerancia, el cron pasaba a VENCIDO a todo el ciclo
        en curso más viejo que la tolerancia y `enviar_recordatorios` les
        mandaba «tu cuota está vencida» a alumnos que, por diseño, NO estaban
        bloqueados. La ráfaga que la fecha de activación existe para evitar,
        entrando por la otra puerta."""
        self._con_tolerancia(3, activacion=date(2026, 7, 1))
        anterior = self._pendiente(date(2026, 6, 20))  # cubre hasta el 17/7
        posterior = self._pendiente(date(2026, 7, 2))

        self.assertEqual(marcar_vencidos(date(2026, 7, 5)), 1)

        anterior.refresh_from_db()
        posterior.refresh_from_db()
        self.assertEqual(anterior.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(posterior.estado, Cuota.Estado.VENCIDO)
        # La anterior sigue el régimen de siempre: vence al terminar su ciclo.
        self.assertEqual(marcar_vencidos(date(2026, 7, 17)), 0)
        self.assertEqual(marcar_vencidos(date(2026, 7, 18)), 1)

    def test_la_regla_en_sql_coincide_con_la_regla_en_python(self):
        """`filtro_por_limite_de_pago` es `limite_de_pago` escrita en SQL, y
        de ella dependen vencer (`marcar_vencidos`) y avisar «por vencer» (el
        cron). Si divergen, hay un día en que el alumno está bloqueado y la
        app dice «Pendiente», o un aviso que llega con el acceso ya cortado.
        Se recorren 70 días y cinco tolerancias, con cuotas de los dos lados
        de la fecha de activación."""
        from pagos.models import filtro_por_limite_de_pago, limite_de_pago

        activacion = date(2026, 7, 1)
        cuotas = [
            self._pendiente(date(2026, 6, 20)),
            self._pendiente(date(2026, 7, 2)),
            self._pendiente(date(2026, 7, 30)),
        ]
        for tolerancia in (None, 0, 1, 3, 10):
            for corrimiento in range(70):
                hoy = date(2026, 6, 20) + timedelta(days=corrimiento)
                ventana = (hoy, hoy + timedelta(days=3))

                def limite(cuota):
                    return limite_de_pago(
                        cuota.periodo_inicio, cuota.periodo_fin, tolerancia, activacion
                    )

                vencidas_python = {c.pk for c in cuotas if limite(c) < hoy}
                vencidas_sql = set(
                    Cuota.objects.filter(
                        filtro_por_limite_de_pago(
                            tolerancia, activacion, hasta=hoy - timedelta(days=1)
                        )
                    ).values_list("pk", flat=True)
                )
                self.assertEqual(vencidas_sql, vencidas_python, (tolerancia, hoy))

                por_vencer_python = {
                    c.pk for c in cuotas if ventana[0] <= limite(c) <= ventana[1]
                }
                por_vencer_sql = set(
                    Cuota.objects.filter(
                        filtro_por_limite_de_pago(
                            tolerancia, activacion, desde=ventana[0], hasta=ventana[1]
                        )
                    ).values_list("pk", flat=True)
                )
                self.assertEqual(por_vencer_sql, por_vencer_python, (tolerancia, hoy))


    def test_la_tolerancia_se_evalua_por_gimnasio(self):
        """No es un valor global: dos gimnasios con tolerancias distintas
        pueden dar resultados distintos el mismo día."""
        self._con_tolerancia(10)
        estricto = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self._con_tolerancia(2, gimnasio=estricto)

        alumno_estricto = Alumno.objects.create(
            gimnasio=estricto, nombre="Ana", apellido="Gomez"
        )
        flexible = self._pendiente(date(2026, 7, 1))
        rigurosa = self._pendiente(
            date(2026, 7, 1), gimnasio=estricto, alumno=alumno_estricto
        )

        actualizados = marcar_vencidos(date(2026, 7, 6))

        flexible.refresh_from_db()
        rigurosa.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(flexible.estado, Cuota.Estado.PENDIENTE)
        self.assertEqual(rigurosa.estado, Cuota.Estado.VENCIDO)

    def test_no_toca_las_pagadas_ni_las_anuladas(self):
        self._con_tolerancia(0)
        pagada = self._pendiente(date(2026, 5, 1))
        pagada.estado = Cuota.Estado.PAGADO
        pagada.save()
        anulada = self._pendiente(date(2026, 6, 1))
        anulada.estado = Cuota.Estado.ANULADO
        anulada.save()

        self.assertEqual(marcar_vencidos(date(2026, 7, 20)), 0)

    def test_escribe_modificado_para_que_salga_el_push(self):
        """REGRESIÓN de un bug preexistente. `QuerySet.update()` NO dispara
        `auto_now`, y `enviar_recordatorios` filtra por `modificado__date=hoy`
        para saber a quién avisarle: sin escribir `modificado` a mano, el push
        de «cuota vencida» no salía nunca."""
        self._con_tolerancia(0)
        cuota = self._pendiente(date(2026, 5, 1))
        modificado_antes = cuota.modificado

        marcar_vencidos(date(2026, 7, 20))

        cuota.refresh_from_db()
        self.assertGreater(cuota.modificado, modificado_antes)


class GenerarPagosFechaLocalTests(TestCase):
    """El cron deriva "hoy" de la fecha LOCAL, no de la UTC.

    `TIME_ZONE` es `America/Argentina/Buenos_Aires` (UTC-3): entre las 21:00 y
    las 23:59 la fecha UTC ya es la de mañana, así que una corrida manual a esa
    hora emitía cuotas fechadas un día adelante.

    El reloj se congela con `patch` sobre `django.utils.timezone.now` (que es
    lo que `localdate()` resuelve por atributo de módulo) y con una fecha LEJANA
    a hoy: con una cercana el test compara contra la fecha real y pasa o falla
    por el motivo equivocado.
    """

    # 2026-06-01 01:00 UTC == 2026-05-31 22:00 en Buenos Aires.
    MOMENTO = datetime(2026, 6, 1, 1, 0, tzinfo=dt_timezone.utc)

    def test_usa_la_fecha_local_y_no_la_utc(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        Alumno.objects.create(
            gimnasio=gimnasio, nombre="Ana", apellido="Gómez",
            fecha_inicio_ciclo=date(2026, 5, 4),
        )

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            call_command("generar_pagos", stdout=StringIO())

        # Ancla el 4/5. Con la fecha LOCAL (31/5) el ciclo vigente es el que
        # arrancó el 4/5 -- el que de verdad está corriendo. Con la UTC (1/6)
        # el índice avanza uno y el vigente pasaría a ser el del 1/6, saltando
        # un ciclo entero de facturación.
        #
        # El ciclo del 1/6 aparece en los DOS casos (como pre-emisión del
        # siguiente cuando la fecha es local), así que lo que distingue no es
        # su ausencia sino la presencia del 4/5.
        self.assertTrue(Cuota.objects.filter(periodo_inicio=date(2026, 5, 4)).exists())


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

        self.pago_pendiente_a = crear_cuota_mensual(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("0"),
        )
        self.pago_pagado_a = crear_cuota_mensual(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=4,
            anio=2026,
            monto=Decimal("15000.00"),
            estado=Cuota.Estado.PAGADO,
        )
        self.pago_b = crear_cuota_mensual(
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
        pago_vencido = crear_cuota_mensual(
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

    def test_una_fecha_imposible_en_el_filtro_no_da_500(self):
        """REGRESIÓN. `parse_date` devuelve `None` con un texto mal formado,
        pero LANZA `ValueError` con uno bien formado e imposible: `?desde=
        2026-02-31` era un 500, contra lo que prometía el docstring."""
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("pagos:listado"), {"desde": "2026-02-31", "hasta": "2026-13-01"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.pago_pendiente_a, list(response.context["pagos"]))

    # --- anular (condonar) una cuota ---

    def test_anular_condona_la_cuota_y_la_saca_de_la_deuda(self):
        """`Estado.ANULADO` existía desde la migración a ciclos pero ninguna
        vista lo escribía: la única salida para destrabar a un becado sin
        falsear la facturación estaba en `/admin/`."""
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(
            reverse("pagos:anular", args=[self.pago_pendiente_a.pk])
        )

        self.assertRedirects(response, reverse("pagos:listado"))
        self.pago_pendiente_a.refresh_from_db()
        self.assertEqual(self.pago_pendiente_a.estado, Cuota.Estado.ANULADO)
        self.assertNotIn(
            self.pago_pendiente_a, list(acceso.cuotas_impagas_de(self.alumno_a))
        )

    def test_reactivar_una_anulada_la_vuelve_pendiente(self):
        self.client.login(username="staff-a", password="clave-123456")
        self.pago_pendiente_a.estado = Cuota.Estado.ANULADO
        self.pago_pendiente_a.save()

        self.client.post(reverse("pagos:anular", args=[self.pago_pendiente_a.pk]))

        self.pago_pendiente_a.refresh_from_db()
        self.assertEqual(self.pago_pendiente_a.estado, Cuota.Estado.PENDIENTE)

    def test_una_cuota_pagada_no_se_anula(self):
        """Si el cobro fue un error se edita desde «Confirmar pago»; anular
        una cuota cobrada borraría un ingreso real de la facturación."""
        self.client.login(username="staff-a", password="clave-123456")

        self.assertEqual(
            self.client.get(reverse("pagos:anular", args=[self.pago_pagado_a.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("pagos:anular", args=[self.pago_pagado_a.pk])).status_code,
            404,
        )
        self.pago_pagado_a.refresh_from_db()
        self.assertEqual(self.pago_pagado_a.estado, Cuota.Estado.PAGADO)

    def test_anular_una_cuota_de_otro_gimnasio_da_404(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.post(reverse("pagos:anular", args=[self.pago_b.pk]))

        self.assertEqual(response.status_code, 404)
        self.pago_b.refresh_from_db()
        self.assertEqual(self.pago_b.estado, Cuota.Estado.PENDIENTE)

    def test_el_alumno_no_puede_anular(self):
        self.client.login(username="alumno-a", password="clave-123456")

        response = self.client.post(
            reverse("pagos:anular", args=[self.pago_pendiente_a.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_la_confirmacion_explica_que_anular_no_suma_ingresos(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(
            reverse("pagos:anular", args=[self.pago_pendiente_a.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no suma a los ingresos")

    def test_el_listado_ofrece_anular_solo_a_las_impagas(self):
        self.client.login(username="staff-a", password="clave-123456")

        response = self.client.get(reverse("pagos:listado"))

        self.assertContains(
            response, reverse("pagos:anular", args=[self.pago_pendiente_a.pk])
        )
        self.assertNotContains(
            response, reverse("pagos:anular", args=[self.pago_pagado_a.pk])
        )


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

        self.pago_propio = crear_cuota_mensual(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.pago_de_otro_alumno = crear_cuota_mensual(
            gimnasio=self.gimnasio_a,
            alumno=self.otro_alumno_a,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.pago_de_otro_gimnasio = crear_cuota_mensual(
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


class BloqueoPorFaltaDePagoTests(TestCase):
    """El portón: `pagos/acceso.py` y su efecto en las vistas del alumno."""

    def setUp(self):
        self.hoy = date(2026, 7, 20)
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio A", slug="gimnasio-a", dias_tolerancia_pago=3
        )
        # La señal estampa la activación en la fecha REAL de hoy; estos tests
        # trabajan con fechas fijas de 2026, así que se retrocede a mano. El
        # comportamiento anti-retroactivo tiene su propio test más abajo.
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        self.gimnasio.refresh_from_db()
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez",
            fecha_inicio_ciclo=date(2026, 7, 1),
        )

    def _cuota(self, inicio, **extra):
        return crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno, inicio=inicio, **extra
        )

    def test_sin_tolerancia_configurada_no_bloquea_a_nadie(self):
        """El estado de TODOS los gimnasios al desplegar. Si esto fallara, el
        deploy dejaría sin rutina a medio padrón el primer día."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=None, fecha_activacion_bloqueo=None
        )
        self.alumno.refresh_from_db()
        self._cuota(date(2026, 1, 1))

        self.assertIsNone(acceso.bloqueo_de(self.alumno, hoy=self.hoy))

    def test_bloquea_recien_pasada_la_tolerancia(self):
        cuota = self._cuota(date(2026, 7, 18))  # 2 días de atraso, tolerancia 3

        self.assertIsNone(acceso.bloqueo_de(self.alumno, hoy=date(2026, 7, 20)))

        bloqueo = acceso.bloqueo_de(self.alumno, hoy=date(2026, 7, 21))
        self.assertIsNotNone(bloqueo)
        self.assertEqual(bloqueo.cuota, cuota)
        self.assertEqual(bloqueo.dias_de_atraso, 3)

    def test_una_cuota_pagada_no_bloquea(self):
        self._cuota(date(2026, 6, 1), estado=Cuota.Estado.PAGADO)

        self.assertIsNone(acceso.bloqueo_de(self.alumno, hoy=self.hoy))

    def test_una_cuota_anulada_no_bloquea(self):
        """ANULADO es la salida del staff para condonar sin falsear un pago.
        Sin este estado, destrabar a un becado o a alguien de licencia
        obligaba a marcar PAGADO y ensuciar `ingresos_por_mes`."""
        self._cuota(date(2026, 6, 1), estado=Cuota.Estado.ANULADO)

        self.assertIsNone(acceso.bloqueo_de(self.alumno, hoy=self.hoy))

    def test_no_bloquea_por_cuotas_anteriores_a_prender_la_funcion(self):
        """REGRESIÓN, el hallazgo más grave de la revisión del plan. Sin la
        fecha de activación, el día que el dueño prende la tolerancia quedan
        bloqueados de golpe todos los que arrastren cualquier impaga
        histórica: el que estuvo de licencia, el que pagó en efectivo y nadie
        confirmó, el becado. Es exactamente «un alumno que pagó y se queda sin
        acceso», multiplicado por el gimnasio entero."""
        vieja = self._cuota(date(2026, 1, 1))
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            fecha_activacion_bloqueo=date(2026, 7, 1)
        )
        self.alumno.refresh_from_db()
        self.alumno.gimnasio.refresh_from_db()

        self.assertIsNone(acceso.bloqueo_de(self.alumno, hoy=self.hoy))

        # Pero una cuota posterior a la activación sí bloquea.
        nueva = self._cuota(date(2026, 7, 2))
        bloqueo = acceso.bloqueo_de(self.alumno, hoy=self.hoy)
        self.assertEqual(bloqueo.cuota, nueva)
        self.assertNotEqual(bloqueo.cuota, vieja)

    def test_devuelve_la_cuota_mas_vieja_de_las_que_bloquean(self):
        """Es la que el alumno tiene que saldar primero."""
        primera = self._cuota(date(2026, 7, 1))
        self._cuota(date(2026, 7, 15))

        self.assertEqual(acceso.bloqueo_de(self.alumno, hoy=self.hoy).cuota, primera)

    def test_la_senal_estampa_la_activacion_solo_en_la_transicion(self):
        """Si reestampara en cada guardado, cambiarle el logo al gimnasio
        movería la fecha de corte y perdonaría deudas que ya bloqueaban."""
        original = self.gimnasio.fecha_activacion_bloqueo
        self.assertIsNotNone(original)

        self.gimnasio.nombre = "Otro nombre"
        self.gimnasio.save()
        self.gimnasio.refresh_from_db()
        self.assertEqual(self.gimnasio.fecha_activacion_bloqueo, original)

        # Apagar limpia la marca, para que volver a prender corte desde cero.
        self.gimnasio.dias_tolerancia_pago = None
        self.gimnasio.save()
        self.gimnasio.refresh_from_db()
        self.assertIsNone(self.gimnasio.fecha_activacion_bloqueo)

    def test_el_contador_del_panel_no_crece_en_queries_con_los_alumnos(self):
        """El contador va en el dashboard: es exactamente donde un N+1 se paga
        caro (este proyecto ya se comió un 502 por eso). Se comparan DOS
        tamaños de conjunto, y lo que crece es lo que de verdad multiplica el
        costo: alumnos CON cuota impaga que bloquea."""
        def sembrar(cantidad, desde):
            for i in range(cantidad):
                alumno = Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"A{desde + i}", apellido="X",
                    fecha_inicio_ciclo=date(2026, 7, 1),
                )
                crear_cuota(
                    gimnasio=self.gimnasio, alumno=alumno, inicio=date(2026, 7, 1)
                )

        sembrar(3, 0)
        with CaptureQueriesContext(connection) as chico:
            self.assertEqual(acceso.contar_bloqueados(self.gimnasio, hoy=self.hoy), 3)

        sembrar(30, 100)
        with CaptureQueriesContext(connection) as grande:
            self.assertEqual(acceso.contar_bloqueados(self.gimnasio, hoy=self.hoy), 33)

        self.assertEqual(len(grande), len(chico))


class BloqueoEnLasVistasDelAlumnoTests(TestCase):
    """Qué puede y qué no puede hacer un alumno bloqueado."""

    def setUp(self):
        self.hoy = timezone.localdate()
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio A", slug="gimnasio-a", dias_tolerancia_pago=0
        )
        # Ver la nota de `BloqueoPorFaltaDePagoTests`: la activación se
        # retrocede para que las cuotas del fixture entren en la ventana.
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            fecha_activacion_bloqueo=self.hoy - timedelta(days=60)
        )
        self.gimnasio.refresh_from_db()
        self.usuario = get_user_model().objects.create_user(
            "ana", password="clave-123456"
        )
        self.perfil = Perfil.objects.create(
            usuario=self.usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez",
            perfil=self.perfil, fecha_inicio_ciclo=self.hoy - timedelta(days=10),
        )
        self.cuota = crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=self.hoy - timedelta(days=10), monto=Decimal("15000.00"),
        )
        self.client.login(username="ana", password="clave-123456")

    def _rutina_con_un_dia(self):
        from ejercicios.models import Ejercicio
        from rutinas.models import RutinaAsignada, RutinaAsignadaItem

        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            nombre_snapshot="Plan", objetivo_snapshot="Fuerza",
            fecha_inicio=self.hoy - timedelta(days=3),
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina, ejercicio_nombre_snapshot="Sentadilla",
            semana=1, dia=1, orden=1, series=3, repeticiones="10",
        )
        return rutina

    def test_el_portal_reemplaza_la_rutina_por_el_aviso_de_bloqueo(self):
        self._rutina_con_un_dia()

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Tu acceso está pausado")
        self.assertNotContains(response, "/rutinas/mi-rutina/dia/1/")

    def test_el_detalle_del_dia_explica_el_bloqueo_y_no_da_404(self):
        """Un 404 le diría al alumno que la app se rompió. Esta URL queda en
        favoritos y en el historial del celular."""
        self._rutina_con_un_dia()

        response = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu acceso a la rutina está pausado")

    def test_sin_alias_cargado_el_detalle_del_dia_muestra_el_contacto(self):
        """REGRESIÓN. `bloqueado.html` leía `perfil.gimnasio`, que solo
        `HomeView` pone en el contexto: acá `gimnasio` quedaba vacío y, sin
        `MedioCobro` cargado, siempre salía el genérico «Consultá con el
        gimnasio cómo pagar» en vez del contacto real."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(contacto="11-5555-0000")
        self._rutina_con_un_dia()

        response = self.client.get(reverse("rutinas:mi_dia_detalle", args=[1]))

        self.assertContains(response, "11-5555-0000")
        self.assertNotContains(response, "Consultá con el gimnasio cómo pagar")


    def test_no_puede_calificar_ni_marcar_dia_entrenado(self):
        rutina = self._rutina_con_un_dia()
        item = rutina.items.first()

        self.assertEqual(
            self.client.post(
                reverse("rutinas:item_calificar", args=[item.pk]), {"rpe": "al_limite"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("rutinas:dia_completado_toggle", args=[1, 1])
            ).status_code,
            403,
        )

    def test_no_puede_reservar_turno(self):
        response = self.client.post(
            reverse("turnos:reservar"),
            {"fecha": (self.hoy + timedelta(days=1)).isoformat(), "hora_inicio": "10:00"},
        )
        self.assertEqual(response.status_code, 403)

    def test_igual_puede_subir_el_comprobante(self):
        """Bloquear el camino de pago sería encerrar al alumno."""
        response = self.client.get(
            reverse("pagos:comprobante_subir", args=[self.cuota.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_subir_el_comprobante_no_desbloquea(self):
        """Decisión de producto: solo el staff, confirmando el pago."""
        self.client.post(
            reverse("pagos:comprobante_subir", args=[self.cuota.pk]),
            {"comprobante": SimpleUploadedFile(
                "c.jpg", b"\xff\xd8\xff", content_type="image/jpeg"
            )},
        )

        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, Cuota.Estado.PENDIENTE)
        self.assertIsNotNone(acceso.bloqueo_de(self.alumno))

    def test_marcar_pagado_desbloquea(self):
        self.cuota.estado = Cuota.Estado.PAGADO
        self.cuota.save()

        self.assertIsNone(acceso.bloqueo_de(self.alumno))
        self._rutina_con_un_dia()
        self.assertContains(self.client.get(reverse("home")), "Tu rutina")


class AnclaDelCicloTests(TestCase):
    """De dónde sale `Alumno.fecha_inicio_ciclo`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

    def test_el_alta_estampa_el_ancla(self):
        """Sin ancla el alumno no recibe NINGUNA cuota: un agujero de
        facturación que no da ningún síntoma."""
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        self.assertEqual(alumno.fecha_inicio_ciclo, timezone.localdate())

    def test_la_primera_rutina_corrige_el_ancla(self):
        """Regla de producto: el alumno se puede dar de alta un lunes y
        empezar a entrenar el jueves."""
        from rutinas.models import RutinaAsignada

        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez"
        )
        arranque = timezone.localdate() - timedelta(days=2)
        RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, nombre_snapshot="P",
            objetivo_snapshot="O", fecha_inicio=arranque,
        )

        alumno.refresh_from_db()
        self.assertEqual(alumno.fecha_inicio_ciclo, arranque)

    def test_la_segunda_rutina_no_mueve_el_ancla_si_ya_hubo_cuotas(self):
        """Mover el ancla con cuotas emitidas solapa períodos y vuelve a
        cobrar días ya cobrados -- y el `unique_together` no lo impide, porque
        solo prohíbe dos cuotas con el MISMO arranque."""
        from rutinas.models import RutinaAsignada

        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez",
            fecha_inicio_ciclo=date(2026, 5, 1),
        )
        crear_cuota(gimnasio=self.gimnasio, alumno=alumno, inicio=date(2026, 5, 1))

        RutinaAsignada.objects.create(
            gimnasio=self.gimnasio, alumno=alumno, nombre_snapshot="P",
            objetivo_snapshot="O", fecha_inicio=date(2026, 6, 10),
        )

        alumno.refresh_from_db()
        self.assertEqual(alumno.fecha_inicio_ciclo, date(2026, 5, 1))

    def test_reactivar_reancla_el_ciclo_a_hoy(self):
        """REGRESIÓN. Durante la baja no se emiten cuotas pero el ancla sigue
        avanzando sola: sin re-anclar, el que vuelve después de tres meses
        recibe esa noche una cuota cuyo período transcurrió casi entero
        mientras estaba de baja, el cron la vence en la misma corrida y queda
        sin rutina ni turnos el día 1 de su regreso."""
        hoy = timezone.localdate()
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez",
            fecha_inicio_ciclo=hoy - timedelta(days=200),
        )
        alumno.estado = Alumno.Estado.INACTIVO
        alumno.save()

        alumno.estado = Alumno.Estado.ACTIVO
        alumno.save()

        alumno.refresh_from_db()
        self.assertEqual(alumno.fecha_inicio_ciclo, hoy)

    def test_una_baja_y_alta_el_mismo_dia_no_regala_dias(self):
        """Re-anclar siempre le regalaría hasta 27 días de cuota al que se dio
        de baja por error y volvió en el acto."""
        hoy = timezone.localdate()
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gomez",
            fecha_inicio_ciclo=hoy,
        )
        alumno.estado = Alumno.Estado.INACTIVO
        alumno.save()
        alumno.estado = Alumno.Estado.ACTIVO
        alumno.save()

        alumno.refresh_from_db()
        self.assertEqual(alumno.fecha_inicio_ciclo, hoy)


class TransicionDesdeElMesCalendarioTests(TestCase):
    """Que el régimen de 28 días arranque SIN pisar lo ya facturado.

    Es la garantía que más costó del plan: la versión intuitiva (anclar en la
    primera rutina del alumno) producía siempre un ciclo solapado con la cuota
    calendario del mes en curso, que el gimnasio probablemente ya cobró.
    """

    def test_el_primer_ciclo_no_se_solapa_con_la_ultima_cuota_mensual(self):
        hoy = date(2026, 9, 3)
        gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        alumno = Alumno.objects.create(
            gimnasio=gimnasio, nombre="Ana", apellido="Gomez"
        )
        # Histórico tal como lo deja el backfill: meses calendario completos.
        for mes in (7, 8, 9):
            crear_cuota_mensual(
                gimnasio=gimnasio, alumno=alumno, mes=mes, anio=2026,
                estado=Cuota.Estado.PAGADO,
            )
        ultimo_fin = max(
            Cuota.objects.filter(alumno=alumno).values_list("periodo_fin", flat=True)
        )
        # Y el ancla que calcula la migración: el primer día no cubierto.
        Alumno.objects.filter(pk=alumno.pk).update(
            fecha_inicio_ciclo=ultimo_fin + timedelta(days=1)
        )

        antes = Cuota.objects.filter(alumno=alumno).count()
        generar_pagos_pendientes(hoy)

        # Estamos a mitad de septiembre-calendario, así que el ancla (1/10)
        # todavía no llegó: no se emite nada y no hay cuota duplicada.
        self.assertEqual(Cuota.objects.filter(alumno=alumno).count(), antes)

        # Y cuando llega, arranca justo donde terminó lo ya cobrado.
        generar_pagos_pendientes(ultimo_fin + timedelta(days=1))
        nueva = Cuota.objects.filter(alumno=alumno).order_by("-periodo_inicio").first()
        self.assertEqual(nueva.periodo_inicio, ultimo_fin + timedelta(days=1))

    def test_ningun_periodo_nuevo_se_solapa_con_uno_historico(self):
        hoy = date(2026, 9, 3)
        gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        alumno = Alumno.objects.create(
            gimnasio=gimnasio, nombre="Ana", apellido="Gomez"
        )
        crear_cuota_mensual(gimnasio=gimnasio, alumno=alumno, mes=9, anio=2026)
        Alumno.objects.filter(pk=alumno.pk).update(
            fecha_inicio_ciclo=date(2026, 10, 1)
        )

        for dia in range(1, 60):
            generar_pagos_pendientes(hoy + timedelta(days=dia))

        periodos = sorted(
            Cuota.objects.filter(alumno=alumno).values_list(
                "periodo_inicio", "periodo_fin"
            )
        )
        for (_, fin_previo), (inicio, _) in zip(periodos, periodos[1:]):
            self.assertLess(fin_previo, inicio, f"se solapan: {periodos}")
