"""Tests de la máquina de estados de solicitudes.

`TransactionTestCase` (no `TestCase`) porque los mails salen por
`transaction.on_commit`, que bajo `TestCase` NUNCA corre (la transacción del
test no commitea). Con `override_settings` de un backend locmem chequeamos
`mail.outbox`.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from solicitudes import services
from solicitudes.models import SolicitudAcceso, SolicitudAccesoToken, SolicitudEvento
from tenants.models import Gimnasio, Perfil

_Estado = SolicitudAcceso.Estado

DATOS = {
    "nombre_gimnasio": "Box Fuerza",
    "nombre_contacto": "Ana",
    "email": "ana@box.com",
    "telefono": "",
    "cantidad_alumnos": 40,
    "anios_operando": "",
    "tamano_staff": "",
    "banda_ingresos": "",
    "formato_registros": "",
    "profundidad_historia": "",
    "puede_compartir_archivos": "",
    "comentario": "",
}


def _url_verif(tok):
    return f"http://testserver/solicitar/verificar/{tok}/"


def _url_panel(s):
    return f"http://testserver/plataforma/solicitudes/{s.pk}/"


def _link_inv(usuario):
    return f"http://testserver/accounts/reset/uid/token/"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@tugimapp.com",
    SOLICITUDES_AVISO_EMAIL="dueno@tugimapp.com",
)
class MaquinaDeEstadosTests(TransactionTestCase):
    def _crear(self, **over):
        datos = {**DATOS, **over}
        return services.crear_solicitud(
            datos=datos, ip_hash="h", url_verificacion_para=_url_verif
        )

    def test_crear_deja_no_verificada_con_token_y_manda_mail(self):
        s = self._crear()
        self.assertEqual(s.estado, _Estado.NO_VERIFICADA)
        self.assertEqual(s.email, "ana@box.com")
        self.assertEqual(s.tokens.count(), 1)
        self.assertTrue(SolicitudEvento.objects.filter(solicitud=s).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ana@box.com", mail.outbox[0].to)

    def test_email_se_normaliza_a_minusculas(self):
        s = self._crear(email="  ANA@Box.COM ")
        self.assertEqual(s.email, "ana@box.com")

    def test_segundo_tramite_abierto_para_el_mismo_email_no_crea_ni_revela(self):
        self._crear()
        mail.outbox.clear()
        segunda = self._crear()
        self.assertIsNone(segunda)
        self.assertEqual(SolicitudAcceso.objects.filter(email="ana@box.com").count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_verificar_pasa_a_pendiente_y_avisa_al_dueno(self):
        s = self._crear()
        tok = s.tokens.first().token
        mail.outbox.clear()
        solicitud, recien = services.verificar(token_str=tok, url_panel_para=_url_panel)
        self.assertTrue(recien)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, _Estado.PENDIENTE)
        self.assertIsNotNone(solicitud.verificada_en)
        self.assertEqual(len(mail.outbox), 1)  # aviso al dueño

    def test_verificar_es_idempotente(self):
        s = self._crear()
        tok = s.tokens.first().token
        services.verificar(token_str=tok, url_panel_para=_url_panel)
        mail.outbox.clear()
        _s, recien = services.verificar(token_str=tok, url_panel_para=_url_panel)
        self.assertFalse(recien)
        self.assertEqual(len(mail.outbox), 0)

    def test_verificar_token_vencido_no_verifica(self):
        s = self._crear()
        token = s.tokens.first()
        token.expira = timezone.now() - timedelta(days=1)
        token.save(update_fields=["expira"])
        _s, recien = services.verificar(token_str=token.token, url_panel_para=_url_panel)
        self.assertFalse(recien)
        s.refresh_from_db()
        self.assertEqual(s.estado, _Estado.NO_VERIFICADA)

    def test_verificar_token_desconocido(self):
        solicitud, recien = services.verificar(token_str="nada", url_panel_para=_url_panel)
        self.assertIsNone(solicitud)
        self.assertFalse(recien)

    def _pendiente(self, **over):
        s = self._crear(**over)
        services.verificar(token_str=s.tokens.first().token, url_panel_para=_url_panel)
        s.refresh_from_db()
        return s

    def test_aprobar_crea_el_gimnasio_y_manda_invitacion(self):
        s = self._pendiente()
        mail.outbox.clear()
        gimnasio, resultado = services.aprobar(
            solicitud=s, actor=None, link_invitacion_para=_link_inv
        )
        self.assertEqual(resultado, "aprobada")
        s.refresh_from_db()
        self.assertEqual(s.estado, _Estado.APROBADA)
        self.assertEqual(s.gimnasio_creado, gimnasio)
        # crear_gimnasio acuñó User + Perfil STAFF + categorías
        self.assertTrue(
            Perfil.objects.filter(gimnasio=gimnasio, rol=Perfil.Rol.STAFF).exists()
        )
        self.assertTrue(get_user_model().objects.filter(username="ana@box.com").exists())
        self.assertEqual(len(mail.outbox), 1)  # invitación
        self.assertIn("ana@box.com", mail.outbox[0].to)

    def test_aprobar_es_idempotente(self):
        s = self._pendiente()
        g1, _ = services.aprobar(solicitud=s, actor=None, link_invitacion_para=_link_inv)
        cuantos = Gimnasio.objects.count()
        mail.outbox.clear()
        g2, resultado = services.aprobar(
            solicitud=s, actor=None, link_invitacion_para=_link_inv
        )
        self.assertEqual(resultado, "ya_existia")
        self.assertEqual(g1, g2)
        self.assertEqual(Gimnasio.objects.count(), cuantos)
        self.assertEqual(len(mail.outbox), 0)

    def test_aprobar_con_email_ya_usado_no_crea_y_avisa(self):
        # Ya existe un usuario con ese email (p. ej. staff de otro gimnasio).
        get_user_model().objects.create_user("ana@box.com", password="x123456789")
        s = self._pendiente()
        cuantos = Gimnasio.objects.count()
        mail.outbox.clear()
        gimnasio, resultado = services.aprobar(
            solicitud=s, actor=None, link_invitacion_para=_link_inv
        )
        self.assertEqual(resultado, "email_en_uso")
        self.assertIsNone(gimnasio)
        self.assertEqual(Gimnasio.objects.count(), cuantos)
        s.refresh_from_db()
        self.assertNotEqual(s.estado, _Estado.APROBADA)
        self.assertEqual(len(mail.outbox), 1)  # "ya tenés cuenta"

    def test_rechazar_y_lista_espera(self):
        s = self._pendiente()
        mail.outbox.clear()
        services.rechazar(solicitud=s, actor=None, motivo="no aplica")
        s.refresh_from_db()
        self.assertEqual(s.estado, _Estado.RECHAZADA)
        self.assertEqual(s.motivo_decision, "no aplica")
        self.assertEqual(len(mail.outbox), 1)

    def test_lista_espera(self):
        s = self._pendiente()
        services.a_lista_espera(solicitud=s, actor=None, motivo="")
        s.refresh_from_db()
        self.assertEqual(s.estado, _Estado.LISTA_ESPERA)

    def test_cerrada_permite_un_nuevo_tramite_del_mismo_email(self):
        s = self._pendiente()
        services.rechazar(solicitud=s, actor=None, motivo="")
        # Ahora el email puede volver a solicitar (la constraint es parcial).
        nueva = self._crear()
        self.assertIsNotNone(nueva)
        self.assertNotEqual(nueva.pk, s.pk)

    def test_expirar_vencidas(self):
        s = self._crear()
        token = s.tokens.first()
        token.expira = timezone.now() - timedelta(days=1)
        token.save(update_fields=["expira"])
        n = services.expirar_vencidas()
        self.assertEqual(n, 1)
        s.refresh_from_db()
        self.assertEqual(s.estado, _Estado.EXPIRADA)
