"""Lógica de la integración con Google Calendar (Parte C).

Dos bloques:

1. OAuth / conexión (C1): armar la URL de autorización, intercambiar el code por
   tokens, refrescar el access token, y provisionar el calendario secundario
   "Turnos de {gimnasio}".
2. Sync de eventos (C2): crear/actualizar/borrar el evento de una reserva, el
   backfill al conectar y el resync por cambio de duración.

Todo el bloque 2 es **best-effort**: si la integración no está activa, si el
alumno no conectó su cuenta, o si la API de Google falla, no se rompe la reserva
-- se loguea y (para eventos) se deja `ReservaCalendarEvent.sync_status=error`.
Las llamadas a la API se disparan desde signals con `transaction.on_commit`
(ver `calendario/signals.py`), nunca dentro de una transacción abierta.

Los `import` de las libs de Google son locales a cada función para no acoplar el
import del módulo a ellas (mismo espíritu que los imports tardíos del repo).
"""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils.timezone import now

logger = logging.getLogger(__name__)

TZ = "America/Argentina/Buenos_Aires"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_REVOKE_URI = "https://oauth2.googleapis.com/revoke"


# --------------------------------------------------------------------------- #
# Configuración / disponibilidad
# --------------------------------------------------------------------------- #

def integracion_activa() -> bool:
    """True solo si están las 4 credenciales GOOGLE_* (ver settings)."""
    return bool(settings.GOOGLE_CALENDAR_ENABLED)


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": _TOKEN_URI,
            "redirect_uris": [settings.GOOGLE_OAUTH_REDIRECT_URI],
        }
    }


def _sanitizar_error(exc: Exception) -> str:
    """Código + mensaje corto para `last_error`/logs. NUNCA tokens ni la
    respuesta completa de la API."""
    return f"{type(exc).__name__}: {str(exc)[:200]}"


# --------------------------------------------------------------------------- #
# OAuth (C1)
# --------------------------------------------------------------------------- #

def build_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        _client_config(),
        scopes=settings.GOOGLE_CALENDAR_SCOPES,
        state=state,
        redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
    )


def build_authorization_url() -> tuple[str, str]:
    """URL a la que mandar al alumno + el `state` (anti-CSRF, se guarda en sesión).

    `access_type=offline` para recibir `refresh_token`; `prompt=consent` para que
    Google lo reenvíe también en reconexiones (no siempre lo hace);
    `include_granted_scopes=true` para consentimiento incremental.
    """
    url, state = build_flow().authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url, state


def intercambiar_code(code: str, state: str):
    """Cambia el authorization code por credenciales de Google."""
    flow = build_flow(state=state)
    flow.fetch_token(code=code)
    return flow.credentials


def guardar_credencial(alumno, credentials):
    """Crea/actualiza la `GoogleCalendarCredential` del alumno con los tokens.

    Si Google no devolvió `refresh_token` (puede pasar en reconexiones), se
    conserva el que ya estaba guardado -- sin él no se puede refrescar después.
    """
    from calendario.models import GoogleCalendarCredential

    credencial, _ = GoogleCalendarCredential.objects.get_or_create(alumno=alumno)
    if credentials.refresh_token:
        credencial.refresh_token = credentials.refresh_token
    credencial.access_token = credentials.token or ""
    credencial.expires_at = getattr(credentials, "expiry", None)
    credencial.scopes = " ".join(credentials.scopes or settings.GOOGLE_CALENDAR_SCOPES)
    credencial.revoked_at = None
    credencial.connected_at = now()
    credencial.save()
    return credencial


def _google_credentials(credencial):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=credencial.access_token or None,
        refresh_token=credencial.refresh_token or None,
        token_uri=_TOKEN_URI,
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=credencial.scopes.split() or settings.GOOGLE_CALENDAR_SCOPES,
    )


def _marcar_revocada(credencial) -> None:
    """El refresh token dejó de servir (revocado por el usuario o expirado):
    marca la credencial para que el portal ofrezca reconectar. A partir de acá
    `esta_conectada` es False, así que la sync deja de intentar."""
    credencial.revoked_at = now()
    credencial.save(update_fields=["revoked_at", "modificado"])


