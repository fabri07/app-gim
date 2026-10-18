"""Conectar / desconectar la cuenta de Mercado Pago del gimnasio (OAuth + PKCE).

Todo es del staff con la tienda habilitada, fuera de la cuenta demo y sólo con
`settings.TIENDA_MP_ENABLED`. Los errores son mensaje + redirect a la
configuración, nunca un 4xx: bajo `hx-boost` sería un click mudo.

El `state` es un valor firmado (gimnasio, usuario, nonce) y además el nonce, el
`code_verifier` y la hora viven en la sesión: el callback sólo vale para el
mismo usuario, el mismo gimnasio, dentro de 10 minutos y una sola vez.
Molde: `calendario/views.py::_state_valido`.
"""

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from notificaciones import services as avisos
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto, es_de_prueba
from tienda import mercadopago_oauth
from tienda.models import ESTADOS_QUE_RETIENEN, ConfiguracionTienda, CuentaMercadoPago, Pedido, obtener_configuracion
from tienda.views import TiendaStaffMixin

logger = logging.getLogger(__name__)

_SALT = "tienda.mercadopago.oauth"
_SESION_NONCE = "tienda_mp_nonce"
_SESION_VERIFIER = "tienda_mp_verifier"
_SESION_TS = "tienda_mp_ts"
_STATE_MAX_SEGUNDOS = 600

#: Un cambio de cuenta se rechaza si hay pedidos de Mercado Pago de la cuenta
#: vieja vivos o de esta ventana: sus webhooks y reintegros todavía importan.
VENTANA_CAMBIO_DE_CUENTA = timedelta(days=7)


def _nombre(usuario):
    return usuario.get_full_name() or usuario.get_username()


class _MercadoPagoStaffView(TiendaStaffMixin, View):
    def _no_disponible(self):
        """Mensaje + redirect si no se puede tocar Mercado Pago ahora."""
        if not settings.TIENDA_MP_ENABLED:
            messages.info(self.request, "Mercado Pago no está disponible por ahora.")
        elif self.gimnasio.es_demo:
            messages.info(
                self.request,
                "En la cuenta de demostración no se conecta Mercado Pago.",
            )
        else:
            return None
        return redirect("tienda:configuracion")


