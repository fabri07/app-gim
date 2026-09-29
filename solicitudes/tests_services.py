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
    EMAIL_REPLY_TO="soporte@tugimapp.com",
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
        # El From es no-reply: la respuesta del interesado tiene que caer en
        # una casilla que alguien lea, o el lead se pierde acá.
        self.assertEqual(mail.outbox[0].reply_to, ["soporte@tugimapp.com"])

    @override_settings(EMAIL_REPLY_TO="")
    def test_sin_casilla_configurada_el_mail_sale_sin_reply_to(self):
        """Es el estado que corre en producción entre el deploy de esta feature
        y el momento en que se carga `EMAIL_REPLY_TO` en Render -- y el de
        cualquier entorno que no la cargue nunca. Sin el condicional de
        `_enviar`, la cabecera saldría como `Reply-To: <>`, que es peor que no
        mandarla: algunos servidores rechazan el mensaje entero y el lead se
        pierde sin que quede rastro (el envío es `fail_silently`)."""
        self._crear()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, [])

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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@tugimapp.com",
    EMAIL_REPLY_TO="soporte@tugimapp.com",
    SOLICITUDES_AVISO_EMAIL="dueno@tugimapp.com",
)
class CuerpoDeLosMailsDeDecisionTests(TransactionTestCase):
    """Lo que el interesado LEE cuando se lo rechaza o se lo posterga.

    `motivo_decision` es la nota de triage del superadmin (se pide rotulada
    como interna en `plataforma/solicitud_detalle.html` y se muestra en el
    historial de auditoría). Estos tests existen porque los templates la
    interpolaban tal cual en el cuerpo: un «no es un gimnasio real» escrito
    para uso interno terminaba leído por la persona de la que se escribió.
    """

    MOTIVO_INTERNO = "PRUEBA INTERNA - no es un gimnasio real, descartar"

    def _pendiente(self):
        s = services.crear_solicitud(
            datos=DATOS, ip_hash="h", url_verificacion_para=_url_verif
        )
        services.verificar(token_str=s.tokens.first().token, url_panel_para=_url_panel)
        s.refresh_from_db()
        return s

    def _cuerpo_tras(self, transicion):
        s = self._pendiente()
        mail.outbox.clear()
        transicion(solicitud=s, actor=None, motivo=self.MOTIVO_INTERNO)
        self.assertEqual(len(mail.outbox), 1)
        s.refresh_from_db()
        # La nota SÍ se guarda: lo que no tiene que hacer es viajar.
        self.assertEqual(s.motivo_decision, self.MOTIVO_INTERNO)
        return mail.outbox[0]

    def test_el_rechazo_no_le_manda_la_nota_interna_al_interesado(self):
        msg = self._cuerpo_tras(services.rechazar)
        self.assertNotIn(self.MOTIVO_INTERNO, msg.body)
        self.assertNotIn("no es un gimnasio real", msg.body)

    def test_la_lista_de_espera_no_le_manda_la_nota_interna_al_interesado(self):
        msg = self._cuerpo_tras(services.a_lista_espera)
        self.assertNotIn(self.MOTIVO_INTERNO, msg.body)

    def test_el_rechazo_deja_la_puerta_abierta_y_una_casilla_real(self):
        """Un rechazo sin salida hace que el lead no vuelva nunca. Con
        `EMAIL_REPLY_TO` seteada, el cuerpo dice a dónde escribir."""
        msg = self._cuerpo_tras(services.rechazar)
        self.assertIn("soporte@tugimapp.com", msg.body)
        self.assertEqual(msg.reply_to, ["soporte@tugimapp.com"])

    @override_settings(EMAIL_REPLY_TO="")
    def test_sin_casilla_configurada_no_invita_a_escribir_a_ninguna_parte(self):
        """El `From` es no-reply: prometer un canal que no existe es peor que
        no ofrecer ninguno (mismo criterio que el link de 'olvidé mi
        contraseña', que se esconde sin `EMAIL_*`)."""
        msg = self._cuerpo_tras(services.rechazar)
        self.assertNotIn("escribinos", msg.body)
        self.assertNotIn("@", msg.body.replace("no-reply@tugimapp.com", ""))
        self.assertIsNone(msg.reply_to or None)
