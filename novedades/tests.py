"""
Tests de Fase 1 para el modelo `Novedad`: creación básica, la regla de
"visible ahora" (`NovedadQuerySet.visibles`) y el aislamiento por gimnasio
heredado de `TenantQuerySet.for_gimnasio` (core).
"""

from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now

from novedades.models import Novedad
from tenants.models import Gimnasio


class NovedadModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )

    def test_creacion_y_str(self):
        novedad = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Cerrado el feriado",
            mensaje="El gimnasio no abre el lunes por feriado nacional.",
        )

        self.assertEqual(str(novedad), "Cerrado el feriado")
        self.assertTrue(novedad.activa)
        self.assertEqual(novedad.fecha_publicacion, now().date())
        self.assertIsNone(novedad.visible_hasta)


class NovedadVisiblesTests(TestCase):
    """Cubre la regla de negocio de `NovedadQuerySet.visibles()`."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.hoy = now().date()

    def test_visibles_devuelve_solo_la_activa_publicada_y_no_vencida(self):
        vigente = Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vigente",
            mensaje="Novedad actualmente visible.",
            fecha_publicacion=self.hoy,
            visible_hasta=None,
            activa=True,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Vencida",
            mensaje="Ya venció ayer.",
            fecha_publicacion=self.hoy - timedelta(days=10),
            visible_hasta=self.hoy - timedelta(days=1),
            activa=True,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Inactiva",
            mensaje="Fue ocultada por el staff.",
            fecha_publicacion=self.hoy,
            visible_hasta=None,
            activa=False,
        )
        Novedad.objects.create(
            gimnasio=self.gimnasio,
            titulo="Futura",
            mensaje="Todavía no se publicó.",
            fecha_publicacion=self.hoy + timedelta(days=1),
            visible_hasta=None,
            activa=True,
        )

        visibles = Novedad.objects.for_gimnasio(self.gimnasio).visibles()

        self.assertEqual(list(visibles), [vigente])


class NovedadTenantIsolationTests(TestCase):
    """Confirma que `for_gimnasio()` aísla novedades entre gimnasios."""

    def test_for_gimnasio_no_devuelve_novedades_de_otro_gimnasio(self):
        gimnasio_a = Gimnasio.objects.create(nombre="A", slug="a")
        gimnasio_b = Gimnasio.objects.create(nombre="B", slug="b")

        novedad_a = Novedad.objects.create(
            gimnasio=gimnasio_a, titulo="Aviso A", mensaje="Solo para A."
        )
        Novedad.objects.create(
            gimnasio=gimnasio_b, titulo="Aviso B", mensaje="Solo para B."
        )

        novedades_de_a = Novedad.objects.for_gimnasio(gimnasio_a)

        self.assertEqual(list(novedades_de_a), [novedad_a])
        self.assertNotIn(
            "Aviso B", novedades_de_a.values_list("titulo", flat=True)
        )