def get_calendar_service(credencial):
    """Cliente de la API de Calendar para ese alumno, refrescando el access
    token si venció (y persistiendo el nuevo). Si el refresh falla porque el
    token fue revocado, marca la credencial como revocada y propaga."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = _google_credentials(credencial)
    if not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            _marcar_revocada(credencial)
            raise
        credencial.access_token = creds.token or ""
        credencial.expires_at = getattr(creds, "expiry", None)
        credencial.save(update_fields=["access_token", "expires_at", "modificado"])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def asegurar_calendario_secundario(credencial) -> str:
    """Devuelve el id del calendario "Turnos de {gimnasio}" del alumno,
    creándolo la primera vez. Con `calendar.app.created` NO se puede escribir en
    el calendario principal: hay que usar uno creado por la app."""
    if credencial.google_calendar_id:
        return credencial.google_calendar_id

    summary = f"Turnos de {credencial.alumno.gimnasio.nombre}"
    service = get_calendar_service(credencial)
    creado = service.calendars().insert(
        body={"summary": summary, "timeZone": TZ}
    ).execute()
    credencial.google_calendar_id = creado["id"]
    credencial.google_calendar_summary = summary
    credencial.save(update_fields=["google_calendar_id", "google_calendar_summary", "modificado"])
    return credencial.google_calendar_id


def revocar(credencial) -> None:
    """Revoca el token en Google (best-effort)."""
    import requests

    token = credencial.refresh_token or credencial.access_token
    if not token:
        return
    try:
        requests.post(_REVOKE_URI, params={"token": token}, timeout=10)
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("No se pudo revocar el token de Google: %s", _sanitizar_error(exc))


def borrar_calendario_secundario(credencial) -> bool:
    """Borra el calendario secundario creado por la app (elimina todos sus
    eventos de una). Best-effort: devuelve False si no se pudo."""
    if not credencial.google_calendar_id:
        return True
    try:
        service = get_calendar_service(credencial)
        service.calendars().delete(calendarId=credencial.google_calendar_id).execute()
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning(
            "No se pudo borrar el calendario secundario: %s", _sanitizar_error(exc)
        )
        return False


# --------------------------------------------------------------------------- #
# Sync de eventos (C2)
# --------------------------------------------------------------------------- #

def _credencial_conectada(alumno):
    """La credencial conectada del alumno, o None si la integración está apagada,
    el alumno no conectó, o su credencial está revocada."""
    if not integracion_activa() or alumno is None:
        return None
    from calendario.models import GoogleCalendarCredential

    try:
        credencial = alumno.credencial_calendar
    except GoogleCalendarCredential.DoesNotExist:
        return None
    return credencial if credencial.esta_conectada else None


def _event_body(reserva) -> dict:
    from turnos.models import obtener_configuracion

    config = obtener_configuracion(reserva.gimnasio)
    inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
    fin = inicio + timedelta(minutes=config.duracion_minutos)
    return {
        "summary": f"Entrenamiento en {reserva.gimnasio.nombre}",
        "start": {"dateTime": inicio.isoformat(), "timeZone": TZ},
        "end": {"dateTime": fin.isoformat(), "timeZone": TZ},
        # Permite reconciliar el evento con la reserva si un signal corre dos
        # veces o hay que reintentar.
        "extendedProperties": {
            "private": {
                "reserva_id": str(reserva.pk),
                "gimnasio_id": str(reserva.gimnasio_id),
            }
        },
    }


def _marcar_ok(evento) -> None:
    from calendario.models import ReservaCalendarEvent

    evento.sync_status = ReservaCalendarEvent.SyncStatus.OK
    evento.last_synced_at = now()
    evento.last_error = ""
    evento.save(update_fields=["google_event_id", "sync_status", "last_synced_at", "last_error", "modificado"])


def _marcar_error(evento, exc) -> None:
    from calendario.models import ReservaCalendarEvent

    evento.sync_status = ReservaCalendarEvent.SyncStatus.ERROR
    evento.last_error = _sanitizar_error(exc)[:300]
    evento.save(update_fields=["sync_status", "last_error", "modificado"])
    logger.warning("Fallo al sincronizar evento de reserva %s: %s", evento.reserva_id, evento.last_error)


def crear_evento(reserva) -> None:
    """Crea el evento de la reserva en el calendario del alumno. Idempotente: si
    ya hay `google_event_id`, actualiza en vez de duplicar. Best-effort."""
    credencial = _credencial_conectada(reserva.alumno)
    if credencial is None:
        return
    from calendario.models import ReservaCalendarEvent

    evento, _ = ReservaCalendarEvent.objects.get_or_create(reserva=reserva)
    if evento.google_event_id:
        return actualizar_evento(reserva)
    try:
        service = get_calendar_service(credencial)
        cal_id = asegurar_calendario_secundario(credencial)
        creado = service.events().insert(
            calendarId=cal_id, body=_event_body(reserva)
        ).execute()
        evento.google_event_id = creado["id"]
        _marcar_ok(evento)
    except Exception as exc:  # noqa: BLE001 - best-effort
        _marcar_error(evento, exc)


def actualizar_evento(reserva) -> None:
    """Actualiza el evento de la reserva (p.ej. tras migrarla o cambiar la
    duración). Si todavía no hay evento, lo crea. Best-effort."""
    credencial = _credencial_conectada(reserva.alumno)
    if credencial is None:
        return
    from calendario.models import ReservaCalendarEvent

    try:
        evento = reserva.calendar_event
    except ReservaCalendarEvent.DoesNotExist:
        return crear_evento(reserva)
    if not evento.google_event_id:
        return crear_evento(reserva)
    try:
        service = get_calendar_service(credencial)
        cal_id = asegurar_calendario_secundario(credencial)
        service.events().update(
            calendarId=cal_id, eventId=evento.google_event_id, body=_event_body(reserva)
        ).execute()
        _marcar_ok(evento)
    except Exception as exc:  # noqa: BLE001 - best-effort
        _marcar_error(evento, exc)


def borrar_evento(credencial, google_event_id: str) -> None:
    """Borra un evento del calendario del alumno por id. Best-effort. Se le pasa
    el `google_event_id` porque al cancelar la reserva el `ReservaCalendarEvent`
    ya se borró por cascade (ver `pre_delete` en signals)."""
    if credencial is None or not google_event_id or not credencial.google_calendar_id:
        return
    try:
        service = get_calendar_service(credencial)
        service.events().delete(
            calendarId=credencial.google_calendar_id, eventId=google_event_id
        ).execute()
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("No se pudo borrar el evento de Calendar: %s", _sanitizar_error(exc))


def _reservas_futuras(reservas):
    # `_ahora_local()` de turnos devuelve la hora local naive, comparable con
    # `datetime.combine(fecha, hora)` (mismo criterio que la reconciliación).
    from turnos.services import _ahora_local

    ahora = _ahora_local()
    for reserva in reservas:
        if datetime.combine(reserva.fecha, reserva.hora_inicio) < ahora:
            continue
        yield reserva


def sincronizar_reservas_futuras(alumno) -> None:
    """Backfill: crea los eventos de las reservas futuras que YA existían cuando
    el alumno conectó su cuenta (o cuando pide reintentar). Best-effort."""
    if _credencial_conectada(alumno) is None:
        return
    for reserva in _reservas_futuras(alumno.reservas.all()):
        crear_evento(reserva)


def resync_duracion(gimnasio) -> None:
    """Cuando el staff cambia `ConfiguracionTurnos.duracion_minutos`, actualiza el
    `dtend` de los eventos futuros ya sincronizados del gimnasio. Best-effort."""
    from calendario.models import ReservaCalendarEvent
    from turnos.models import Reserva

    ids_con_evento = set(
        ReservaCalendarEvent.objects.exclude(google_event_id="").values_list(
            "reserva_id", flat=True
        )
    )
    reservas = Reserva.objects.for_gimnasio(gimnasio).filter(pk__in=ids_con_evento)
    for reserva in _reservas_futuras(reservas):
        actualizar_evento(reserva)
