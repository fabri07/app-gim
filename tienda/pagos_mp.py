"""Cobro de pedidos de la tienda con la cuenta de Mercado Pago del gimnasio.

`crear_link_de_pago` corre DESPUÉS del `atomic` que reservó el stock (la red
nunca va dentro de un lock). Si Mercado Pago falla, el pedido se borra: nunca
tuvo un link pagable, así que no existe para nadie, y el carrito del alumno
queda intacto para reintentar.

Los tokens viajan envueltos en `Secreto` y no se atan a nombres locales (ver
`suscripcion.mercadopago.Secreto`).

`acreditar_pedido` lleva un pago al estado que le corresponde (molde
`suscripcion.services.acreditar`); `reconciliar_pedidos` es la red de
seguridad del cron para «el webhook se perdió y el alumno cerró la pestaña».
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from suscripcion import mercadopago
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto
from tienda import cuenta_mp, services
from tienda.models import CuentaMercadoPago, ItemPedido, Pedido, obtener_configuracion, q_vendible

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


# --- Acreditar --------------------------------------------------------------

ESTADOS_MP_APROBADO = frozenset({"approved"})
ESTADOS_MP_RECHAZADO = frozenset({"rejected", "cancelled"})
ESTADOS_MP_DEVUELTO = frozenset({"refunded", "charged_back"})
TOLERANCIA_MONTO = Decimal("0.01")
CLAVE_PAGOS_ADICIONALES = "pagos_adicionales"
#: Pedidos cuyo pago anterior (si lo hubo) NO movió plata: un pago nuevo los
#: puede reemplazar.
_SIN_PLATA = (_E.PENDIENTE_PAGO, _E.VENCIDO, _E.CANCELADO)
#: Pedidos sobre los que ya se cobró (o se cobró y se devolvió).
_CON_PLATA = (_E.PAGADO, _E.ENTREGADO, _E.REVISAR, _E.DEVUELTO)
#: Hasta cuántos días atrás `reconciliar_pedidos` mira pedidos.
DIAS_RECONCILIACION = 7


class AcreditacionReintentable(Exception):
    """Otro proceso movió el pedido dos veces seguidas mientras se decidía:
    no se adivina. El webhook responde 500 (Mercado Pago reintenta) y la
    reconciliación lo vuelve a intentar en la próxima corrida."""


class _Carrera(Exception):
    """Un UPDATE condicional no tocó la fila: hay que releer y decidir."""


def _monto_coincide(pago, pedido):
    """`transaction_amount` (lo que se cobró por el pedido, sin el interés de
    las cuotas ni la comisión de MP) en ARS, con tolerancia de un centavo."""
    if pago.get("currency_id") != "ARS":
        return False
    try:
        cobrado = Decimal(str(pago["transaction_amount"]))
    except (KeyError, InvalidOperation):
        return False
    return abs(cobrado - pedido.total) <= TOLERANCIA_MONTO


def _adicionales(respuesta):
    if not isinstance(respuesta, dict):
        return []
    return [dict(r) for r in respuesta.get(CLAVE_PAGOS_ADICIONALES) or [] if isinstance(r, dict)]


def adicionales_sin_resolver(respuesta):
    """Los pagos duplicados que todavía hay que devolver (el detalle del staff)."""
    return [r for r in _adicionales(respuesta) if not r.get("resuelto")]


def hay_adicionales_sin_resolver(respuesta):
    """El valor de `revision_mp_pendiente`: alguna entrada sin resolver."""
    return bool(adicionales_sin_resolver(respuesta))


def marcar_adicional_resuelto(respuesta, payment_id, ahora):
    """La respuesta con la entrada de `payment_id` resuelta, o `None` si ese
    pago no está anotado como adicional. Resolver dos veces no cambia nada."""
    registrados = _adicionales(respuesta)
    encontrado = False
    for entrada in registrados:
        if str(entrada.get("payment_id")) == payment_id:
            encontrado = True
            if not entrada.get("resuelto"):
                entrada["resuelto"] = ahora.isoformat()
    if not encontrado:
        return None
    nueva = dict(respuesta or {})
    nueva[CLAVE_PAGOS_ADICIONALES] = registrados
    return nueva


def _respuesta_con(pago, pedido):
    """El resumen del pago, conservando `pagos_adicionales`: la devolución
    del pago principal no puede borrar la única traza del que hay que
    devolver (molde `suscripcion.services.acreditar`)."""
    respuesta = dict(pago)
    adicionales = _adicionales(pedido.respuesta_mp)
    if adicionales:
        respuesta[CLAVE_PAGOS_ADICIONALES] = adicionales
    return respuesta


def _avisar(pedido_pk, staff=None, alumno=None):
    """Los avisos salen DESPUÉS del commit (y no salen si se deshace). Un
    push caído no rompe la acreditación."""
    from notificaciones import services as avisos

    def mandar():
        try:
            pedido = Pedido.objects.select_related("alumno__perfil__usuario", "gimnasio").get(
                pk=pedido_pk
            )
            if staff:
                avisos.notificar_pedido_al_staff(pedido, staff)
            if alumno:
                avisos.notificar_pedido_al_alumno(pedido, alumno)
        except Exception:  # noqa: BLE001 - un push caído no frena nada
            logger.exception("No se pudo avisar la acreditación del pedido %s", pedido_pk)

    transaction.on_commit(mandar)


def _transicionar(pedido, origen, ahora, *, filtro=None, **campos):
    """`services._transicionar`, pero un UPDATE que no toca filas es una
    carrera (otro proceso movió el pedido): se relee y se decide otra vez."""
    if not services._transicionar(pedido, origen, ahora, filtro=filtro, **campos):
        raise _Carrera


def _leer_pedido(pk):
    """El pedido con la fila bloqueada (Postgres; en SQLite no hace nada y el
    UPDATE condicional es el que manda). Aparte para que los tests simulen
    una lectura vieja."""
    return Pedido.objects.select_for_update().get(pk=pk)


def _registrar_adicional(pedido, pago_id, estado_mp, ahora):
    """Un segundo pago aprobado sobre un pedido que ya cobró: el pedido queda
    como está, el pago se anota y aparece en «Revisar MP» hasta que se
    devuelva. Quien llama ya descartó los ya anotados (el aviso sale una vez)."""
    respuesta = dict(pedido.respuesta_mp or {})
    registrados = _adicionales(respuesta)
    registrados.append(
        {"payment_id": pago_id, "status": estado_mp, "detectado": ahora.isoformat()}
    )
    respuesta[CLAVE_PAGOS_ADICIONALES] = registrados
    _transicionar(
        pedido, [pedido.estado], ahora, respuesta_mp=respuesta, revision_mp_pendiente=True
    )
    logger.warning(
        "Pago de Mercado Pago adicional sobre el pedido %s (gimnasio %s): pago %s. "
        "Hay que devolverlo desde Mercado Pago.",
        pedido.pk, pedido.gimnasio_id, pago_id,
    )
    _avisar(pedido.pk, staff="pago_duplicado")


def _re_reservar(pedido, campos, ahora):
    """Pago aprobado que llega tarde (VENCIDO, o CANCELADO por el alumno):
    se vuelve a pedir el stock todo-o-nada en su propio savepoint. PRIMERO la
    transición y DESPUÉS los UPDATE de stock (orden pedido -> variantes, como
    `vencer_pedidos`): dos llamadas del mismo pago no reservan dos veces,
    porque la segunda ya no encuentra el pedido en VENCIDO/CANCELADO. Sin
    stock: se deshace el savepoint, se relee y va a REVISAR.

    Un producto que ya no se vende (bajado o pendiente de la moderación, o
    despublicado) va a REVISAR por la misma rama, sin re-reservar: revivir el
    pedido le vendería algo que la plataforma sacó de la venta."""
    origen = [_E.VENCIDO, _E.CANCELADO]
    # Un CANCELADO por el staff no se revive (`_aplicar`).
    filtro = Q(estado=_E.VENCIDO) | Q(cancelado_por_staff=False)
    try:
        with transaction.atomic():
            if (
                ItemPedido.objects.filter(pedido_id=pedido.pk)
                .exclude(q_vendible("variante__producto__"))
                .exists()
            ):
                raise services.ErrorDeTienda("producto_no_disponible")
            _transicionar(
                pedido, origen, ahora, filtro=filtro,
                estado=_E.PAGADO, pagado_en=ahora, motivo="", **campos,
            )
            services._reservar_stock_del_pedido(pedido.pk)
    except services.ErrorDeTienda:
        pedido.refresh_from_db()
        _transicionar(pedido, origen, ahora, filtro=filtro, estado=_E.REVISAR, **campos)
        _avisar(pedido.pk, staff="revisar_mp", alumno="revisar_mp")
        return
    _avisar(pedido.pk, staff="pago_mp", alumno="confirmado")


def _aplicar(pedido, pago, ahora):
    """La máquina de estados (§5 del plan; tabla completa en el reporte de
    B5). Toda escritura es un UPDATE condicional; `_Carrera` si alguno no
    tocó la fila. Nunca `pedido.save()`."""
    pago_id = str(pago["id"])
    estado_mp = pago.get("status", "") or ""
    mismo = pedido.payment_id == pago_id
    previo = pedido.estado

    # 1. Un pago ya anotado como adicional (el duplicado): sólo su devolución
    #    cuenta, y lo único que hace es marcar resuelta su entrada. El pedido
    #    bueno sigue como está.
    if any(str(r.get("payment_id")) == pago_id for r in _adicionales(pedido.respuesta_mp)):
        if estado_mp in ESTADOS_MP_DEVUELTO:
            respuesta = marcar_adicional_resuelto(pedido.respuesta_mp, pago_id, ahora)
            if respuesta != pedido.respuesta_mp:
                _transicionar(
                    pedido, [previo], ahora, respuesta_mp=respuesta,
                    revision_mp_pendiente=hay_adicionales_sin_resolver(respuesta),
                )
        return

    # 2. Idempotencia por `(payment_id, estado_mp)`, NO por `payment_id` solo:
    #    la devolución llega con el MISMO id.
    if mismo and pedido.estado_mp == estado_mp:
        return

    # 3. Para el MISMO pago `estado_mp` no retrocede: dos lecturas fuera del
    #    lock pueden commitear en otro orden (una «approved» vieja no acredita
    #    un pago ya devuelto; una «in_process» vieja no pisa un «approved»).
    if mismo:
        if pedido.estado_mp in ESTADOS_MP_DEVUELTO and estado_mp not in ESTADOS_MP_DEVUELTO:
            return
        if pedido.estado_mp in ESTADOS_MP_APROBADO and estado_mp not in (
            ESTADOS_MP_APROBADO | ESTADOS_MP_DEVUELTO
        ):
            return

    campos = dict(
        payment_id=pago_id, estado_mp=estado_mp, respuesta_mp=_respuesta_con(pago, pedido)
    )

    if estado_mp in ESTADOS_MP_APROBADO:
        if previo in _CON_PLATA:
            if mismo:
                # Mismo pago con un estado previo no aprobado sobre un pedido
                # que ya cobró (no debería pasar): sólo se anota.
                _transicionar(pedido, [previo], ahora, filtro=Q(payment_id=pago_id), **campos)
            elif previo in (_E.PAGADO, _E.ENTREGADO) and (
                pedido.payment_id == "" or pedido.estado_mp not in ESTADOS_MP_APROBADO
            ):
                # El staff lo marcó pagado a mano (pasado el link): se adjunta
                # el pago, sin alarma. Sin pago o con uno que NO cobró (un
                # rechazado anterior queda anotado: `marcar_pagado` no lo
                # borra, y tomarlo por cobrado daría un falso «cobrado dos
                # veces»). El filtro exige el pago leído: no se pisa uno que
                # otro proceso adjuntó en el medio.
                if not _monto_coincide(pago, pedido):
                    logger.warning(
                        "Pago de Mercado Pago %s con monto o moneda distintos adjuntado al "
                        "pedido %s, que el staff marcó pagado a mano.",
                        pago_id, pedido.pk,
                    )
                _transicionar(
                    pedido, [previo], ahora,
                    filtro=Q(payment_id=pedido.payment_id, estado_mp=pedido.estado_mp),
                    **campos,
                )
            else:
                # Otro pago (o vacío en DEVUELTO) sobre un pedido que ya cobró:
                # queda como está (NO pasa a REVISAR: perdería el estado y el
                # stock). El pago va a «Revisar MP» hasta que se devuelva.
                _registrar_adicional(pedido, pago_id, estado_mp, ahora)
            return
        if previo not in _SIN_PLATA:
            # EN_REVISION: un pedido de MP nunca llega ahí (es de transferencia).
            logger.warning("Pago aprobado sobre el pedido %s en %s: ignorado", pedido.pk, previo)
            return
        if not _monto_coincide(pago, pedido):
            # Monto o moneda distintos: nunca «Pagado». Si estaba pendiente,
            # el stock vuelve (REVISAR no retiene); vencido o cancelado ya lo
            # habían devuelto.
            with transaction.atomic():
                _transicionar(pedido, [previo], ahora, estado=_E.REVISAR, **campos)
                if previo == _E.PENDIENTE_PAGO:
                    services._devolver_stock([pedido.pk])
            _avisar(pedido.pk, staff="revisar_mp", alumno="revisar_mp")
            return
        if previo == _E.PENDIENTE_PAGO:
            _transicionar(
                pedido, [_E.PENDIENTE_PAGO], ahora,
                estado=_E.PAGADO, pagado_en=ahora, motivo="", **campos,
            )
            _avisar(pedido.pk, staff="pago_mp", alumno="confirmado")
            return
        if previo == _E.CANCELADO and pedido.cancelado_por_staff:
            # Lo canceló el staff: un pago tardío no lo revive, lo decide una
            # persona desde «Revisar MP».
            _transicionar(
                pedido, [_E.CANCELADO], ahora, filtro=Q(cancelado_por_staff=True),
                estado=_E.REVISAR, **campos,
            )
            _avisar(pedido.pk, staff="revisar_mp", alumno="revisar_mp")
            return
        _re_reservar(pedido, campos, ahora)
        return

    if estado_mp in ESTADOS_MP_RECHAZADO:
        # Se anota sólo sobre un pedido sin plata (un aprobado posterior lo
        # reemplaza). Nunca pisa la marca de un pago DEVUELTO de otro id: es
        # lo que frena que una lectura vieja de «approved» lo acredite.
        if previo in _SIN_PLATA and (mismo or pedido.estado_mp not in ESTADOS_MP_DEVUELTO):
            _transicionar(pedido, [previo], ahora, **campos)
        return

    if estado_mp in ESTADOS_MP_DEVUELTO:
        if mismo and previo == _E.PAGADO:
            with transaction.atomic():
                _transicionar(pedido, [_E.PAGADO], ahora, estado=_E.DEVUELTO, **campos)
                services._devolver_stock([pedido.pk])
                services._resolver_arrepentimiento(pedido, ahora)
            _avisar(pedido.pk, staff="devuelto_mp", alumno="devuelto_mp")
        elif mismo and previo in (_E.ENTREGADO, _E.REVISAR):
            with transaction.atomic():
                _transicionar(pedido, [previo], ahora, estado=_E.DEVUELTO, **campos)
                services._resolver_arrepentimiento(pedido, ahora)
            _avisar(pedido.pk, staff="devuelto_mp", alumno="devuelto_mp")
        elif mismo and previo == _E.DEVUELTO:
            # No-op explícito: ya estaba devuelto (el staff lo canceló o dijo
            # «Ya lo devolví»). Sólo se anota el estado del pago: sin stock ni
            # avisos.
            _transicionar(pedido, [_E.DEVUELTO], ahora, **campos)
        elif previo in _SIN_PLATA and (mismo or pedido.estado_mp not in ESTADOS_MP_DEVUELTO):
            # Devuelto sobre un pedido que nunca quedó pagado: se anota igual,
            # así una lectura vieja de «approved» de ESTE pago no lo acredita
            # después (regla 3).
            _transicionar(pedido, [previo], ahora, **campos)
        # Un devuelto de OTRO pago sobre un pedido que cobró: no lo toca.
        return

    # `in_process`, `pending`, `authorized`, `in_mediation`...: nada.


def acreditar_pedido(cuenta, payment_id):
    """Lee el pago en Mercado Pago con el token de `cuenta` y lleva el pedido
    al estado que le corresponde. Devuelve el pedido, o `None` si el pago no
    es de un pedido de Mercado Pago de ESE gimnasio.

    Idempotente y conmutativo: el webhook, el retorno y la reconciliación lo
    llaman en cualquier orden y cantidad. Permisivo: no se niega por
    `publicada`, `tienda_habilitada` ni cuenta congelada (la plata ya entró).

    `ErrorDeMercadoPago` se propaga (quien llama decide: el webhook mira el
    código). `AcreditacionReintentable` si otro proceso movió el pedido dos
    veces mientras se decidía."""
    # Fuera de la transacción (no se retiene un lock esperando a la red) y
    # recortado en la MISMA línea: la respuesta cruda trae los datos del
    # pagador y Sentry captura las variables locales (ver
    # `suscripcion.services.acreditar`).
    pago = mercadopago.resumen_de_pago(
        mercadopago.consultar_pago(payment_id, token=Secreto(cuenta.access_token))
    )
    if pago.get("id") is None:
        return None
    try:
        referencia = uuid.UUID(str(pago.get("external_reference") or ""))
    except (ValueError, AttributeError):
        return None
    # La autenticidad: un pago leído con el token de ESTE gimnasio, con la
    # referencia de un pedido SUYO (un pago de otro vendedor no lo encuentra).
    pk = (
        Pedido.objects.filter(
            gimnasio_id=cuenta.gimnasio_id,
            medio=Pedido.Medio.MERCADOPAGO,
            referencia_externa=referencia,
        )
        .values_list("pk", flat=True)
        .first()
    )
    if pk is None:
        return None

    ahora = timezone.now()
    with transaction.atomic():
        # Una relectura y una decisión más si un UPDATE no tocó la fila (otro
        # proceso, p. ej. `vencer_pedidos`, lo movió); dos veces, se rinde.
        for _ in range(2):
            pedido = _leer_pedido(pk)
            try:
                _aplicar(pedido, pago, ahora)
                break
            except _Carrera:
                continue
        else:
            raise AcreditacionReintentable(
                f"El pedido {pk} cambió dos veces mientras se acreditaba"
            )
    return Pedido.objects.get(pk=pk)


# --- Reconciliar --------------------------------------------------------------


def _ids_de_pagos(referencia, token):
    """Sólo los ids de la búsqueda: cada resultado trae los datos del pagador
    y en una función aparte la lista muere con este frame (Sentry captura las
    variables locales). `acreditar_pedido` vuelve a leer cada pago por id."""
    return [
        r["id"]
        for r in mercadopago.buscar_pagos(str(referencia), token=token)
        if isinstance(r, dict) and r.get("id") is not None
    ]


def reconciliar_pedidos(*, limite=50, ahora=None):
    """Busca en Mercado Pago los pagos de los pedidos recientes que no
    quedaron pagados y los acredita. Devuelve `{"revisados", "errores"}`.

    **Se filtra por ESTADO, no por `payment_id` vacío** (lección de
    `suscripcion.services.reconciliar_pendientes`): una tarjeta rechazada
    deja su id en el pedido y el reintento aprobado, con el webhook perdido,
    no se miraría nunca. Del más nuevo al más viejo, con tope (cada llamada
    espera hasta 5 s y el job tiene 15 min). Un error se loguea y sigue: la
    próxima corrida lo reintenta. Las cuentas se leen en UNA query."""
    ahora = ahora or timezone.now()
    pedidos = list(
        Pedido.objects.filter(
            medio=Pedido.Medio.MERCADOPAGO,
            estado__in=_SIN_PLATA,
            creado__gte=ahora - timedelta(days=DIAS_RECONCILIACION),
        )
        .exclude(preference_id="")
        .exclude(referencia_externa__isnull=True)
        .order_by("-creado", "-pk")
        .values_list("pk", "gimnasio_id", "referencia_externa")[:limite]
    )
    cuentas = {
        c.gimnasio_id: c
        for c in CuentaMercadoPago.objects.filter(
            gimnasio_id__in={g for _, g, _ in pedidos}
        )
    }
    resultado = {"revisados": 0, "errores": 0}
    rechazadas = set()
    for pk, gimnasio_id, referencia in pedidos:
        cuenta = cuentas.get(gimnasio_id)
        # Sin tokens (ilegibles, o desconectada hace más de 7 días): no hay
        # con qué preguntar. Una cuenta que ya dio 401 en esta corrida
        # tampoco: daría 401 en cada pedido.
        if cuenta is None or not cuenta.access_token or gimnasio_id in rechazadas:
            continue
        resultado["revisados"] += 1
        try:
            for pago_id in _ids_de_pagos(referencia, Secreto(cuenta.access_token)):
                acreditar_pedido(cuenta, pago_id)
        except ErrorDeMercadoPago as exc:
            if exc.codigo == 401:
                rechazadas.add(gimnasio_id)
                cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira)
            logger.warning(
                "Reconciliación de la tienda: no se pudo revisar el pedido %s: %s (codigo=%s)",
                pk, type(exc).__name__, exc.codigo,
            )
            resultado["errores"] += 1
        except Exception as exc:  # noqa: BLE001 - un pedido no frena a los demás
            logger.error(
                "Reconciliación de la tienda: falló el pedido %s: %s", pk, type(exc).__name__
            )
            resultado["errores"] += 1
    return resultado
