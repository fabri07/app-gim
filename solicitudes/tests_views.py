"""Tests de las vistas públicas del embudo y de la cola en el panel."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from solicitudes import antispam, services
from solicitudes.models import SolicitudAcceso

_Estado = SolicitudAcceso.Estado


def _tiempo_valido():
    marca = (timezone.now() - timedelta(seconds=5)).timestamp()
    return signing.dumps(marca, salt=antispam._SIGNER_SALT, compress=True)


def _post_data(**over):
    base = {
        "nombre_gimnasio": "Box Fuerza",
        "nombre_contacto": "Ana",
        "email": "ana@box.com",
        "telefono": "",
        "cantidad_alumnos": "",
        "anios_operando": "",
        "tamano_staff": "",
        "banda_ingresos": "",
        "formato_registros": "",
        "profundidad_historia": "",
        "puede_compartir_archivos": "",
        "comentario": "",
        "website": "",
        "tiempo": _tiempo_valido(),
    }
    base.update(over)
    return base


class SolicitarViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_renderiza_el_form(self):
        r = self.client.get(reverse("solicitudes:solicitar"))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "solicitudes/solicitar.html")

    def test_post_valido_crea_solicitud_y_redirige(self):
        r = self.client.post(reverse("solicitudes:solicitar"), _post_data())
        self.assertRedirects(r, reverse("solicitudes:enviada"))
        s = SolicitudAcceso.objects.get(email="ana@box.com")
        self.assertEqual(s.estado, _Estado.NO_VERIFICADA)

    def test_honeypot_lleno_no_crea_pero_responde_igual(self):
        r = self.client.post(reverse("solicitudes:solicitar"), _post_data(website="x"))
        self.assertRedirects(r, reverse("solicitudes:enviada"))
        self.assertFalse(SolicitudAcceso.objects.exists())

    def test_envio_demasiado_rapido_no_crea(self):
        datos = _post_data(tiempo="")  # sin token = demasiado rápido
        r = self.client.post(reverse("solicitudes:solicitar"), datos)
        self.assertRedirects(r, reverse("solicitudes:enviada"))
        self.assertFalse(SolicitudAcceso.objects.exists())

    def test_respuesta_neutral_no_revela_duplicado(self):
        self.client.post(reverse("solicitudes:solicitar"), _post_data())
        # Segundo envío del mismo email (trámite abierto): misma pantalla.
        r = self.client.post(reverse("solicitudes:solicitar"), _post_data())
        self.assertRedirects(r, reverse("solicitudes:enviada"))
        self.assertEqual(SolicitudAcceso.objects.filter(email="ana@box.com").count(), 1)


class VerificarViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.solicitud = services.crear_solicitud(
            datos={
                "nombre_gimnasio": "Box",
                "nombre_contacto": "Ana",
                "email": "ana@box.com",
                "telefono": "",
                "cantidad_alumnos": None,
                "anios_operando": "",
                "tamano_staff": "",
                "banda_ingresos": "",
                "formato_registros": "",
                "profundidad_historia": "",
                "puede_compartir_archivos": "",
                "comentario": "",
            },
            ip_hash="",
            url_verificacion_para=lambda t: t,
        )

    def test_link_valido_confirma(self):
        tok = self.solicitud.tokens.first().token
        r = self.client.get(reverse("solicitudes:verificar", args=[tok]))
        self.assertEqual(r.status_code, 200)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, _Estado.PENDIENTE)

    def test_link_invalido_no_rompe(self):
        r = self.client.get(reverse("solicitudes:verificar", args=["nada"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no es válido")


class ColaEnElPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.super = User.objects.create_superuser("s", "s@x.com", "clave-123456")
        self.otro = User.objects.create_user("otro", password="clave-123456")
        self.solicitud = SolicitudAcceso.objects.create(
            nombre_gimnasio="Box", nombre_contacto="Ana", email="ana@box.com",
            estado=_Estado.PENDIENTE,
        )

    def test_solo_superadmin(self):
        r = self.client.get(reverse("plataforma:solicitud_lista"))
        self.assertIn(r.status_code, (302, 403))  # anónimo: redirect a login
        self.client.login(username="otro", password="clave-123456")
        r = self.client.get(reverse("plataforma:solicitud_lista"))
        self.assertEqual(r.status_code, 403)

    def test_lista_y_detalle(self):
        self.client.login(username="s", password="clave-123456")
        r = self.client.get(reverse("plataforma:solicitud_lista"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Box")
        r = self.client.get(reverse("plataforma:solicitud_detalle", args=[self.solicitud.pk]))
        self.assertEqual(r.status_code, 200)

    def test_aprobar_crea_el_gimnasio_y_redirige(self):
        self.client.login(username="s", password="clave-123456")
        r = self.client.post(reverse("plataforma:solicitud_aprobar", args=[self.solicitud.pk]))
        self.assertRedirects(r, reverse("plataforma:solicitud_detalle", args=[self.solicitud.pk]))
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, _Estado.APROBADA)
        self.assertIsNotNone(self.solicitud.gimnasio_creado)

    def test_aprobar_es_post_only(self):
        self.client.login(username="s", password="clave-123456")
        r = self.client.get(reverse("plataforma:solicitud_aprobar", args=[self.solicitud.pk]))
        self.assertEqual(r.status_code, 405)

    def test_rechazar(self):
        self.client.login(username="s", password="clave-123456")
        r = self.client.post(
            reverse("plataforma:solicitud_rechazar", args=[self.solicitud.pk]),
            {"motivo": "no aplica"},
        )
        self.assertRedirects(r, reverse("plataforma:solicitud_detalle", args=[self.solicitud.pk]))
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, _Estado.RECHAZADA)
