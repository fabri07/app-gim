"""Tests de la integración con Google Calendar (Parte C), con la API de Google
MOCKEADA (sin red).

- C1: campo cifrado, disponibilidad, vistas de conectar/callback/desconectar.
- C2: sync de reservas por signals (crear/actualizar/borrar), backfill, resync
  por duración, idempotencia, best-effort ante errores, y degradación cuando la
  integración está apagada.

`GOOGLE_CALENDAR_ENABLED` y las 4 credenciales se fijan por `override_settings`
(en runtime dependen del entorno). El sync real corre en `transaction.on_commit`,
así que los tests que lo ejercitan usan `captureOnCommitCallbacks(execute=True)`.
"""

from datetime import time, timedelta
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio, Perfil
from turnos.models import ConfiguracionTurnos, HorarioAtencion, Reserva
from turnos.services import reconciliar_reservas_desencajadas
from calendario import services
from calendario.models import GoogleCalendarCredential, ReservaCalendarEvent

_TEST_KEY = Fernet.generate_key().decode()

GOOGLE_ON = dict(
    GOOGLE_CALENDAR_ENABLED=True,
    GOOGLE_OAUTH_CLIENT_ID="cid",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://app.example.com/calendario/callback/",
    GOOGLE_TOKEN_ENCRYPTION_KEY=_TEST_KEY,
)


def _mock_service():
    """Un cliente de Calendar mockeado con respuestas verosímiles."""
    service = MagicMock(name="calendar_service")
    service.calendars.return_value.insert.return_value.execute.return_value = {"id": "cal_abc"}
    service.events.return_value.insert.return_value.execute.return_value = {"id": "evt_1"}
    service.events.return_value.update.return_value.execute.return_value = {"id": "evt_1"}
    service.events.return_value.delete.return_value.execute.return_value = {}
    return service


@override_settings(**GOOGLE_ON)
class EncryptedTextFieldTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez")

    def test_round_trip_y_no_guarda_plano(self):
        GoogleCalendarCredential.objects.create(
            alumno=self.alumno, refresh_token="mi-token-secreto"
        )

        recargada = GoogleCalendarCredential.objects.get(alumno=self.alumno)
        self.assertEqual(recargada.refresh_token, "mi-token-secreto")

        with connection.cursor() as cur:
            cur.execute("SELECT refresh_token FROM calendario_googlecalendarcredential")
            crudo = cur.fetchone()[0]
        self.assertNotIn("mi-token-secreto", crudo)  # cifrado en la DB

    def test_vacio_se_guarda_tal_cual(self):
        cred = GoogleCalendarCredential.objects.create(alumno=self.alumno)
        self.assertEqual(cred.refresh_token, "")


class IntegracionActivaTests(TestCase):
    @override_settings(**GOOGLE_ON)
    def test_activa_con_las_4_vars(self):
        self.assertTrue(services.integracion_activa())

    @override_settings(GOOGLE_CALENDAR_ENABLED=False)
    def test_apagada_sin_vars(self):
        self.assertFalse(services.integracion_activa())


