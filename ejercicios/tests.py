"""
Tests de Fase 1 para la biblioteca de ejercicios: creación básica, choices de
`grupo_muscular` y aislamiento por gimnasio. Sigue el patrón de
tenants/tests.py::TenantIsolationTests.
"""

from django.test import TestCase

from ejercicios.models import Ejercicio
from tenants.models import Gimnasio


class EjercicioModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_basica_y_str(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

        self.assertEqual(str(ejercicio), "Sentadilla")
        self.assertTrue(ejercicio.activo)
        self.assertEqual(ejercicio.descripcion, "")
        self.assertEqual(ejercicio.url_video, "")

    def test_grupo_muscular_expone_la_etiqueta_legible(self):
        ejercicio = Ejercicio.objects.create(
            gimnasio=self.gimnasio,
            nombre="Press de banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )

        self.assertEqual(ejercicio.grupo_muscular, "pecho")
        self.assertEqual(ejercicio.get_grupo_muscular_display(), "Pecho")


class EjercicioTenantIsolationTests(TestCase):
    """Confirma que la biblioteca de ejercicios de un gimnasio no se mezcla
    con la de otro."""

    def test_for_gimnasio_devuelve_solo_los_ejercicios_de_ese_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a,
            nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b,
            nombre="Dominadas",
            grupo_muscular=Ejercicio.GrupoMuscular.ESPALDA,
        )

        resultado = Ejercicio.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(resultado), [ejercicio_a])
