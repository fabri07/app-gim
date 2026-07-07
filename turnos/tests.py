"""
Tests de Fase 6 para `turnos`: modelos (creación + `__str__` de cada uno,
idempotencia de `obtener_configuracion`, restricciones de unicidad, el
`CheckConstraint` de `HorarioAtencion` y el aislamiento por gimnasio heredado
de `TenantQuerySet.for_gimnasio`, patrón `novedades/tests.py`) y los
servicios de dominio de `turnos/services.py` (corte de horarios en franjas,
cupos, alta/baja de reservas y reconciliación de reservas desencajadas).

Los tests de `crear_reserva`/`cancelar_reserva`/`reconciliar_reservas_desencajadas`
anclan sus fechas/horas a `timezone.localtime()` (nunca a fechas hardcodeadas)
para no pudrirse con el paso del tiempo. En SQLite (backend de test)
`select_for_update()` no toma un lock real -- Django lo ejecuta como un SELECT
normal dentro de la transacción (no hay locking entre conexiones porque los
tests corren en un único hilo/conexión) -- así que no hace falta simular
concurrencia real acá; en Postgres (producción) sí aplica el lock.
"""

from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from novedades.models import Novedad
from tenants.models import Gimnasio, Perfil
from turnos.models import (
    ConfiguracionTurnos,
    CupoExcepcion,
    DiaSemana,
    HorarioAtencion,
    Reserva,
    obtener_configuracion,
)
from turnos.services import (
    CIERRE_RESERVA,
    Franja,
    ReservaDuplicada,
    TurnoCerrado,
    TurnoInexistente,
    TurnoLleno,
    cancelar_reserva,
    crear_reserva,
    es_franja_vigente,
    franjas_de_rango,
    franjas_del_dia,
    grilla_semanal,
    reconciliar_reservas_desencajadas,
    reservas_por_franja,
    url_google_calendar,
    vacantes_de_franja,
)


class ConfiguracionTurnosModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_y_str(self):
        config = ConfiguracionTurnos.objects.create(gimnasio=self.gimnasio)

        self.assertEqual(
            str(config), f"Configuración de turnos de {self.gimnasio}"
        )
        self.assertEqual(config.duracion_minutos, 60)
        self.assertEqual(config.vacantes_default, 10)

    def test_unicidad_por_gimnasio(self):
        ConfiguracionTurnos.objects.create(gimnasio=self.gimnasio)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConfiguracionTurnos.objects.create(gimnasio=self.gimnasio)


class ObtenerConfiguracionTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_crea_con_defaults_la_primera_vez(self):
        config = obtener_configuracion(self.gimnasio)

        self.assertEqual(config.duracion_minutos, 60)
        self.assertEqual(config.vacantes_default, 10)
        self.assertEqual(ConfiguracionTurnos.objects.count(), 1)

    def test_segunda_llamada_devuelve_la_misma_fila(self):
        primera = obtener_configuracion(self.gimnasio)
        segunda = obtener_configuracion(self.gimnasio)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(ConfiguracionTurnos.objects.count(), 1)


class HorarioAtencionModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_y_str(self):
        horario = HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )

        self.assertEqual(str(horario), "Lunes 08:00:00-12:00:00")

    def test_check_constraint_hora_desde_debe_ser_menor_a_hora_hasta(self):
        """`hora_desde == hora_hasta` debe violar el CheckConstraint. En SQLite
        (backend de test) Django enforce los CheckConstraint al hacer el
        INSERT; si algún backend no lo soportara, este test documentaría el
        intento sin bloquear la tarea (no es lógica de negocio, es detalle de
        backend)."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HorarioAtencion.objects.create(
                    gimnasio=self.gimnasio,
                    dia_semana=DiaSemana.LUNES,
                    hora_desde=time(8, 0),
                    hora_hasta=time(8, 0),
                )


class CupoExcepcionModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_y_str(self):
        excepcion = CupoExcepcion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.MARTES,
            hora_inicio=time(18, 0),
            vacantes=0,
        )

        self.assertEqual(str(excepcion), "Martes 18:00:00 -> 0")


class ReservaModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )

    def test_creacion_y_str(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=date(2026, 7, 6),
            hora_inicio=time(9, 0),
        )

        self.assertEqual(str(reserva), "Gómez, Ana - 2026-07-06 09:00:00")

    def test_unicidad_por_gimnasio_alumno_fecha_hora(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=date(2026, 7, 6),
            hora_inicio=time(9, 0),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Reserva.objects.create(
                    gimnasio=self.gimnasio,
                    alumno=self.alumno,
                    fecha=date(2026, 7, 6),
                    hora_inicio=time(9, 0),
                )

    def test_full_clean_rechaza_alumno_de_otro_gimnasio(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        alumno_de_otro = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Bruno", apellido="Pérez"
        )
        reserva = Reserva(
            gimnasio=self.gimnasio,
            alumno=alumno_de_otro,
            fecha=date(2026, 7, 6),
            hora_inicio=time(9, 0),
        )

        with self.assertRaises(ValidationError):
            reserva.full_clean()


class TurnosTenantIsolationTests(TestCase):
    """Confirma que `for_gimnasio()` aísla horarios y reservas entre gimnasios,
    patrón `novedades/tests.py::NovedadTenantIsolationTests`."""

    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

    def test_horario_atencion_aislado_por_gimnasio(self):
        horario_a = HorarioAtencion.objects.create(
            gimnasio=self.gimnasio_a,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio_b,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )

        horarios_de_a = HorarioAtencion.objects.for_gimnasio(self.gimnasio_a)

        self.assertEqual(list(horarios_de_a), [horario_a])

    def test_reserva_aislada_por_gimnasio(self):
        alumno_a = Alumno.objects.create(
            gimnasio=self.gimnasio_a, nombre="Ana", apellido="Gómez"
        )
        alumno_b = Alumno.objects.create(
            gimnasio=self.gimnasio_b, nombre="Bruno", apellido="Pérez"
        )
        reserva_a = Reserva.objects.create(
            gimnasio=self.gimnasio_a,
            alumno=alumno_a,
            fecha=date(2026, 7, 6),
            hora_inicio=time(9, 0),
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio_b,
            alumno=alumno_b,
            fecha=date(2026, 7, 6),
            hora_inicio=time(9, 0),
        )

        reservas_de_a = Reserva.objects.for_gimnasio(self.gimnasio_a)

        self.assertEqual(list(reservas_de_a), [reserva_a])


# ---------------------------------------------------------------------------
# turnos/services.py
# ---------------------------------------------------------------------------


class FranjasDeRangoTests(TestCase):
    """Función pura, sin DB: corte de un rango horario en franjas."""

    def test_60_minutos_en_8_a_12_da_cuatro_franjas_exactas(self):
        franjas = franjas_de_rango(time(8, 0), time(12, 0), 60)

        self.assertEqual(
            franjas,
            [
                (time(8, 0), time(9, 0)),
                (time(9, 0), time(10, 0)),
                (time(10, 0), time(11, 0)),
                (time(11, 0), time(12, 0)),
            ],
        )

    def test_90_minutos_en_8_a_12_30_incluye_la_franja_que_calza_justo(self):
        """11:00 + 90' = 12:30 == hora_hasta -> SÍ entra (regla `hora_fin <=
        hora_hasta`, no `<`)."""
        franjas = franjas_de_rango(time(8, 0), time(12, 30), 90)

        self.assertEqual(
            franjas,
            [
                (time(8, 0), time(9, 30)),
                (time(9, 30), time(11, 0)),
                (time(11, 0), time(12, 30)),
            ],
        )

    def test_rango_que_no_da_para_ninguna_franja_completa_da_lista_vacia(self):
        franjas = franjas_de_rango(time(8, 0), time(8, 30), 60)

        self.assertEqual(franjas, [])


class FranjasDelDiaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )

    def test_combina_las_franjas_de_dos_rangos_del_mismo_dia(self):
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(16, 0),
            hora_hasta=time(21, 0),
        )

        horas_inicio = [
            inicio for inicio, _ in franjas_del_dia(self.gimnasio, DiaSemana.LUNES)
        ]

        self.assertEqual(
            horas_inicio,
            [
                time(8, 0),
                time(9, 0),
                time(10, 0),
                time(11, 0),
                time(16, 0),
                time(17, 0),
                time(18, 0),
                time(19, 0),
                time(20, 0),
            ],
        )

    def test_dia_sin_horario_atencion_da_lista_vacia(self):
        self.assertEqual(franjas_del_dia(self.gimnasio, DiaSemana.DOMINGO), [])

    def test_rangos_solapados_no_duplican_horas(self):
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.MARTES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.MARTES,
            hora_desde=time(10, 0),
            hora_hasta=time(14, 0),
        )

        horas_inicio = [
            inicio for inicio, _ in franjas_del_dia(self.gimnasio, DiaSemana.MARTES)
        ]

        self.assertEqual(
            horas_inicio,
            [
                time(8, 0),
                time(9, 0),
                time(10, 0),
                time(11, 0),
                time(12, 0),
                time(13, 0),
            ],
        )


class VacantesDeFranjaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_sin_excepcion_devuelve_el_default(self):
        vacantes = vacantes_de_franja(
            self.gimnasio, DiaSemana.LUNES, time(9, 0), default=8
        )

        self.assertEqual(vacantes, 8)

    def test_con_excepcion_devuelve_su_valor_incluido_cero(self):
        CupoExcepcion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_inicio=time(9, 0),
            vacantes=0,
        )

        vacantes = vacantes_de_franja(
            self.gimnasio, DiaSemana.LUNES, time(9, 0), default=8
        )

        self.assertEqual(vacantes, 0)

    def test_con_excepcion_no_nula_devuelve_su_valor(self):
        CupoExcepcion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.MIERCOLES,
            hora_inicio=time(18, 0),
            vacantes=3,
        )

        vacantes = vacantes_de_franja(
            self.gimnasio, DiaSemana.MIERCOLES, time(18, 0), default=8
        )

        self.assertEqual(vacantes, 3)


class EsFranjaVigenteTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )

    @staticmethod
    def _proximo(dia_semana):
        """Próxima fecha (a partir de una fecha ancla cualquiera) que caiga
        en `dia_semana`, sin tener que verificar a mano qué día de la semana
        es una fecha hardcodeada."""
        ancla = date(2026, 8, 10)
        return ancla + timedelta(days=(dia_semana - ancla.weekday()) % 7)

    def test_true_para_una_franja_generada(self):
        fecha = self._proximo(DiaSemana.LUNES)

        self.assertTrue(es_franja_vigente(self.gimnasio, fecha, time(9, 0)))

    def test_false_para_una_hora_que_no_cae_en_la_grilla(self):
        fecha = self._proximo(DiaSemana.LUNES)

        self.assertFalse(es_franja_vigente(self.gimnasio, fecha, time(9, 30)))

    def test_false_para_un_dia_sin_horario_atencion(self):
        fecha = self._proximo(DiaSemana.DOMINGO)

        self.assertFalse(es_franja_vigente(self.gimnasio, fecha, time(9, 0)))


class FranjaTests(TestCase):
    """Las propiedades de `Franja` no dependen de la DB, pero se agrupan acá
    (TestCase) por consistencia con el resto del módulo."""

    def test_llena_cuando_ocupadas_alcanza_las_vacantes(self):
        franja = Franja(
            fecha=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(9, 0),
            hora_fin=time(10, 0),
            vacantes=2,
            ocupadas=2,
        )

        self.assertTrue(franja.llena)

    def test_no_llena_cuando_ocupadas_es_menor_a_vacantes(self):
        franja = Franja(
            fecha=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(9, 0),
            hora_fin=time(10, 0),
            vacantes=2,
            ocupadas=1,
        )

        self.assertFalse(franja.llena)

    def test_pasada_true_para_una_franja_de_ayer(self):
        franja = Franja(
            fecha=timezone.localdate() - timedelta(days=1),
            hora_inicio=time(9, 0),
            hora_fin=time(10, 0),
            vacantes=2,
            ocupadas=0,
        )

        self.assertTrue(franja.pasada)
        self.assertFalse(franja.reservable)

    def test_reservable_false_si_falta_menos_de_cierre_reserva(self):
        ahora = timezone.localtime()
        dentro_de_30_min = ahora + timedelta(minutes=30)
        franja = Franja(
            fecha=dentro_de_30_min.date(),
            hora_inicio=dentro_de_30_min.time().replace(second=0, microsecond=0),
            hora_fin=time(23, 59),
            vacantes=2,
            ocupadas=0,
        )

        self.assertFalse(franja.pasada)
        self.assertFalse(franja.reservable)

    def test_reservable_true_si_falta_mas_de_cierre_reserva_y_no_esta_llena(self):
        ahora = timezone.localtime()
        dentro_de_dos_horas = ahora + timedelta(hours=2)
        franja = Franja(
            fecha=dentro_de_dos_horas.date(),
            hora_inicio=dentro_de_dos_horas.time().replace(second=0, microsecond=0),
            hora_fin=time(23, 59),
            vacantes=2,
            ocupadas=0,
        )

        self.assertTrue(franja.reservable)


class CrearReservaTests(TestCase):
    """Las franjas se generan en grilla de 15' cubriendo casi todo el día
    (00:00-23:45, los 7 días de la semana) para poder anclar los casos de
    borde de `CIERRE_RESERVA` con precisión relativa a
    `timezone.localtime()`, sin hardcodear fechas absolutas."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        self.config = ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=15, vacantes_default=1
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 45),
            )

        ahora = timezone.localtime()

        # Franja cómodamente futura: +2 días siempre es futuro, sin importar
        # a qué hora corra el test.
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.hora_futura = time(10, 0)

        # Franja a menos de 1h de "ahora" pero todavía sin empezar: se ancla
        # al grid de 15' de la config (2 franjas de 15' = 30' de margen,
        # menos lo que ya pasó del cuarto de hora actual) para garantizar que
        # sea una franja válida. Se corrige el único hueco que el modelo
        # nunca genera: los últimos 15' del día (`hora_hasta=23:45` arriba).
        base_15 = ahora.replace(
            minute=(ahora.minute // 15) * 15, second=0, microsecond=0
        )
        casi = base_15 + timedelta(minutes=30)
        if casi.time() == time(23, 45):
            casi -= timedelta(minutes=15)
        self.fecha_cerrada = casi.date()
        self.hora_cerrada = casi.time()

    def test_caso_feliz_crea_la_reserva(self):
        reserva = crear_reserva(
            self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura
        )

        self.assertIsNotNone(reserva.pk)
        self.assertEqual(reserva.gimnasio, self.gimnasio)
        self.assertEqual(reserva.alumno, self.alumno)
        self.assertEqual(reserva.fecha, self.fecha_futura)
        self.assertEqual(reserva.hora_inicio, self.hora_futura)

    def test_turno_inexistente_si_la_hora_no_es_una_franja(self):
        # 10:07 nunca es franja: la grilla siempre cae en :00/:15/:30/:45.
        with self.assertRaises(TurnoInexistente):
            crear_reserva(self.gimnasio, self.alumno, self.fecha_futura, time(10, 7))

    def test_turno_cerrado_si_falta_menos_de_una_hora(self):
        with self.assertRaises(TurnoCerrado):
            crear_reserva(
                self.gimnasio, self.alumno, self.fecha_cerrada, self.hora_cerrada
            )

    def test_turno_cerrado_si_ya_paso(self):
        ahora = timezone.localtime()
        fecha_pasada = (ahora - timedelta(days=2)).date()

        with self.assertRaises(TurnoCerrado):
            crear_reserva(self.gimnasio, self.alumno, fecha_pasada, time(10, 0))

    def test_permite_reservar_exactamente_una_hora_antes_del_inicio(self):
        """Caso borde decidido: a las 17:00 se puede reservar el turno de las
        18:00 (falta EXACTAMENTE `CIERRE_RESERVA`, el cierre es a `< 1h`, no
        a `<= 1h`)."""
        inicio = datetime.combine(self.fecha_futura, self.hora_futura)
        ahora_exacta = inicio - CIERRE_RESERVA

        with patch("turnos.services.timezone.localtime", return_value=ahora_exacta):
            reserva = crear_reserva(
                self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura
            )

        self.assertIsNotNone(reserva.pk)

    def test_un_segundo_despues_del_cierre_ya_no_se_puede_reservar(self):
        inicio = datetime.combine(self.fecha_futura, self.hora_futura)
        ahora_tarde = inicio - CIERRE_RESERVA + timedelta(seconds=1)

        with patch("turnos.services.timezone.localtime", return_value=ahora_tarde):
            with self.assertRaises(TurnoCerrado):
                crear_reserva(
                    self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura
                )

    def test_turno_lleno_cuando_se_alcanza_el_cupo(self):
        # vacantes_default=1 en el setUp.
        crear_reserva(self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura)

        with self.assertRaises(TurnoLleno):
            crear_reserva(
                self.gimnasio,
                self.otro_alumno,
                self.fecha_futura,
                self.hora_futura,
            )

    def test_no_cuenta_reservas_de_otro_gimnasio_para_el_cupo(self):
        alumno_de_otro_gym = Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Carla", apellido="Ruiz"
        )
        Reserva.objects.create(
            gimnasio=self.otro_gimnasio,
            alumno=alumno_de_otro_gym,
            fecha=self.fecha_futura,
            hora_inicio=self.hora_futura,
        )

        # vacantes_default=1 en self.gimnasio; la reserva de "otro_gimnasio"
        # no debe contar para su cupo.
        reserva = crear_reserva(
            self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura
        )

        self.assertIsNotNone(reserva.pk)

    def test_reserva_duplicada_en_el_segundo_intento(self):
        crear_reserva(self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura)

        with self.assertRaises(ReservaDuplicada):
            crear_reserva(
                self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura
            )


class CrearReservaSinConfiguracionPreviaTests(TestCase):
    """Reproduce el caso donde el gimnasio tiene horarios cargados pero
    todavía nunca se generó su fila de `ConfiguracionTurnos` (p.ej. se
    cargaron horarios por otra vía sin pasar antes por
    `obtener_configuracion`/la vista de config)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Sin Config", slug="gimnasio-sin-config"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        # A propósito NO se crea ConfiguracionTurnos acá.

    def test_crea_la_reserva_aunque_no_exista_configuracion_previa(self):
        self.assertEqual(ConfiguracionTurnos.objects.filter(gimnasio=self.gimnasio).count(), 0)
        fecha_futura = (timezone.localdate() + timedelta(days=2))

        reserva = crear_reserva(self.gimnasio, self.alumno, fecha_futura, time(10, 0))

        self.assertIsNotNone(reserva.pk)
        self.assertEqual(
            ConfiguracionTurnos.objects.filter(gimnasio=self.gimnasio).count(), 1
        )


class CancelarReservaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.ahora = timezone.localtime()

    def test_borra_si_la_franja_todavia_no_empezo(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=(self.ahora + timedelta(days=1)).date(),
            hora_inicio=time(10, 0),
        )

        cancelar_reserva(reserva)

        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_levanta_turno_cerrado_y_no_borra_si_ya_paso(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=(self.ahora - timedelta(days=1)).date(),
            hora_inicio=time(10, 0),
        )

        with self.assertRaises(TurnoCerrado):
            cancelar_reserva(reserva)

        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())


class ReconciliarReservasDesencajadasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.config = ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.fecha_pasada = (ahora - timedelta(days=2)).date()

    def test_migra_a_la_franja_mas_cercana_con_lugar(self):
        # 10:00 es franja válida con duración=60; deja de serlo con 45 (600'
        # no es múltiplo de 45'). 9:45 (585') sí lo es y queda a 15' de
        # distancia (vs. 30' de 10:30) -- se reprograma ahí.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        self.assertEqual(resultado.canceladas, 0)
        reserva = Reserva.objects.get(gimnasio=self.gimnasio, fecha=self.fecha_futura)
        self.assertEqual(reserva.hora_inicio, time(9, 45))

    def test_cancela_si_no_queda_ninguna_franja_ese_dia(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancela_si_la_franja_mas_cercana_ya_esta_llena(self):
        self.config.vacantes_default = 1
        self.config.save()
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=otro_alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(9, 45),
        )
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancela_si_el_alumno_ya_tiene_otra_reserva_en_la_franja_mas_cercana(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(9, 45),
        )
        reserva_desencajada = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva_desencajada.pk).exists())
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                fecha=self.fecha_futura,
                hora_inicio=time(9, 45),
            ).exists()
        )

    def test_ante_empate_de_distancia_elige_la_franja_mas_temprana(self):
        # Bajo duracion_minutos=60 (config de setUp), 10:30 nunca es una
        # franja válida (630' no es múltiplo de 60') -- queda exactamente a
        # mitad de camino entre 10:00 (600') y 11:00 (660'), ambas a 30' de
        # distancia.
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 30),
        )

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        reserva.refresh_from_db()
        self.assertEqual(reserva.hora_inicio, time(10, 0))

    def test_no_toca_reserva_pasada_aunque_quede_desencajada(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_pasada, hora_inicio=time(10, 0)
            ).exists()
        )

    def test_no_toca_reserva_futura_que_sigue_encajando(self):
        # 03:00 = 180', que sigue siendo múltiplo de 45 -> sigue siendo franja.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(3, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(3, 0)
            ).exists()
        )

    def test_reducir_el_cupo_no_toca_ninguna_reserva(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.vacantes_default = 1
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(10, 0)
            ).exists()
        )


class ReconciliacionGeneraNovedadesTests(TestCase):
    """Parte B: cuando la reconciliación migra o cancela la reserva de un
    alumno CON login, le genera una `Novedad` personal. Alumno sin `Perfil`
    (sin portal donde verla) no genera nada, pero la reserva se migra/cancela
    igual. Mismo escenario base que `ReconciliarReservasDesencajadasTests`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.perfil = Perfil.objects.create(
            usuario=User.objects.create_user("alumno-1", password="clave-123456"),
            gimnasio=self.gimnasio,
            rol=Perfil.Rol.ALUMNO,
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez", perfil=self.perfil
        )
        self.config = ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        self.fecha_futura = (timezone.localtime() + timedelta(days=2)).date()

    def _reserva_10hs(self):
        return Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

    def test_migrar_genera_novedad_personal(self):
        self._reserva_10hs()
        self.config.duracion_minutos = 45  # 10:00 deja de encajar; migra a 09:45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        novedad = Novedad.objects.get(alumno=self.alumno)
        self.assertEqual(novedad.gimnasio, self.gimnasio)
        self.assertEqual(novedad.titulo, "Cambió el horario de tu turno")
        self.assertIn("10:00", novedad.mensaje)
        self.assertIn("09:45", novedad.mensaje)
        self.assertEqual(novedad.visible_hasta, self.fecha_futura)
        self.assertTrue(novedad.activa)

    def test_cancelar_genera_novedad_personal(self):
        self._reserva_10hs()
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()  # sin franjas ese día -> se cancela

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.canceladas, 1)
        novedad = Novedad.objects.get(alumno=self.alumno)
        self.assertEqual(novedad.titulo, "Se canceló uno de tus turnos")
        self.assertIn("10:00", novedad.mensaje)
        self.assertEqual(novedad.visible_hasta, self.fecha_futura)

    def test_alumno_sin_perfil_no_genera_novedad_pero_migra_igual(self):
        alumno_sin_login = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Sin Login"
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno_sin_login,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        self.assertFalse(Novedad.objects.filter(alumno=alumno_sin_login).exists())

    def test_reserva_que_sigue_encajando_no_genera_novedad(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(3, 0),  # 180' sigue siendo múltiplo de 45
        )
        self.config.duracion_minutos = 45
        self.config.save()

        reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertFalse(Novedad.objects.filter(alumno=self.alumno).exists())

    def test_eventos_expone_detalle_por_reserva(self):
        self._reserva_10hs()
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(len(resultado.eventos), 1)
        evento = resultado.eventos[0]
        self.assertEqual(evento.alumno, self.alumno)
        self.assertEqual(evento.fecha, self.fecha_futura)
        self.assertEqual(evento.hora_original, time(10, 0))
        self.assertEqual(evento.hora_nueva, time(9, 45))


class UrlGoogleCalendarTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        self.reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=date(2026, 8, 10),
            hora_inicio=time(9, 0),
        )

    def test_contiene_action_template_fechas_y_timezone(self):
        url = url_google_calendar(self.reserva, self.gimnasio)
        _, _, query = url.partition("?")
        params = parse_qs(query)

        self.assertTrue(
            url.startswith("https://calendar.google.com/calendar/render")
        )
        self.assertEqual(params["action"], ["TEMPLATE"])
        self.assertEqual(params["dates"], ["20260810T090000/20260810T100000"])
        self.assertEqual(params["ctz"], ["America/Argentina/Buenos_Aires"])


class GrillaSemanalTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=2
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(9, 0),
                hora_hasta=time(11, 0),
            )
        self.desde = timezone.localdate()

    def test_arma_franjas_por_fecha_con_ocupadas_y_reservada_por_mi(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.desde,
            hora_inicio=time(9, 0),
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.otro_alumno,
            fecha=self.desde,
            hora_inicio=time(9, 0),
        )

        grilla = grilla_semanal(self.gimnasio, self.desde, dias=3, alumno=self.alumno)

        self.assertEqual(
            set(grilla.keys()),
            {
                self.desde,
                self.desde + timedelta(days=1),
                self.desde + timedelta(days=2),
            },
        )
        franja_9 = next(f for f in grilla[self.desde] if f.hora_inicio == time(9, 0))
        self.assertEqual(franja_9.ocupadas, 2)
        self.assertEqual(franja_9.vacantes, 2)
        self.assertTrue(franja_9.llena)
        self.assertTrue(franja_9.reservada_por_mi)

        franja_10 = next(f for f in grilla[self.desde] if f.hora_inicio == time(10, 0))
        self.assertEqual(franja_10.ocupadas, 0)
        self.assertFalse(franja_10.reservada_por_mi)

    def test_cantidad_de_queries_no_escala_con_los_dias(self):
        """`grilla_semanal` debe precargar horarios/excepciones en bloque en
        vez de re-consultarlos por día/franja -- la cantidad de queries no
        puede depender de `dias`."""
        CupoExcepcion.objects.create(
            gimnasio=self.gimnasio, dia_semana=0, hora_inicio=time(9, 0), vacantes=5
        )

        with CaptureQueriesContext(connection) as pocos_dias:
            grilla_semanal(self.gimnasio, self.desde, dias=3, alumno=self.alumno)
        with CaptureQueriesContext(connection) as muchos_dias:
            grilla_semanal(self.gimnasio, self.desde, dias=14, alumno=self.alumno)

        self.assertEqual(
            len(pocos_dias.captured_queries), len(muchos_dias.captured_queries)
        )


class ReservasPorFranjaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )

    def test_agrupa_las_reservas_por_fecha_y_hora(self):
        fecha = timezone.localdate() + timedelta(days=1)
        reserva_1 = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=fecha,
            hora_inicio=time(9, 0),
        )
        reserva_2 = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.otro_alumno,
            fecha=fecha,
            hora_inicio=time(9, 0),
        )

        agrupadas = reservas_por_franja(self.gimnasio, fecha, fecha)

        self.assertEqual(
            set(agrupadas[(fecha, time(9, 0))]), {reserva_1, reserva_2}
        )


# ---------------------------------------------------------------------------
# turnos/views.py (Task 4: configuración de staff)
# ---------------------------------------------------------------------------


class TurnosViewsAccesoTests(TestCase):
    """Anónimo -> redirect a login; alumno -> 403, en las 5 rutas de gestión
    nuevas (patrón `novedades/tests.py::NovedadViewsAccesoTests`, extendido a
    todas las rutas del brief)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.horario = HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        self.excepcion = CupoExcepcion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_inicio=time(8, 0),
            vacantes=3,
        )

    def _urls(self):
        return [
            reverse("turnos:configuracion"),
            reverse("turnos:horario_crear"),
            reverse("turnos:horario_eliminar", args=[self.horario.pk]),
            reverse("turnos:cupo_crear"),
            reverse("turnos:cupo_eliminar", args=[self.excepcion.pk]),
        ]

    def test_anonimo_es_redirigido_a_login_en_todas_las_rutas(self):
        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403_en_todas_las_rutas(self):
        user = User.objects.create_user("alumno-1", password="clave-123456")
        Perfil.objects.create(
            usuario=user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alumno-1", password="clave-123456")

        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)


class ConfiguracionTurnosViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

    def test_get_crea_la_fila_de_configuracion_si_no_existia(self):
        self.assertEqual(ConfiguracionTurnos.objects.count(), 0)

        response = self.client.get(reverse("turnos:configuracion"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConfiguracionTurnos.objects.count(), 1)
        config = response.context["form"].instance
        self.assertEqual(config.duracion_minutos, 60)
        self.assertEqual(config.vacantes_default, 10)

    def test_post_cambia_duracion_y_vacantes_default(self):
        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 15},
        )

        self.assertRedirects(response, reverse("turnos:configuracion"))
        config = obtener_configuracion(self.gimnasio)
        self.assertEqual(config.duracion_minutos, 45)
        self.assertEqual(config.vacantes_default, 15)


class HorarioAtencionCreateViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

    def test_crear_horario_valido(self):
        response = self.client.post(
            reverse("turnos:horario_crear"),
            {
                "dia_semana": DiaSemana.LUNES,
                "hora_desde": "08:00",
                "hora_hasta": "12:00",
            },
        )

        self.assertRedirects(response, reverse("turnos:configuracion"))
        self.assertEqual(
            HorarioAtencion.objects.for_gimnasio(self.gimnasio).count(), 1
        )

    def test_hora_desde_mayor_o_igual_a_hora_hasta_no_crea_fila(self):
        response = self.client.post(
            reverse("turnos:horario_crear"),
            {
                "dia_semana": DiaSemana.LUNES,
                "hora_desde": "12:00",
                "hora_hasta": "12:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "El horario de inicio debe ser anterior al de cierre."
        )
        self.assertEqual(HorarioAtencion.objects.count(), 0)

    def test_horario_solapado_con_uno_existente_no_crea_fila(self):
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )

        response = self.client.post(
            reverse("turnos:horario_crear"),
            {
                "dia_semana": DiaSemana.LUNES,
                "hora_desde": "10:00",
                "hora_hasta": "14:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Ya existe un horario que se superpone ese día."
        )
        self.assertEqual(
            HorarioAtencion.objects.for_gimnasio(self.gimnasio).count(), 1
        )


class HorarioAtencionEliminarViewTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        self.staff_a = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )
        self.horario_a = HorarioAtencion.objects.create(
            gimnasio=self.gimnasio_a,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        self.horario_b = HorarioAtencion.objects.create(
            gimnasio=self.gimnasio_b,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        self.client.login(username="staff-a", password="clave-123456")

    def test_eliminar_horario_propio_lo_borra(self):
        response = self.client.post(
            reverse("turnos:horario_eliminar", args=[self.horario_a.pk])
        )

        self.assertRedirects(response, reverse("turnos:configuracion"))
        self.assertFalse(
            HorarioAtencion.objects.filter(pk=self.horario_a.pk).exists()
        )

    def test_eliminar_horario_de_otro_gimnasio_da_404_y_no_borra(self):
        response = self.client.post(
            reverse("turnos:horario_eliminar", args=[self.horario_b.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            HorarioAtencion.objects.filter(pk=self.horario_b.pk).exists()
        )


class CupoExcepcionCreateViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        HorarioAtencion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_desde=time(8, 0),
            hora_hasta=time(12, 0),
        )
        self.client.login(username="staff-1", password="clave-123456")

    def test_crear_excepcion_con_hora_que_es_franja_valida(self):
        response = self.client.post(
            reverse("turnos:cupo_crear"),
            {"dia_semana": DiaSemana.LUNES, "hora_inicio": "09:00", "vacantes": 3},
        )

        self.assertRedirects(response, reverse("turnos:configuracion"))
        self.assertEqual(
            CupoExcepcion.objects.for_gimnasio(self.gimnasio).count(), 1
        )

    def test_hora_que_no_es_franja_no_crea_fila(self):
        response = self.client.post(
            reverse("turnos:cupo_crear"),
            {"dia_semana": DiaSemana.LUNES, "hora_inicio": "09:30", "vacantes": 3},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Ese horario no coincide con ninguna franja de turnos de ese día.",
        )
        self.assertEqual(CupoExcepcion.objects.count(), 0)

    def test_excepcion_duplicada_no_rompe_con_integrity_error(self):
        """Regression: sin este chequeo, `gimnasio` no siendo un campo del
        form hace que Django se salte la validación automática de
        `unique_together` -- una segunda excepción para el mismo
        (gimnasio, dia_semana, hora_inicio) llegaba cruda al INSERT y volaba
        con `IntegrityError` (500), en vez de re-renderizar el form con un
        error prolijo. No hay vista de edición: el flujo esperado para
        "cambiar" una excepción es borrar y recrear, así que este caso (el
        staff se olvida de borrar la vieja primero) es un camino realista."""
        CupoExcepcion.objects.create(
            gimnasio=self.gimnasio,
            dia_semana=DiaSemana.LUNES,
            hora_inicio=time(9, 0),
            vacantes=3,
        )

        response = self.client.post(
            reverse("turnos:cupo_crear"),
            {"dia_semana": DiaSemana.LUNES, "hora_inicio": "09:00", "vacantes": 7},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Ya existe una excepción de cupo para ese día y horario.",
        )
        self.assertEqual(
            CupoExcepcion.objects.for_gimnasio(self.gimnasio).count(), 1
        )
        self.assertEqual(
            CupoExcepcion.objects.for_gimnasio(self.gimnasio).first().vacantes, 3
        )


class CupoExcepcionEliminarViewTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        self.staff_a = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF
        )
        self.excepcion_a = CupoExcepcion.objects.create(
            gimnasio=self.gimnasio_a,
            dia_semana=DiaSemana.LUNES,
            hora_inicio=time(8, 0),
            vacantes=3,
        )
        self.excepcion_b = CupoExcepcion.objects.create(
            gimnasio=self.gimnasio_b,
            dia_semana=DiaSemana.LUNES,
            hora_inicio=time(8, 0),
            vacantes=3,
        )
        self.client.login(username="staff-a", password="clave-123456")

    def test_eliminar_excepcion_propia_la_borra(self):
        response = self.client.post(
            reverse("turnos:cupo_eliminar", args=[self.excepcion_a.pk])
        )

        self.assertRedirects(response, reverse("turnos:configuracion"))
        self.assertFalse(
            CupoExcepcion.objects.filter(pk=self.excepcion_a.pk).exists()
        )

    def test_eliminar_excepcion_de_otro_gimnasio_da_404_y_no_borra(self):
        response = self.client.post(
            reverse("turnos:cupo_eliminar", args=[self.excepcion_b.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            CupoExcepcion.objects.filter(pk=self.excepcion_b.pk).exists()
        )


class ConfiguracionTurnosReconciliacionTests(TestCase):
    """Cambiar la configuración (vía la vista, no el service directo) debe
    disparar `reconciliar_reservas_desencajadas` y avisar al staff cuántas
    reservas futuras se reprogramaron y/o cancelaron -- pero nunca tocar
    reservas pasadas."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.fecha_pasada = (ahora - timedelta(days=2)).date()
        self.client.login(username="staff-1", password="clave-123456")

    def test_cambiar_duracion_reprograma_reserva_futura_desencajada_y_avisa(self):
        # 10:00 es franja válida con duración=60; deja de serlo con 45
        # (600' no es múltiplo de 45') -- pero 9:45 (585') sí lo es y queda
        # libre, así que se reprograma en vez de cancelarse.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        reserva = Reserva.objects.get(gimnasio=self.gimnasio, fecha=self.fecha_futura)
        self.assertEqual(reserva.hora_inicio, time(9, 45))
        self.assertContains(
            response,
            "Se reprogramaron 1 reserva(s) futura(s) a un nuevo horario.",
        )

    def test_end_to_end_reprogramar_genera_novedad_en_el_portal_del_alumno(self):
        # Cadena completa de la Parte B, a través de las vistas y plantillas
        # reales: el staff cambia la config -> la reserva se reprograma -> se
        # crea una Novedad personal -> el alumno la ve en su portal (con badge
        # "Nueva"), y el staff NO la ve en su listado de gestión.
        user_alumno = User.objects.create_user("alumno-e2e", password="clave-123456")
        perfil_alumno = Perfil.objects.create(
            usuario=user_alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Eva", apellido="Ruiz", perfil=perfil_alumno
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        novedad = Novedad.objects.get(alumno=alumno)
        self.assertEqual(novedad.titulo, "Cambió el horario de tu turno")

        listado = self.client.get(reverse("novedades:listado"))
        self.assertNotContains(listado, "Cambió el horario de tu turno")

        self.client.logout()
        self.client.login(username="alumno-e2e", password="clave-123456")
        portal = self.client.get(reverse("home"))
        self.assertContains(portal, "Cambió el horario de tu turno")
        self.assertContains(portal, "Nueva")

    def test_cambiar_duracion_cancela_si_no_hay_franja_alternativa_y_avisa(self):
        # Sin ningún HorarioAtencion ese día en la config nueva, no hay a
        # dónde reprogramar -- se cancela como antes.
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        self.assertFalse(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                fecha=self.fecha_futura,
                hora_inicio=time(10, 0),
            ).exists()
        )
        self.assertContains(
            response,
            "Se cancelaron 1 reserva(s) futura(s) que ya no encajan en la nueva grilla.",
        )

    def test_reserva_pasada_no_se_toca_ni_genera_aviso(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                fecha=self.fecha_pasada,
                hora_inicio=time(10, 0),
            ).exists()
        )
        self.assertNotContains(response, "Se cancelaron")
        self.assertNotContains(response, "Se reprogramaron")


# ---------------------------------------------------------------------------
# turnos/views.py (Task 5: grilla y reservas del alumno)
# ---------------------------------------------------------------------------


class TurnosAlumnoViewsAccesoTests(TestCase):
    """Anónimo -> redirect a login; staff -> 403, en mis_turnos/reservar/cancelar
    (patrón `TurnosViewsAccesoTests`, versión alumno)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(10, 0),
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )

    def _urls(self):
        return [
            reverse("turnos:mis_turnos"),
            reverse("turnos:reservar"),
            reverse("turnos:cancelar", args=[self.reserva.pk]),
        ]

    def test_anonimo_es_redirigido_a_login_en_todas_las_rutas(self):
        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_staff_recibe_403_en_todas_las_rutas(self):
        self.client.login(username="staff-1", password="clave-123456")

        for url in self._urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)


class MisTurnosViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")

        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        self.alumno_de_b = Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Carla", apellido="Ruiz"
        )

        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=2
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(8, 0),
                hora_hasta=time(12, 0),
            )

        self.user = User.objects.create_user("alumno-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil
        self.alumno.save()
        self.client.login(username="alumno-1", password="clave-123456")

        self.hoy = timezone.localdate()
        self.lunes_actual = self.hoy - timedelta(days=self.hoy.weekday())

    def test_muestra_la_ocupacion_de_una_franja_con_reserva_previa(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.lunes_actual,
            hora_inicio=time(9, 0),
        )

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1/2")

    def test_mis_reservas_incluye_href_de_google_calendar(self):
        fecha = self.hoy + timedelta(days=1)
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, fecha=fecha, hora_inicio=time(9, 0)
        )

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertContains(response, "calendar.google.com")

    def test_alumno_sin_ficha_no_rompe_y_no_muestra_reservas(self):
        user_sin_ficha = User.objects.create_user(
            "alumno-2", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=user_sin_ficha, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alumno-2", password="clave-123456")

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mis_reservas"], [])

    def test_gimnasio_sin_horarios_muestra_estado_vacio(self):
        gimnasio_sin_horario = Gimnasio.objects.create(
            nombre="Sin Horario", slug="sin-horario"
        )
        user_otro_gym = User.objects.create_user(
            "alumno-3", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=user_otro_gym,
            gimnasio=gimnasio_sin_horario,
            rol=Perfil.Rol.ALUMNO,
        )
        self.client.login(username="alumno-3", password="clave-123456")

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "El gimnasio todavía no configuró sus turnos."
        )

    def test_reserva_existente_sigue_visible_aunque_el_gimnasio_quede_sin_horarios(
        self,
    ):
        """Regression: "Mis reservas" no debe depender de `sin_horarios`. Si
        el staff borra todos los `HorarioAtencion` (p.ej. a mitad de una
        reconfiguración) mientras el alumno todavía tiene una `Reserva`
        futura de antes, tiene que poder seguir viéndola (y cancelarla desde
        ahí) aunque no haya grilla para reservar turnos nuevos -- la
        cancelación por POST directo ya funcionaba, pero quedaba
        indescubrible desde la pantalla."""
        fecha = self.hoy + timedelta(days=1)
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=fecha,
            hora_inicio=time(9, 0),
        )
        HorarioAtencion.objects.for_gimnasio(self.gimnasio).delete()

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mis reservas")
        # Identifica la fila de la reserva por su URL de cancelar (única por
        # pk) en vez de la fecha formateada -- `{{ reserva.fecha }}` se
        # localiza (es-ar) a "7 Julio 2026", no al `str(date)` de Python.
        self.assertContains(
            response, reverse("turnos:cancelar", args=[reserva.pk])
        )
        self.assertContains(
            response, "El gimnasio todavía no configuró sus turnos."
        )

    def test_alumno_de_gimnasio_b_no_ve_ocupacion_de_reservas_del_gimnasio_a(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.lunes_actual,
            hora_inicio=time(9, 0),
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.otro_alumno,
            fecha=self.lunes_actual,
            hora_inicio=time(9, 0),
        )

        ConfiguracionTurnos.objects.create(
            gimnasio=self.otro_gimnasio, duracion_minutos=60, vacantes_default=2
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.otro_gimnasio,
                dia_semana=dia,
                hora_desde=time(8, 0),
                hora_hasta=time(12, 0),
            )
        user_b = User.objects.create_user("alumno-b", password="clave-123456")
        perfil_b = Perfil.objects.create(
            usuario=user_b, gimnasio=self.otro_gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno_de_b.perfil = perfil_b
        self.alumno_de_b.save()
        self.client.login(username="alumno-b", password="clave-123456")

        response = self.client.get(reverse("turnos:mis_turnos"))

        grilla = response.context["grilla"]
        franja_9 = next(
            f for f in grilla[self.lunes_actual] if f.hora_inicio == time(9, 0)
        )
        self.assertEqual(franja_9.ocupadas, 0)


class ReservarViewTests(TestCase):
    """Franjas de 15' cubriendo casi todo el día, mismo patrón que
    `CrearReservaTests`, para poder anclar los casos de "libre", "llena",
    "cerrada" y "fuera de grilla" con precisión relativa a
    `timezone.localtime()`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=15, vacantes_default=1
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 45),
            )

        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.hora_futura = time(10, 0)

        base_15 = ahora.replace(
            minute=(ahora.minute // 15) * 15, second=0, microsecond=0
        )
        casi = base_15 + timedelta(minutes=30)
        if casi.time() == time(23, 45):
            casi -= timedelta(minutes=15)
        self.fecha_cerrada = casi.date()
        self.hora_cerrada = casi.time()

        self.user = User.objects.create_user("alumno-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil
        self.alumno.save()
        self.client.login(username="alumno-1", password="clave-123456")

    def _reservar(self, fecha, hora_inicio):
        return self.client.post(
            reverse("turnos:reservar"),
            {"fecha": fecha, "hora_inicio": hora_inicio.strftime("%H:%M")},
        )

    def test_reservar_franja_libre_y_vigente_crea_la_reserva(self):
        response = self._reservar(self.fecha_futura, self.hora_futura)

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                fecha=self.fecha_futura,
                hora_inicio=self.hora_futura,
            ).count(),
            1,
        )
        mensajes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("reservado" in m.lower() for m in mensajes))

    def test_reservar_franja_llena_no_crea_fila(self):
        crear_reserva(
            self.gimnasio, self.otro_alumno, self.fecha_futura, self.hora_futura
        )

        response = self._reservar(self.fecha_futura, self.hora_futura)

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(Reserva.objects.count(), 1)
        mensajes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("completo" in m.lower() for m in mensajes))

    def test_reservar_franja_cerrada_no_crea_fila(self):
        response = self._reservar(self.fecha_cerrada, self.hora_cerrada)

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_reservar_duplicada_no_crea_segunda_fila(self):
        crear_reserva(self.gimnasio, self.alumno, self.fecha_futura, self.hora_futura)

        response = self._reservar(self.fecha_futura, self.hora_futura)

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(Reserva.objects.count(), 1)

    def test_reservar_fuera_de_grilla_no_crea_fila(self):
        response = self.client.post(
            reverse("turnos:reservar"),
            {"fecha": self.fecha_futura, "hora_inicio": "10:07"},
        )

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_form_invalido_no_crea_fila_y_redirige(self):
        response = self.client.post(
            reverse("turnos:reservar"),
            {"fecha": "no-es-una-fecha", "hora_inicio": "10:00"},
        )

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_alumno_sin_ficha_recibe_403(self):
        user_sin_ficha = User.objects.create_user(
            "alumno-2", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=user_sin_ficha, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.client.login(username="alumno-2", password="clave-123456")

        response = self._reservar(self.fecha_futura, self.hora_futura)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reserva.objects.count(), 0)


class CancelarReservaViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")

        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.otro_alumno_mismo_gym = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        self.alumno_otro_gym = Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Carla", apellido="Ruiz"
        )

        self.user = User.objects.create_user("alumno-1", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno.perfil = self.perfil
        self.alumno.save()
        self.client.login(username="alumno-1", password="clave-123456")

        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=1)).date()
        self.fecha_pasada = (ahora - timedelta(days=1)).date()

    def test_cancelar_reserva_propia_futura_la_borra(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(reverse("turnos:cancelar", args=[reserva.pk]))

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancelar_reserva_de_otro_alumno_mismo_gimnasio_da_404_y_no_borra(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.otro_alumno_mismo_gym,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(reverse("turnos:cancelar", args=[reserva.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancelar_reserva_de_otro_gimnasio_da_404_y_no_borra(self):
        reserva = Reserva.objects.create(
            gimnasio=self.otro_gimnasio,
            alumno=self.alumno_otro_gym,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(reverse("turnos:cancelar", args=[reserva.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancelar_reserva_ya_pasada_no_la_borra(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(reverse("turnos:cancelar", args=[reserva.pk]))

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_alumno_sin_ficha_recibe_404_no_puede_cancelar_reserva_ajena(self):
        """Regression: `get_queryset()` debe devolver `Reserva.objects.none()`
        cuando `self.alumno is None`, no reventar filtrando `alumno=None` (que
        además, por accidente, podría matchear una fila real con
        `alumno_id IS NULL` si el modelo lo permitiera)."""
        user_sin_ficha = User.objects.create_user(
            "alumno-2", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=user_sin_ficha, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.client.login(username="alumno-2", password="clave-123456")

        response = self.client.post(reverse("turnos:cancelar", args=[reserva.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())


# ---------------------------------------------------------------------------
# turnos/views.py (Task 6: agenda de staff)
# ---------------------------------------------------------------------------


class AgendaViewAccesoTests(TestCase):
    """Anónimo -> redirect a login; alumno -> 403 (patrón
    `TurnosAlumnoViewsAccesoTests`, versión staff)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno_user = User.objects.create_user(
            "alumno-1", password="clave-123456"
        )
        Perfil.objects.create(
            usuario=self.alumno_user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )

    def test_anonimo_es_redirigido_a_login(self):
        url = reverse("turnos:agenda")

        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_alumno_recibe_403(self):
        self.client.login(username="alumno-1", password="clave-123456")

        response = self.client.get(reverse("turnos:agenda"))

        self.assertEqual(response.status_code, 403)


class AgendaViewTests(TestCase):
    """La agenda muestra la ocupación ("X/Y") y el detalle de quién reservó
    cada franja, sin filtrar reservas de otros gimnasios."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")

        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.alumno_de_otro_gym = Alumno.objects.create(
            gimnasio=self.otro_gimnasio, nombre="Carla", apellido="Ruiz"
        )

        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=2
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(8, 0),
                hora_hasta=time(12, 0),
            )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.otro_gimnasio, duracion_minutos=60, vacantes_default=2
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.otro_gimnasio,
                dia_semana=dia,
                hora_desde=time(8, 0),
                hora_hasta=time(12, 0),
            )

        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

        self.hoy = timezone.localdate()
        self.lunes_actual = self.hoy - timedelta(days=self.hoy.weekday())

    def test_muestra_ocupacion_y_nombre_del_alumno_anotado(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.lunes_actual,
            hora_inicio=time(9, 0),
        )

        response = self.client.get(reverse("turnos:agenda"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1/2")
        self.assertContains(response, "Gómez")
        self.assertContains(response, "Ana")

    def test_no_muestra_reservas_de_otro_gimnasio(self):
        Reserva.objects.create(
            gimnasio=self.otro_gimnasio,
            alumno=self.alumno_de_otro_gym,
            fecha=self.lunes_actual,
            hora_inicio=time(9, 0),
        )

        response = self.client.get(reverse("turnos:agenda"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Ruiz")

    def test_incluye_link_a_configuracion(self):
        response = self.client.get(reverse("turnos:agenda"))

        self.assertContains(response, reverse("turnos:configuracion"))


class NavStaffTurnosLinkTests(TestCase):
    """El nav de `base.html` para un staff logueado incluye el link a la
    agenda de turnos, en cualquier página que extienda `base.html` (se usa
    `home` como muestra, patrón brief Task 6)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-1", password="clave-123456")

    def test_nav_contiene_link_a_agenda_de_turnos(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("turnos:agenda"))
