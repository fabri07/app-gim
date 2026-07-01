"""
Tests de `Alumno`: creación básica, valores por defecto y aislamiento de
tenant (mismo criterio que `tenants/tests.py::TenantIsolationTests`).
"""

from django.test import TestCase

from alumnos.models import Alumno
from tenants.models import Gimnasio


class AlumnoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        self.assertEqual(str(alumno), "Pérez, Juan")

    def test_estado_por_defecto_activo_y_fecha_activacion_none(self):
        alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.assertEqual(alumno.estado, Alumno.Estado.ACTIVO)
        self.assertIsNone(alumno.fecha_activacion)


class TenantIsolationTests(TestCase):
    """Confirma que dos gimnasios no comparten alumnos."""

    def test_for_gimnasio_devuelve_solo_los_alumnos_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")
        alumno_a = Alumno.objects.create(
            gimnasio=gimnasio_a, nombre="Alumno", apellido="A"
        )
        alumno_b = Alumno.objects.create(
            gimnasio=gimnasio_b, nombre="Alumno", apellido="B"
        )

        resultado = Alumno.objects.for_gimnasio(gimnasio_a)

        self.assertIn(alumno_a, resultado)
        self.assertNotIn(alumno_b, resultado)
