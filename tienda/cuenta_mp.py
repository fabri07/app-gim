"""Ciclo de vida de la cuenta de Mercado Pago del gimnasio: refresco de los
tokens, paso a «reconectar» y borrado de los tokens de una cuenta desconectada.

Los tokens nunca se loguean ni se mezclan en un mensaje. Un token que no se
descifra (clave rotada o ausente) llega como `None`: la cuenta se trata como
«hay que reconectar» sin llamar a Mercado Pago, y NUNCA se guarda con `save()`
completo (escribiría `None` en las columnas de tokens): todo va con
`update()` sobre las columnas que se cambian.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from notificaciones import services as avisos
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto
from tienda import mercadopago_oauth
from tienda.models import CuentaMercadoPago

logger = logging.getLogger(__name__)

#: Cuánto se conservan los tokens de una cuenta desconectada: los pagos tardíos
#: de pedidos vencidos siguen verificables con `reconciliar_pedidos`.
PLAZO_TOKENS_DESCONECTADA = timedelta(days=7)
#: Se refrescan las cuentas a las que les quedan menos de 30 días de token.
VENTANA_REFRESCO = timedelta(days=30)


def url_cuenta_mp(gimnasio):
    """Dónde se administra la cuenta de Mercado Pago de ESTE gimnasio: la
    configuración de la tienda si tiene el add-on, «Mi gimnasio» si no (la
    tienda le mostraría el cartel del add-on). La usan las vistas de conexión
    y el push de `notificar_cuenta_mp`."""
    if gimnasio.puede_usar_tienda:
        return reverse("tienda:configuracion")
    return reverse("gimnasio_editar") + "#mercadopago"


def _sanitizar(exc):
    """Tipo + código HTTP, nunca el mensaje de la API ni el cuerpo."""
    return f"{type(exc).__name__} (codigo={getattr(exc, 'codigo', None)})"


def borrar_tokens_vencidos(ahora=None):
    """Borra los tokens de las desconectadas hace más de 7 días. No necesita la
    clave de cifrado (escribe vacíos), así que corre aunque la bandera esté
    apagada. Devuelve cuántas cuentas tocó."""
    ahora = ahora or timezone.now()
    return (
        CuentaMercadoPago.objects.filter(
            estado=CuentaMercadoPago.Estado.DESCONECTADA,
            desconectada_en__lt=ahora - PLAZO_TOKENS_DESCONECTADA,
        )
        .exclude(access_token="", refresh_token="")
        .update(access_token="", refresh_token="")
    )


def _avisar_reconectar(gimnasio_id):
    from tenants.models import Gimnasio

    try:
        avisos.notificar_cuenta_mp(Gimnasio.objects.get(pk=gimnasio_id), "reconectar")
    except Exception:  # noqa: BLE001 - un push caído no frena el refresco
        logger.exception("No se pudo avisar que hay que reconectar Mercado Pago")


def marcar_reconectar(cuenta_pk, token_expira_leido):
    """CONECTADA -> RECONECTAR con UPDATE condicional al `token_expira` que se
    leyó: si otro proceso la refrescó en el medio (token_expira nuevo), no se
    pisa una cuenta sana. Devuelve si cambió algo y, en ese caso, avisa al
    staff (los avisos nunca rompen al que llama)."""
    cuentas = CuentaMercadoPago.objects.filter(
        pk=cuenta_pk,
        estado=CuentaMercadoPago.Estado.CONECTADA,
        token_expira=token_expira_leido,
    )
    # `values_list` y no la instancia: no descifra los tokens.
    gimnasio_id = cuentas.values_list("gimnasio_id", flat=True).first()
    cambiadas = cuentas.update(estado=CuentaMercadoPago.Estado.RECONECTAR)
    if cambiadas:
        transaction.on_commit(lambda: _avisar_reconectar(gimnasio_id))
    return bool(cambiadas)


def refrescar_cuentas(ahora=None):
    """Refresca los tokens de las CONECTADAS que vencen en menos de 30 días.

    El refresh token es de UN SOLO USO: por cuenta se toma `select_for_update`
    y se relee tras el lock (otro proceso pudo refrescarla), y el token nuevo
    se guarda en el mismo paso. 400/401 o un token ilegible -> RECONECTAR + aviso
    al staff; otro error (red, 5xx) se cuenta y se reintenta en la próxima
    corrida. Devuelve `{"refrescadas", "reconectar", "errores"}`."""
    ahora = ahora or timezone.now()
    limite = ahora + VENTANA_REFRESCO
    resultado = {"refrescadas": 0, "reconectar": 0, "errores": 0}
    pks = list(
        CuentaMercadoPago.objects.filter(estado=CuentaMercadoPago.Estado.CONECTADA)
        .filter(Q(token_expira__lt=limite) | Q(token_expira__isnull=True))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for pk in pks:
        try:
            with transaction.atomic():
                cuenta = CuentaMercadoPago.objects.select_for_update().get(pk=pk)
                if cuenta.estado != CuentaMercadoPago.Estado.CONECTADA:
                    continue
                if cuenta.token_expira is not None and cuenta.token_expira >= limite:
                    continue  # otro proceso ya la refrescó
                leido = cuenta.token_expira
                if not cuenta.refresh_token:
                    # Ilegible (clave rotada) o vacío: sin llamar a Mercado Pago.
                    if marcar_reconectar(pk, leido):
                        resultado["reconectar"] += 1
                    continue
                try:
                    datos = mercadopago_oauth.refrescar(Secreto(cuenta.refresh_token))
                    nuevo_acceso = datos.get("access_token")
                    nuevo_refresh = datos.get("refresh_token")
                    vigencia = int(datos["expires_in"])
                except (ErrorDeMercadoPago, KeyError, TypeError, ValueError) as exc:
                    if isinstance(exc, ErrorDeMercadoPago) and exc.codigo in (400, 401):
                        if marcar_reconectar(pk, leido):
                            resultado["reconectar"] += 1
                    else:
                        logger.warning(
                            "No se pudo refrescar la cuenta de Mercado Pago %s: %s",
                            pk, _sanitizar(exc),
                        )
                        resultado["errores"] += 1
                    continue
                if not nuevo_acceso or not nuevo_refresh:
                    logger.warning(
                        "Mercado Pago no devolvió tokens al refrescar la cuenta %s", pk
                    )
                    resultado["errores"] += 1
                    continue
                CuentaMercadoPago.objects.filter(pk=pk).update(
                    access_token=nuevo_acceso,
                    refresh_token=nuevo_refresh,
                    token_expira=timezone.now() + timedelta(seconds=vigencia),
                )
                resultado["refrescadas"] += 1
        except Exception as exc:  # noqa: BLE001 - una cuenta rota no frena a las demás
            logger.error("Falló el refresco de la cuenta de Mercado Pago %s: %s", pk, _sanitizar(exc))
            resultado["errores"] += 1
    return resultado
