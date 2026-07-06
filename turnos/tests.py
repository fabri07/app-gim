"""
Tests de Fase 6 para `turnos`: modelos (creación + `__str__` de cada uno,
idempotencia de `obtener_configuracion`, restricciones de unicidad, el
`CheckConstraint` de `HorarioAtencion` y el aislamiento por gimnasio heredado
de `TenantQuerySet.for_gimnasio`, patrón `novedades/tests.py`) y los
servicios de dominio de `turnos/services.py` (corte de horarios en franjas,
cupos, alta/baja de reservas y limpieza de reservas desencajadas).

Los tests de `crear_reserva`/`cancelar_reserva`/`eliminar_reservas_desencajadas`
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
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
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
    eliminar_reservas_desencajadas,
    es_franja_vigente,
    franjas_de_rango,
    franjas_del_dia,
    grilla_semanal,
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


class EliminarReservasDesencajadasTests(TestCase):
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

    def test_borra_reserva_futura_que_queda_desencajada(self):
        # 10:00 es franja válida con duracion=60; deja de serlo con 45
        # (600 min no es múltiplo de 45).
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        borradas = eliminar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(borradas, 1)
        self.assertFalse(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(10, 0)
            ).exists()
        )

    def test_no_borra_reserva_pasada_aunque_quede_desencajada(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        borradas = eliminar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(borradas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_pasada, hora_inicio=time(10, 0)
            ).exists()
        )

    def test_no_borra_reserva_futura_que_sigue_encajando(self):
        # 03:00 = 180', que sigue siendo múltiplo de 45 -> sigue siendo franja.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(3, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        borradas = eliminar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(borradas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(3, 0)
            ).exists()
        )

    def test_reducir_el_cupo_no_borra_ninguna_reserva(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.vacantes_default = 1
        self.config.save()

        borradas = eliminar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(borradas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(10, 0)
            ).exists()
        )


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
    disparar `eliminar_reservas_desencajadas` y avisar al staff cuántas
    reservas futuras se cancelaron -- pero nunca tocar reservas pasadas."""

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

    def test_cambiar_duracion_borra_reserva_futura_desencajada_y_avisa(self):
        # 10:00 es franja válida con duración=60; deja de serlo con 45
        # (600' no es múltiplo de 45').
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

    def test_reserva_pasada_no_se_borra_ni_genera_aviso(self):
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