class ConectarView(_MercadoPagoStaffView):
    """Arranca el OAuth: guarda nonce + verifier + hora en la sesión y manda a
    Mercado Pago a autorizar."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        rechazo = self._no_disponible()
        if rechazo:
            return rechazo
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        nonce = secrets.token_urlsafe(16)
        state = signing.dumps(
            {"g": self.gimnasio.pk, "u": request.user.pk, "n": nonce}, salt=_SALT
        )
        request.session[_SESION_NONCE] = nonce
        request.session[_SESION_VERIFIER] = verifier
        request.session[_SESION_TS] = timezone.now().isoformat()
        return redirect(mercadopago_oauth.url_de_autorizacion(state, challenge))


class CallbackView(_MercadoPagoStaffView):
    """Vuelta de Mercado Pago. Siempre termina en la configuración."""

    http_method_names = ["get"]

    def _volver(self, nivel, texto):
        nivel(self.request, texto)
        return redirect("tienda:configuracion")

    def _state_valido(self, request):
        """Consume la sesión (una sola vez, válido o no)."""
        nonce = request.session.pop(_SESION_NONCE, None)
        ts = request.session.get(_SESION_TS)
        request.session.pop(_SESION_TS, None)
        recibido = request.GET.get("state")
        if not nonce or not recibido or not ts:
            return False
        try:
            datos = signing.loads(recibido, salt=_SALT, max_age=_STATE_MAX_SEGUNDOS)
            emitido = datetime.fromisoformat(ts)
        except (signing.BadSignature, ValueError):
            return False
        if (timezone.now() - emitido).total_seconds() > _STATE_MAX_SEGUNDOS:
            return False
        return (
            datos.get("g") == self.gimnasio.pk
            and datos.get("u") == request.user.pk
            and secrets.compare_digest(str(datos.get("n")), nonce)
        )

    def get(self, request, *args, **kwargs):
        rechazo = self._no_disponible()
        if rechazo:
            return rechazo

        if request.GET.get("error"):
            for clave in (_SESION_NONCE, _SESION_VERIFIER, _SESION_TS):
                request.session.pop(clave, None)
            return self._volver(
                messages.warning,
                "No se conectó Mercado Pago (cancelaste la autorización).",
            )

        verifier = request.session.get(_SESION_VERIFIER)
        valido = self._state_valido(request)
        request.session.pop(_SESION_VERIFIER, None)
        if not valido or not verifier:
            return self._volver(
                messages.error,
                "La conexión con Mercado Pago expiró o es inválida. Probá de nuevo.",
            )
        code = request.GET.get("code")
        if not code:
            return self._volver(
                messages.error, "Mercado Pago no devolvió el código de autorización."
            )

        try:
            datos = mercadopago_oauth.canjear_codigo(code, verifier)
        except ErrorDeMercadoPago as exc:
            logger.warning(
                "Falló el canje OAuth de Mercado Pago (codigo=%s)", exc.codigo
            )
            return self._volver(
                messages.error, "No se pudo conectar Mercado Pago. Probá de nuevo."
            )

        mp_user_id = str(datos.get("user_id") or "")
        if not datos.get("access_token") or not datos.get("refresh_token") or not mp_user_id:
            # Sin refresh token no se puede renovar después (depende de
            # `offline_access` en la app de Mercado Pago): se rechaza antes de
            # guardar una conexión que vence sola.
            logger.warning("Mercado Pago no devolvió los tokens o el user_id al conectar")
            return self._volver(
                messages.error,
                "Mercado Pago no otorgó acceso sin conexión. Revisá que tu cuenta "
                "autorice a la aplicación y volvé a conectar.",
            )
        try:
            vigencia = int(datos["expires_in"])
        except (KeyError, TypeError, ValueError):
            vigencia = 15552000  # 180 días, lo habitual de Mercado Pago
        ahora = timezone.now()

        with transaction.atomic():
            previa = CuentaMercadoPago.objects.filter(gimnasio=self.gimnasio).first()
            if previa is not None and previa.mp_user_id and previa.mp_user_id != mp_user_id:
                hay_pedidos = Pedido.objects.filter(
                    gimnasio=self.gimnasio, medio=Pedido.Medio.MERCADOPAGO
                ).filter(
                    Q(estado__in=ESTADOS_QUE_RETIENEN + (Pedido.Estado.REVISAR,))
                    | Q(creado__gte=ahora - VENTANA_CAMBIO_DE_CUENTA)
                ).exists()
                if hay_pedidos:
                    return self._volver(
                        messages.error,
                        "Esa es otra cuenta de Mercado Pago y todavía hay pedidos de "
                        "Mercado Pago sin cerrar o recientes con la cuenta anterior. "
                        "Terminá o cancelá esos pedidos antes de cambiar de cuenta.",
                    )
            CuentaMercadoPago.objects.update_or_create(
                gimnasio=self.gimnasio,
                defaults={
                    "access_token": datos["access_token"],
                    "refresh_token": datos["refresh_token"],
                    "mp_user_id": mp_user_id,
                    "token_expira": ahora + timedelta(seconds=vigencia),
                    "es_de_prueba": datos.get("live_mode") is False
                    or es_de_prueba(Secreto(datos["access_token"])),
                    "estado": CuentaMercadoPago.Estado.CONECTADA,
                    "conectada_por": request.user,
                    "conectada_en": ahora,
                    "desconectada_en": None,
                },
            )
            gimnasio, quien = self.gimnasio, _nombre(request.user)
            transaction.on_commit(
                lambda: _avisar_conexion(gimnasio, quien)
            )
        return self._volver(messages.success, "Mercado Pago quedó conectado.")


def _avisar_conexion(gimnasio, quien):
    try:
        avisos.notificar_cuenta_mp(gimnasio, "conectada", quien)
    except Exception:  # noqa: BLE001 - un push caído no deshace la conexión
        logger.exception("No se pudo avisar la conexión de Mercado Pago")


class DesconectarView(_MercadoPagoStaffView):
    """Desconecta (POST). Con pedidos de Mercado Pago esperando el pago se
    rechaza: quedarían con un link que cobra a una cuenta que ya no se mira.

    Los tokens se conservan 7 días (pagos tardíos de pedidos vencidos siguen
    verificables); `refrescar_cuentas_mp` los borra pasado el plazo. Nunca
    queda la tienda sin medio: se vuelve a aceptar transferencia."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        rechazo = self._no_disponible()
        if rechazo:
            return rechazo
        with transaction.atomic():
            if Pedido.objects.filter(
                gimnasio=self.gimnasio,
                medio=Pedido.Medio.MERCADOPAGO,
                estado=Pedido.Estado.PENDIENTE_PAGO,
            ).exists():
                messages.error(
                    request,
                    "Hay pedidos esperando el pago por Mercado Pago. Esperá a que "
                    "se paguen o venzan antes de desconectar la cuenta.",
                )
                return redirect("tienda:configuracion")
            # `update()` y no `save()`: no toca las columnas de tokens (que
            # pueden ser ilegibles) ni `modificado` de nada que no corresponda.
            cambiadas = CuentaMercadoPago.objects.filter(
                gimnasio=self.gimnasio
            ).exclude(estado=CuentaMercadoPago.Estado.DESCONECTADA).update(
                estado=CuentaMercadoPago.Estado.DESCONECTADA,
                desconectada_en=timezone.now(),
            )
            obtener_configuracion(self.gimnasio)  # garantiza la fila
            ConfiguracionTienda.objects.filter(gimnasio=self.gimnasio).update(
                acepta_mercadopago=False, acepta_transferencia=True
            )
        if cambiadas:
            messages.success(request, "Mercado Pago quedó desconectado.")
        else:
            messages.info(request, "No había una cuenta de Mercado Pago conectada.")
        return redirect("tienda:configuracion")


@csrf_exempt
def webhook(request, uuid_webhook):
    """Webhook de los pagos de la tienda: la URL ya va en cada preferencia
    (`notification_url`), así que la ruta existe desde B4.

    TODO B5: procesar la notificación (§5 del plan: 503 con la bandera
    apagada, 200 «ignorado», `acreditar_pedido`, 401 -> RECONECTAR). Hasta
    entonces responde 503 y Mercado Pago reintenta; la bandera está apagada en
    producción, así que no se crea ninguna preferencia que apunte acá."""
    return HttpResponse("no disponible", status=503, content_type="text/plain")
