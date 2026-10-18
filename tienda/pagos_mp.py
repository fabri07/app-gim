"""Cobro de pedidos de la tienda con la cuenta de Mercado Pago del gimnasio.

`crear_link_de_pago` corre DESPUÉS del `atomic` que reservó el stock (la red
nunca va dentro de un lock). Si Mercado Pago falla, el pedido se borra: nunca
tuvo un link pagable, así que no existe para nadie, y el carrito del alumno
queda intacto para reintentar.

Los tokens viajan envueltos en `Secreto` y no se atan a nombres locales (ver
`suscripcion.mercadopago.Secreto`).
"""

import logging
import uuid

from django.db import transaction

from suscripcion import mercadopago
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto
from tienda import cuenta_mp, services
from tienda.models import CuentaMercadoPago, Pedido, obtener_configuracion

logger = logging.getLogger(__name__)

_E = Pedido.Estado


def _descartar_pedido(pedido):
    """Borra un pedido de MP que nunca tuvo link y devuelve su stock.

    Primero el UPDATE condicional del pedido y recién después las variantes
    (mismo orden de locks que `vencer_pedidos`). Si no tocó la fila (el staff
    lo canceló en esos segundos y ya devolvió el stock), no se devuelve dos
    veces ni se borra."""
    with transaction.atomic():
        tocadas = Pedido.objects.filter(
            pk=pedido.pk, estado=_E.PENDIENTE_PAGO, preference_id=""
        ).update(estado=_E.CANCELADO)
        if tocadas == 1:
            services._devolver_stock([pedido.pk])
            Pedido.objects.filter(pk=pedido.pk).delete()


def _fallo(pedido):
    """Descarta el pedido y arma el error para el alumno. «o elegí
    transferencia» sólo si de verdad la puede elegir (se recalcula: un 401
    recién pasó la cuenta a «reconectar» y la transferencia vuelve de respaldo)."""
    _descartar_pedido(pedido)
    gimnasio = pedido.gimnasio
    medios = services.medios_disponibles(gimnasio, obtener_configuracion(gimnasio))
    detalle = " o elegí transferencia" if Pedido.Medio.TRANSFERENCIA in medios else ""
    return services.ErrorDeTienda("mp_fallo", detalle)


def crear_link_de_pago(pedido, *, url_retorno_para, url_notificacion_para):
    """Crea la preferencia de Checkout Pro del pedido y devuelve el link.

    `url_retorno_para(referencia_externa)` y `url_notificacion_para(uuid_webhook)`
    devuelven URLs absolutas: las arma la vista, que tiene el `request` (molde
    `suscripcion.services.crear_intento`). Ante cualquier falla el pedido queda
    borrado y se levanta `ErrorDeTienda("mp_fallo")`."""
    cuenta = (
        CuentaMercadoPago.objects.filter(
            gimnasio_id=pedido.gimnasio_id, estado=CuentaMercadoPago.Estado.CONECTADA
        ).first()
    )
    if cuenta is None:
        raise _fallo(pedido)
    if not cuenta.access_token:
        # Ilegible (clave rotada o ausente): «reconectar» sin llamar a MP.
        cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira)
        raise _fallo(pedido)

    if pedido.referencia_externa is None:
        # Pedido creado por el código viejo durante el deploy: sin referencia
        # no hay forma de atar el pago al pedido.
        Pedido.objects.filter(pk=pedido.pk, referencia_externa__isnull=True).update(
            referencia_externa=uuid.uuid4()
        )
        pedido.refresh_from_db(fields=["referencia_externa"])
    referencia = str(pedido.referencia_externa)

    try:
        preferencia = mercadopago.crear_preferencia(
            titulo=f"Pedido #{pedido.pk} — {pedido.gimnasio.nombre}",
            monto_ars=pedido.total,
            referencia_externa=referencia,
            url_retorno=url_retorno_para(referencia),
            url_notificacion=url_notificacion_para(cuenta.uuid_webhook),
            expira=pedido.vence_preferencia,
            token=Secreto(cuenta.access_token),
            sandbox=cuenta.es_de_prueba,
            statement_descriptor=None,
        )
    except ErrorDeMercadoPago as exc:
        logger.warning(
            "No se pudo crear la preferencia del pedido %s: %s (codigo=%s)",
            pedido.pk, type(exc).__name__, exc.codigo,
        )
        if exc.codigo == 401:
            cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira)
        raise _fallo(pedido) from None

    guardado = Pedido.objects.filter(
        pk=pedido.pk, estado=_E.PENDIENTE_PAGO, preference_id=""
    ).update(preference_id=preferencia["id"], init_point=preferencia["init_point"])
    if not guardado:
        # Lo cancelaron en esos segundos: no se manda al alumno a pagar un
        # pedido que ya no existe para el gimnasio.
        raise services.ErrorDeTienda("estado_invalido")
    pedido.refresh_from_db(fields=["preference_id", "init_point"])
    return pedido.init_point


def acreditar_pedido(cuenta, payment_id):
    """Lee el pago en Mercado Pago con el token de `cuenta` y mueve el pedido.

    TODO B5: implementar (molde `suscripcion.services.acreditar`, reglas en la
    §5 del plan). Hasta entonces la vista de retorno lo llama y, como cualquier
    error, lo loguea y muestra «Estamos confirmando tu pago»; la bandera está
    apagada en producción, así que nadie llega acá."""
    raise NotImplementedError("acreditar_pedido llega en B5")