@override_settings(**GOOGLE_ON)
class ConectarViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.user = User.objects.create_user("alu", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez", perfil=self.perfil
        )

    def test_anonimo_redirige_a_login(self):
        url = reverse("calendario:conectar")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_staff_recibe_403(self):
        staff = User.objects.create_user("staff", password="clave-123456")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.login(username="staff", password="clave-123456")
        self.assertEqual(self.client.get(reverse("calendario:conectar")).status_code, 403)

    @override_settings(GOOGLE_CALENDAR_ENABLED=False)
    def test_integracion_apagada_redirige_sin_ir_a_google(self):
        self.client.login(username="alu", password="clave-123456")
        response = self.client.get(reverse("calendario:conectar"))
        self.assertRedirects(response, reverse("turnos:mis_turnos"))

    @patch(
        "calendario.services.build_authorization_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?x=1", "st_123"),
    )
    def test_redirige_a_google_y_guarda_state(self, _mock):
        self.client.login(username="alu", password="clave-123456")
        response = self.client.get(reverse("calendario:conectar"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        self.assertEqual(self.client.session["calendario_oauth_state"], "st_123")


@override_settings(**GOOGLE_ON)
class CallbackViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.user = User.objects.create_user("alu", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez", perfil=self.perfil
        )
        self.client.login(username="alu", password="clave-123456")

    def _sembrar_state(self, state="st_ok"):
        session = self.client.session
        session["calendario_oauth_state"] = state
        session["calendario_oauth_state_ts"] = timezone.now().isoformat()
        session.save()

    @patch("calendario.services.sincronizar_reservas_futuras")
    @patch("calendario.services.asegurar_calendario_secundario")
    @patch("calendario.services.intercambiar_code")
    def test_callback_ok_guarda_credencial_y_backfillea(self, mock_code, mock_cal, mock_backfill):
        mock_code.return_value = MagicMock(refresh_token="r_tok", token="a_tok", expiry=None, scopes=None)
        self._sembrar_state("st_ok")

        response = self.client.get(reverse("calendario:callback"), {"code": "c", "state": "st_ok"})

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        cred = GoogleCalendarCredential.objects.get(alumno=self.alumno)
        self.assertEqual(cred.refresh_token, "r_tok")
        self.assertTrue(cred.esta_conectada)
        mock_cal.assert_called_once()
        mock_backfill.assert_called_once_with(self.alumno)

    def test_callback_con_state_invalido_no_guarda_credencial(self):
        self._sembrar_state("st_ok")
        response = self.client.get(reverse("calendario:callback"), {"code": "c", "state": "st_MALO"})
        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertFalse(GoogleCalendarCredential.objects.exists())

    def test_callback_con_error_de_google_no_guarda(self):
        self._sembrar_state("st_ok")
        response = self.client.get(reverse("calendario:callback"), {"error": "access_denied", "state": "st_ok"})
        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertFalse(GoogleCalendarCredential.objects.exists())

    def test_guardar_credencial_sin_refresh_token_conserva_el_previo(self):
        GoogleCalendarCredential.objects.create(alumno=self.alumno, refresh_token="viejo")
        creds = MagicMock(refresh_token=None, token="nuevo_access", expiry=None, scopes=None)

        services.guardar_credencial(self.alumno, creds)

        cred = GoogleCalendarCredential.objects.get(alumno=self.alumno)
        self.assertEqual(cred.refresh_token, "viejo")
        self.assertEqual(cred.access_token, "nuevo_access")

    @patch("calendario.services.sincronizar_reservas_futuras")
    @patch("calendario.services.asegurar_calendario_secundario")
    @patch("calendario.services.intercambiar_code")
    def test_callback_primera_conexion_sin_refresh_token_aborta(self, mock_code, mock_cal, mock_backfill):
        # Primera conexión, Google no manda refresh_token y no había uno previo.
        mock_code.return_value = MagicMock(refresh_token=None, token="a_tok", expiry=None, scopes=None)
        self._sembrar_state("st_ok")

        response = self.client.get(reverse("calendario:callback"), {"code": "c", "state": "st_ok"})

        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertFalse(GoogleCalendarCredential.objects.exists())  # no deja credencial inútil
        mock_cal.assert_not_called()
        mock_backfill.assert_not_called()


@override_settings(**GOOGLE_ON)
class DesconectarViewTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.user = User.objects.create_user("alu", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez", perfil=self.perfil
        )
        self.cred = GoogleCalendarCredential.objects.create(
            alumno=self.alumno, refresh_token="r", google_calendar_id="cal_abc"
        )
        self.client.login(username="alu", password="clave-123456")

    @patch("calendario.services.revocar")
    @patch("calendario.services.borrar_calendario_secundario", return_value=True)
    def test_desconectar_borra_calendario_revoca_y_limpia_local(self, mock_borrar, mock_revocar):
        response = self.client.post(reverse("calendario:desconectar"))
        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        mock_borrar.assert_called_once()
        mock_revocar.assert_called_once()
        self.assertFalse(GoogleCalendarCredential.objects.filter(alumno=self.alumno).exists())

    @patch("calendario.services.revocar")
    @patch("calendario.services.borrar_calendario_secundario", return_value=False)
    def test_desconectar_con_borrado_remoto_fallido_igual_limpia_local(self, mock_borrar, mock_revocar):
        response = self.client.post(reverse("calendario:desconectar"))
        self.assertRedirects(response, reverse("turnos:mis_turnos"))
        self.assertFalse(GoogleCalendarCredential.objects.filter(alumno=self.alumno).exists())


@override_settings(**GOOGLE_ON)
class SyncReservasSignalsTests(TestCase):
    """C2: los signals sincronizan la reserva con Calendar (API mockeada)."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez")
        self.config = ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio, dia_semana=dia,
                hora_desde=time(0, 0), hora_hasta=time(23, 0),
            )
        self.fecha_futura = (timezone.localtime() + timedelta(days=2)).date()

    def _conectar(self):
        return GoogleCalendarCredential.objects.create(
            alumno=self.alumno, refresh_token="r", google_calendar_id="cal_abc"
        )

    def _reserva(self, hora=time(10, 0)):
        return Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=self.fecha_futura, hora_inicio=hora,
        )

    def test_sin_credencial_no_llama_a_la_api_ni_crea_evento(self):
        with patch("calendario.services.get_calendar_service") as mock_svc:
            with self.captureOnCommitCallbacks(execute=True):
                self._reserva()
            mock_svc.assert_not_called()
        self.assertFalse(ReservaCalendarEvent.objects.exists())

    def test_con_credencial_crea_evento(self):
        self._conectar()
        with patch("calendario.services.get_calendar_service", return_value=_mock_service()):
            with self.captureOnCommitCallbacks(execute=True):
                reserva = self._reserva()
        evento = ReservaCalendarEvent.objects.get(reserva=reserva)
        self.assertEqual(evento.google_event_id, "evt_1")
        self.assertEqual(evento.sync_status, ReservaCalendarEvent.SyncStatus.OK)

    def test_crear_evento_es_idempotente(self):
        self._conectar()
        reserva = self._reserva()
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="ya_existe")
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            services.crear_evento(reserva)
        service.events.return_value.insert.assert_not_called()  # no duplica
        service.events.return_value.update.assert_called_once()  # actualiza
        self.assertEqual(ReservaCalendarEvent.objects.filter(reserva=reserva).count(), 1)

    def test_cancelar_reserva_borra_el_evento(self):
        self._conectar()
        reserva = self._reserva()
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="evt_1")
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                reserva.delete()
        service.events.return_value.delete.assert_called_once()

    def test_error_de_api_deja_sync_status_error_sin_romper_la_reserva(self):
        self._conectar()
        service = _mock_service()
        service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("boom token=xyz")
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                reserva = self._reserva()
        self.assertTrue(Reserva.objects.filter(pk=reserva.pk).exists())
        evento = ReservaCalendarEvent.objects.get(reserva=reserva)
        self.assertEqual(evento.sync_status, ReservaCalendarEvent.SyncStatus.ERROR)
        self.assertIn("RuntimeError", evento.last_error)

    @override_settings(GOOGLE_CALENDAR_ENABLED=False)
    def test_integracion_apagada_no_sincroniza(self):
        self._conectar()
        with patch("calendario.services.get_calendar_service") as mock_svc:
            with self.captureOnCommitCallbacks(execute=True):
                self._reserva()
            mock_svc.assert_not_called()
        self.assertFalse(ReservaCalendarEvent.objects.exists())

    def test_reconciliacion_migrada_actualiza_el_evento(self):
        self._conectar()
        reserva = self._reserva(time(10, 0))
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="evt_1")
        service = _mock_service()
        self.config.duracion_minutos = 45  # 10:00 se desencaja -> migra a 09:45
        self.config.save()
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                resultado = reconciliar_reservas_desencajadas(self.gimnasio)
        self.assertEqual(resultado.migradas, 1)
        service.events.return_value.update.assert_called()

    def test_reconciliacion_cancelada_borra_el_evento(self):
        self._conectar()
        reserva = self._reserva(time(10, 0))
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="evt_1")
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()  # sin franjas ese día -> se cancela
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                resultado = reconciliar_reservas_desencajadas(self.gimnasio)
        self.assertEqual(resultado.canceladas, 1)
        service.events.return_value.delete.assert_called_once()

    def test_cambio_de_duracion_resincroniza_eventos_futuros(self):
        self._conectar()
        # 03:00 sigue siendo franja válida con 45' -> no se migra, pero cambia el dtend.
        reserva = self._reserva(time(3, 0))
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="evt_1")
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                self.config.duracion_minutos = 45
                self.config.save()
        service.events.return_value.update.assert_called()

    def test_reserva_save_sin_update_fields_actualiza_el_evento(self):
        self._conectar()
        reserva = self._reserva()
        ReservaCalendarEvent.objects.create(reserva=reserva, google_event_id="evt_1")
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            with self.captureOnCommitCallbacks(execute=True):
                reserva.save()  # update_fields is None
        service.events.return_value.update.assert_called()

    def test_backfill_crea_eventos_de_reservas_futuras_preexistentes(self):
        # Reserva creada ANTES de conectar (integración apagada -> sin evento).
        with override_settings(GOOGLE_CALENDAR_ENABLED=False):
            reserva = self._reserva()
        self.assertFalse(ReservaCalendarEvent.objects.exists())

        self._conectar()
        service = _mock_service()
        with patch("calendario.services.get_calendar_service", return_value=service):
            services.sincronizar_reservas_futuras(self.alumno)

        evento = ReservaCalendarEvent.objects.get(reserva=reserva)
        self.assertEqual(evento.google_event_id, "evt_1")
        self.assertEqual(evento.sync_status, ReservaCalendarEvent.SyncStatus.OK)

    def test_token_expirado_se_refresca_y_persiste_el_nuevo_access(self):
        cred = self._conectar()
        fake_creds = MagicMock(valid=False, refresh_token="r", token="nuevo_access", expiry=None)
        fake_creds.refresh.side_effect = lambda _req: setattr(fake_creds, "valid", True)
        with patch("calendario.services._google_credentials", return_value=fake_creds), \
                patch("googleapiclient.discovery.build"):
            services.get_calendar_service(cred)
        fake_creds.refresh.assert_called_once()
        cred.refresh_from_db()
        self.assertEqual(cred.access_token, "nuevo_access")

    def test_refresh_error_marca_la_credencial_revocada(self):
        from google.auth.exceptions import RefreshError

        cred = self._conectar()
        reserva = self._reserva()
        fake_creds = MagicMock(valid=False, refresh_token="r", token="t", expiry=None)
        fake_creds.refresh.side_effect = RefreshError("invalid_grant")
        with patch("calendario.services._google_credentials", return_value=fake_creds):
            services.crear_evento(reserva)  # dispara get_calendar_service -> refresh falla

        cred.refresh_from_db()
        self.assertIsNotNone(cred.revoked_at)
        self.assertFalse(cred.esta_conectada)
        evento = ReservaCalendarEvent.objects.get(reserva=reserva)
        self.assertEqual(evento.sync_status, ReservaCalendarEvent.SyncStatus.ERROR)


@override_settings(**GOOGLE_ON)
class PortalCalendarUITests(TestCase):
    """El portal "Mis Turnos" ofrece conectar / muestra el estado conectado."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Alfa", slug="alfa")
        self.user = User.objects.create_user("alu", password="clave-123456")
        self.perfil = Perfil.objects.create(
            usuario=self.user, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez", perfil=self.perfil
        )
        self.client.login(username="alu", password="clave-123456")

    def test_muestra_boton_conectar_si_no_conectado(self):
        response = self.client.get(reverse("turnos:mis_turnos"))
        self.assertContains(response, "Conectar mi Google Calendar")

    def test_muestra_estado_conectado(self):
        GoogleCalendarCredential.objects.create(
            alumno=self.alumno, refresh_token="r",
            google_calendar_id="cal_abc", google_calendar_summary="Turnos de Alfa",
        )
        response = self.client.get(reverse("turnos:mis_turnos"))
        self.assertContains(response, "Turnos de Alfa")
        self.assertContains(response, "Desconectar")
        self.assertNotContains(response, "Conectar mi Google Calendar")

    @override_settings(GOOGLE_CALENDAR_ENABLED=False)
    def test_integracion_apagada_no_muestra_card(self):
        response = self.client.get(reverse("turnos:mis_turnos"))
        self.assertNotContains(response, "Conectar mi Google Calendar")

    def test_muestra_reintentar_si_hay_evento_en_error(self):
        GoogleCalendarCredential.objects.create(
            alumno=self.alumno, refresh_token="r",
            google_calendar_id="cal_abc", google_calendar_summary="Turnos de Alfa",
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        # La reserva dispara el signal, pero sin captureOnCommitCallbacks el
        # on_commit no corre (no toca la API real en el test).
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=(timezone.localtime() + timedelta(days=2)).date(), hora_inicio=time(10, 0),
        )
        ReservaCalendarEvent.objects.create(
            reserva=reserva, sync_status=ReservaCalendarEvent.SyncStatus.ERROR
        )

        response = self.client.get(reverse("turnos:mis_turnos"))

        self.assertContains(response, "Reintentar sincronización")
