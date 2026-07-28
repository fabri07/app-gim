"""Tests de `importaciones`. Ver `rutinas/tests.py` para el estilo de
fixtures de este repo."""

from django.test import TestCase

from importaciones.models import Importacion
from tenants.models import Gimnasio


class ImportacionModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central", slug="gimnasio-central"
        )

    def test_creacion_con_defaults(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.PLANTILLAS,
            resultado={"hojas": []},
        )
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertIsNone(importacion.confirmado_en)
        self.assertIsNone(importacion.creado_por)

    def test_str(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.BIBLIOTECA,
            resultado={"items": []},
        )
        self.assertIn("Gimnasio Central", str(importacion))


class ImportacionAislamientoTenantTests(TestCase):
    def test_for_gimnasio_no_mezcla_gimnasios(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        Importacion.objects.create(
            gimnasio=gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        Importacion.objects.create(
            gimnasio=gimnasio_b, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_a).count(), 1)
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_b).count(), 1)
