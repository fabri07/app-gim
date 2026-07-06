"""
Tests de Fase 6 (scaffold) para los modelos de `turnos`: creación + `__str__`
de cada modelo, idempotencia de `obtener_configuracion`, las restricciones de
unicidad (`ConfiguracionTurnos` por gimnasio, `Reserva` por
gimnasio/alumno/fecha/hora), el `CheckConstraint` de `HorarioAtencion` y el
aislamiento por gimnasio heredado de `TenantQuerySet.for_gimnasio` (core),
siguiendo el patrón de `novedades/tests.py::NovedadTenantIsolationTests`.
"""

from datetime import date, time

from django.db import IntegrityError, transaction
from django.test import TestCase

from alumnos.models import Alumno
from tenants.models import Gimnasio
from turnos.models import (
    ConfiguracionTurnos,
    CupoExcepcion,
    DiaSemana,
    HorarioAtencion,
    Reserva,
    obtener_configuracion,
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
