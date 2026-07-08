"""Vistas de la integración con Google Calendar (Parte C).

Todas son del alumno (`AlumnoRequiredMixin`): conectar su cuenta, el callback de
OAuth, desconectar y reintentar la sync. Si la integración global no está
configurada (`GOOGLE_CALENDAR_ENABLED`), conectar redirige con un aviso y el
portal sigue mostrando solo el deep-link.
"""

import logging
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.timezone import now
from django.views.generic import View

from tenants.mixins import AlumnoRequiredMixin
from calendario import services

logger = logging.getLogger(__name__)

_STATE_KEY = "calendario_oauth_state"
_STATE_TS_KEY = "calendario_oauth_state_ts"
_VERIFIER_KEY = "calendario_oauth_verifier"  # code_verifier de PKCE (ver services)
_STATE_MAX_SEGUNDOS = 600  # el state en sesión vale 10 minutos


class ConectarCalendarioView(AlumnoRequiredMixin, View):
    """Arranca el flujo OAuth: guarda el `state` en sesión y manda a Google."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        if not services.integracion_activa():
            messages.info(request, "La integración con Google Calendar no está disponible.")
            return redirect("turnos:mis_turnos")

        url, state, verifier = services.build_authorization_url()
        request.session[_STATE_KEY] = state
        request.session[_STATE_TS_KEY] = now().isoformat()
        request.session[_VERIFIER_KEY] = verifier
        return redirect(url)


class CalendarioCallbackView(AlumnoRequiredMixin, View):
    """Callback de Google: valida el `state`, intercambia el code, guarda la
    credencial, provisiona el calendario secundario y hace el backfill de las
    reservas futuras ya existentes."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")

        if request.GET.get("error"):
            messages.warning(request, "No se conectó Google Calendar (permiso cancelado).")
            return redirect("turnos:mis_turnos")

        if not self._state_valido(request):
            messages.error(request, "La conexión con Google Calendar expiró o es inválida. Probá de nuevo.")
            return redirect("turnos:mis_turnos")

        code = request.GET.get("code")
        state = request.GET.get("state")
        verifier = request.session.pop(_VERIFIER_KEY, None)
        if not code:
            messages.error(request, "Google no devolvió el código de autorización.")
            return redirect("turnos:mis_turnos")

        try:
            credentials = services.intercambiar_code(code, state, verifier)
            credencial = services.guardar_credencial(self.alumno, credentials)
        except Exception as exc:  # noqa: BLE001 - best-effort, no romper el portal
            logger.warning("Fallo conectando Google Calendar: %s", services._sanitizar_error(exc))
            messages.error(request, "No se pudo conectar Google Calendar. Probá de nuevo.")
            return redirect("turnos:mis_turnos")

        # Sin refresh token (ni nuevo ni previo) no se puede refrescar después:
        # `esta_conectada` sería False. Abortamos con un mensaje claro en vez de
        # dejar una credencial inútil y hacer un backfill que no haría nada.
        if not credencial.esta_conectada:
            credencial.delete()
            messages.error(
                request,
                "Google no otorgó acceso sin conexión. Revocá el acceso de la app "
                "en tu cuenta de Google y volvé a conectar.",
            )
            return redirect("turnos:mis_turnos")

        # Provisión del calendario + backfill: best-effort, no revierten la
        # conexión (la credencial ya es válida; la sync se puede reintentar).
        try:
            services.asegurar_calendario_secundario(credencial)
            services.sincronizar_reservas_futuras(self.alumno)
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("Conectado, pero falló el backfill inicial: %s", services._sanitizar_error(exc))

        messages.success(request, "¡Listo! Tus turnos se van a sincronizar con tu Google Calendar.")
        return redirect("turnos:mis_turnos")

    def _state_valido(self, request) -> bool:
        esperado = request.session.pop(_STATE_KEY, None)
        ts = request.session.pop(_STATE_TS_KEY, None)
        recibido = request.GET.get("state")
        if not esperado or not recibido or esperado != recibido or not ts:
            return False
        try:
            emitido = datetime.fromisoformat(ts)
        except ValueError:
            return False
        return (now() - emitido).total_seconds() <= _STATE_MAX_SEGUNDOS


class DesconectarCalendarioView(AlumnoRequiredMixin, View):
    """Desconecta: borra el calendario secundario, revoca el token y limpia lo
    local. Best-effort: si el borrado remoto falla, igual limpia local y avisa."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        from calendario.models import GoogleCalendarCredential, ReservaCalendarEvent

        try:
            credencial = self.alumno.credencial_calendar
        except GoogleCalendarCredential.DoesNotExist:
            return redirect("turnos:mis_turnos")

        borrado_ok = services.borrar_calendario_secundario(credencial)
        services.revocar(credencial)
        credencial.delete()
        # `ReservaCalendarEvent` cuelga de `Reserva`, no de la credencial: se
        # limpia aparte para no dejar ids de eventos ya inexistentes.
        ReservaCalendarEvent.objects.filter(reserva__alumno=self.alumno).delete()

        if borrado_ok:
            messages.success(request, "Desconectaste Google Calendar. Se eliminó el calendario 'Turnos'.")
        else:
            messages.warning(
                request,
                "Te desconectamos, pero no pudimos borrar el calendario remoto; "
                "podés eliminarlo a mano desde Google Calendar.",
            )
        return redirect("turnos:mis_turnos")


class ReintentarSyncView(AlumnoRequiredMixin, View):
    """Reintenta sincronizar las reservas futuras (las que quedaron en error)."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        services.sincronizar_reservas_futuras(self.alumno)
        messages.info(request, "Reintentamos sincronizar tus turnos con Google Calendar.")
        return redirect("turnos:mis_turnos")
