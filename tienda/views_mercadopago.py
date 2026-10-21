"""Conectar / desconectar la cuenta de Mercado Pago del gimnasio (OAuth + PKCE).

Todo es del staff, fuera de la cuenta demo y sólo con
`settings.TIENDA_MP_ENABLED`. **No exige el add-on de la tienda**: la misma
cuenta cobra las cuotas, y cualquier gimnasio puede conectarla desde «Mi
gimnasio». Los errores son mensaje + redirect a donde arrancó el flujo
(`_destino`), nunca un 4xx: bajo `hx-boost` sería un click mudo.

La Redirect URI sigue siendo `/tienda/mercadopago/callback/` (está cargada en
la app de Mercado Pago): sólo cambia a dónde se vuelve después.

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
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.mixins import TenantScopedMixin
from notificaciones import services as avisos
from pagos.models import CobroOnlineCuota
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto, cuerpo_de_webhook, es_de_prueba
from tenants.mixins import StaffRequiredMixin
from tienda import cuenta_mp, mercadopago_oauth, pagos_mp, services
from tienda.models import ESTADOS_QUE_RETIENEN, ConfiguracionTienda, CuentaMercadoPago, Pedido, obtener_configuracion

logger = logging.getLogger(__name__)

_SALT = "tienda.mercadopago.oauth"
_SESION_NONCE = "tienda_mp_nonce"
_SESION_VERIFIER = "tienda_mp_verifier"
_SESION_TS = "tienda_mp_ts"
#: «El OAuth arrancó desde Mi gimnasio»: el callback vuelve ahí.
_SESION_DESDE = "tienda_mp_desde"
_DESDE_GIMNASIO = "gimnasio"
_STATE_MAX_SEGUNDOS = 600

#: Un cambio de cuenta se rechaza si hay pedidos de Mercado Pago de la cuenta
#: vieja vivos o de esta ventana: sus webhooks y reintegros todavía importan.
VENTANA_CAMBIO_DE_CUENTA = timedelta(days=7)


def _nombre(usuario):
    return usuario.get_full_name() or usuario.get_username()


def _destino(request, gimnasio, *, desde_gimnasio=False):
    """A dónde vuelve el flujo de la cuenta: a «Mi gimnasio» si arrancó desde
    ahí o si el gimnasio no tiene la tienda (la configuración de la tienda le
    mostraría el cartel del add-on); si no, a la configuración de la tienda."""
    if desde_gimnasio:
        return reverse("gimnasio_editar") + "#mercadopago"
    return cuenta_mp.url_cuenta_mp(gimnasio)


class _MercadoPagoStaffView(StaffRequiredMixin, TenantScopedMixin, View):
    """Sin `TiendaHabilitadaMixin`: conectar la cuenta no depende del add-on."""

    def _desde_gimnasio(self):
        return False

    def _destino(self):
        return redirect(
            _destino(self.request, self.gimnasio, desde_gimnasio=self._desde_gimnasio())
        )

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
        return self._destino()


class ConectarView(_MercadoPagoStaffView):
    """Arranca el OAuth: guarda nonce + verifier + hora en la sesión y manda a
    Mercado Pago a autorizar. `?desde=gimnasio` (el botón de «Mi gimnasio»)
    queda en la sesión para que el callback vuelva ahí."""

    http_method_names = ["get"]

    def _desde_gimnasio(self):
        return self.request.GET.get("desde") == _DESDE_GIMNASIO

    def get(self, request, *args, **kwargs):
        rechazo = self._no_disponible()
        if rechazo:
            return rechazo
        if self._desde_gimnasio():
            request.session[_SESION_DESDE] = _DESDE_GIMNASIO
        else:
            request.session.pop(_SESION_DESDE, None)
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
    """Vuelta de Mercado Pago. Siempre termina donde arrancó el flujo
    (`_destino`)."""

    http_method_names = ["get"]

    _vino_desde_gimnasio = False

    def _desde_gimnasio(self):
        return self._vino_desde_gimnasio

    def _volver(self, nivel, texto):
        nivel(self.request, texto)
        return self._destino()

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
        # Se consume siempre (una sola vez, como el nonce).
        self._vino_desde_gimnasio = (
            request.session.pop(_SESION_DESDE, None) == _DESDE_GIMNASIO
        )
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

        # Un link ya vencido que el cron todavía no cerró no debe trabar el
        # cambio: se vence antes de mirar (mismo criterio que el catálogo).
        services.vencer_pedidos(gimnasio=self.gimnasio)
        with transaction.atomic():
            previa = CuentaMercadoPago.objects.filter(gimnasio=self.gimnasio).first()
            if previa is not None and previa.mp_user_id and previa.mp_user_id != mp_user_id:
                # Los recientes traban porque un reintegro o contracargo
                # volvería por la cuenta vieja; uno vencido o cancelado que
                # nunca tuvo pago (una compra abandonada) no tiene nada que
                # pueda volver.
                nunca_pagado = Q(
                    estado__in=(Pedido.Estado.VENCIDO, Pedido.Estado.CANCELADO), payment_id=""
                )
                hay_pedidos = Pedido.objects.filter(
                    gimnasio=self.gimnasio, medio=Pedido.Medio.MERCADOPAGO
                ).filter(
                    Q(estado__in=ESTADOS_QUE_RETIENEN + (Pedido.Estado.REVISAR,))
                    | (Q(creado__gte=ahora - VENTANA_CAMBIO_DE_CUENTA) & ~nunca_pagado)
                ).exists()
                if hay_pedidos:
                    return self._volver(
                        messages.error,
                        "Esa es otra cuenta de Mercado Pago y todavía hay pedidos de "
                        "Mercado Pago sin cerrar, o con pago en los últimos 7 días, con "
                        "la cuenta anterior (las compras abandonadas no cuentan). "
                        "Terminá o cancelá esos pedidos antes de cambiar de cuenta.",
                    )
                freno = _freno_por_cuotas(self.gimnasio, ahora)
                if freno:
                    return self._volver(messages.error, freno)
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


#: Un link de cuota que MP todavía cobra. CANCELADO y RECHAZADO también:
#: cancelar el intento (desconectar, pagarla a mano, corregir el monto) no
#: vence la preferencia en MP, y un pago rechazado deja reintentar con el mismo
#: link. Sin `preference_id` no hay link que pagar.
def _q_link_de_cuota_pagable(ahora):
    E = CobroOnlineCuota.Estado
    return (
        Q(estado__in=(E.CREADO, E.CANCELADO, E.RECHAZADO))
        & ~Q(preference_id="")
        & (Q(expira__isnull=True) | Q(expira__gt=ahora))
    )


def _freno_por_cuotas(gimnasio, ahora):
    """El mensaje que frena el cambio a OTRA cuenta de MP por los cobros de
    cuotas de la cuenta vieja, o `None` si no hay nada que lo trabe.

    Mismo criterio que los pedidos: un link todavía pagable cobraría a la
    cuenta vieja (y el webhook, leyendo con el token nuevo, recibiría 404; la
    reconciliación lo saltea por `mp_user_id`: la cuota quedaría impaga con la
    plata cobrada); una revisión pendiente o un cobro de los últimos 7 días
    puede volver como reintegro o contracargo por esa cuenta, y con otra
    conectada nadie lo vería. Un link ya vencido (aunque siga CREADO) no
    traba."""
    E = CobroOnlineCuota.Estado
    intentos = CobroOnlineCuota.objects.filter(gimnasio=gimnasio)
    links = intentos.filter(_q_link_de_cuota_pagable(ahora)).aggregate(
        total=Count("pk"),
        sin_vencimiento=Count("pk", filter=Q(expira__isnull=True)),
        hasta=Max("expira"),
    )
    if links["total"]:
        if links["sin_vencimiento"] or links["hasta"] is None:
            espera = "esperá a que venza"
        else:
            espera = f"esperá hasta las {timezone.localtime(links['hasta']):%H:%M}"
        return (
            "Esa es otra cuenta de Mercado Pago y hay un link de pago de cuota de "
            "la cuenta anterior que todavía se puede pagar; "
            f"{espera} y probá de nuevo."
        )
    if intentos.filter(
        Q(estado__in=(E.REVISAR, E.DUPLICADO, E.DEVUELTO), revision_pendiente=True)
        | Q(
            estado__in=(E.PAGADO, E.DUPLICADO),
            acreditado_en__gte=ahora - VENTANA_CAMBIO_DE_CUENTA,
        )
    ).exists():
        return (
            "Esa es otra cuenta de Mercado Pago y todavía hay cobros de cuotas "
            "para revisar o cuotas cobradas por Mercado Pago en los últimos 7 "
            "días con la cuenta anterior. Resolvé lo pendiente y probá de nuevo "
            "pasados 7 días del último cobro."
        )
    return None


def _avisar_conexion(gimnasio, quien):
    try:
        avisos.notificar_cuenta_mp(gimnasio, "conectada", quien)
    except Exception:  # noqa: BLE001 - un push caído no deshace la conexión
        logger.exception("No se pudo avisar la conexión de Mercado Pago")


class DesconectarView(_MercadoPagoStaffView):
    """Desconecta (POST). Con pedidos de Mercado Pago esperando el pago se
    rechaza: quedarían con un link que cobra a una cuenta que ya no se mira.
    Los links de cuotas vivos NO frenan: se cancelan (el alumno vuelve a ver
    «subí el comprobante»; un pago ya empezado igual se acredita).
    `desde=gimnasio` en el POST vuelve a «Mi gimnasio».

    Los tokens se conservan 7 días (pagos tardíos de pedidos vencidos siguen
    verificables); `refrescar_cuentas_mp` los borra pasado el plazo. Nunca
    queda la tienda sin medio: se vuelve a aceptar transferencia."""

    http_method_names = ["post"]

    def _desde_gimnasio(self):
        return self.request.POST.get("desde") == _DESDE_GIMNASIO

    def post(self, request, *args, **kwargs):
        rechazo = self._no_disponible()
        if rechazo:
            return rechazo
        # Un link ya vencido que el cron todavía no cerró no es un pedido
        # «esperando el pago»: se vence antes de mirar.
        services.vencer_pedidos(gimnasio=self.gimnasio)
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
                return self._destino()
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
            # Sin lock de la cuota a propósito: es un UPDATE sobre los
            # intentos solos (no toma cuota -> intentos al revés).
            CobroOnlineCuota.objects.filter(
                gimnasio=self.gimnasio, estado=CobroOnlineCuota.Estado.CREADO
            ).update(estado=CobroOnlineCuota.Estado.CANCELADO, modificado=timezone.now())
        if cambiadas:
            messages.success(request, "Mercado Pago quedó desconectado.")
        else:
            messages.info(request, "No había una cuenta de Mercado Pago conectada.")
        return self._destino()


@csrf_exempt
@require_POST
def webhook(request, uuid_webhook):
    """Notificación de Mercado Pago de que cambió un pago de la tienda de UN
    gimnasio (el `uuid_webhook` de su cuenta va en la `notification_url` de
    cada preferencia).

    `csrf_exempt` porque Mercado Pago no tiene sesión: **la vista nunca lee
    `request.user`** (un test lo fija). Anónima, `PlataformaMiddleware` la deja
    pasar. La autenticidad sale de la API: el pago se lee con el token de ESE
    gimnasio y tiene que traer la referencia de un pedido suyo, con el monto y
    en ARS. Si en la prueba guiada las notificaciones de un vendedor OAuth
    llegan firmadas, la firma se agrega OBLIGATORIA en un cambio aparte.

    | Caso                                              | Respuesta |
    |---------------------------------------------------|-----------|
    | Bandera apagada                                   | 503 (MP reintenta hasta que se prenda) |
    | Cuenta inexistente                                | 200 «ignorado» |
    | Cuenta sin tokens (ilegibles, o desconectada > 7 días) | 200 «ignorado», sin llamar a MP |
    | Tipo distinto de `payment` o sin `data.id`        | 200 «ignorado» |
    | MP responde 404 o 403 al leer el pago             | 200 (reintentar no lo arregla) |
    | MP responde 401                                   | cuenta a RECONECTAR (condicional) + 200 |
    | Otro error de MP o una carrera repetida           | 500 (MP reintenta a los 15 min) |

    Una cuenta DESCONECTADA hace menos de 7 días todavía tiene tokens: se
    procesa igual (pagos tardíos de pedidos vencidos).
    """
    if not settings.TIENDA_MP_ENABLED:
        return JsonResponse({"error": "integracion apagada"}, status=503)

    cuenta = CuentaMercadoPago.objects.filter(uuid_webhook=uuid_webhook).first()
    if cuenta is None or not cuenta.access_token:
        return JsonResponse({"ok": True, "accion": "ignorado"})

    cuerpo = cuerpo_de_webhook(request.body)
    data_id = request.GET.get("data.id") or request.GET.get("id")
    if not data_id:
        datos = cuerpo.get("data")
        if isinstance(datos, dict) and datos.get("id") is not None:
            data_id = str(datos["id"])
    tipo = request.GET.get("type") or request.GET.get("topic") or cuerpo.get("type")
    if tipo != "payment" or not data_id:
        return JsonResponse({"ok": True, "accion": "ignorado"})

    try:
        pagos_mp.acreditar_pedido(cuenta, data_id)
    except ErrorDeMercadoPago as error:
        if error.codigo in (403, 404):
            logger.warning(
                "Webhook de la tienda: Mercado Pago no deja leer el pago %s (%s); lo ignoro.",
                data_id, error.codigo,
            )
            return JsonResponse({"ok": True, "accion": "pago inexistente"})
        if error.codigo == 401:
            cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira)
            logger.warning(
                "Webhook de la tienda: token rechazado (401) para la cuenta %s.", cuenta.pk
            )
            return JsonResponse({"ok": True, "accion": "reconectar"})
        logger.warning(
            "Webhook de la tienda: Mercado Pago no respondió por el pago %s (codigo=%s); "
            "respondo 500 para que reintente.",
            data_id, error.codigo,
        )
        return JsonResponse({"error": "reintentar"}, status=500)
    except pagos_mp.AcreditacionReintentable:
        logger.warning("Webhook de la tienda: carrera al acreditar el pago %s; reintentar.", data_id)
        return JsonResponse({"error": "reintentar"}, status=500)
    return JsonResponse({"ok": True})
