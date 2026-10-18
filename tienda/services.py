"""Reglas de negocio de la tienda.

Tres decisiones que sostienen todo el archivo (ver la spec):

1. **El stock nunca se lee-y-resta.** Se descuenta con un UPDATE condicional
   (`stock__gte=n`) y se mira cuántas filas tocó.
2. **Toda transición es `filter(pk, estado__in=ORIGEN).update(...)`** y el
   stock se devuelve sólo si tocó una fila. Así no hay doble devolución ni en
   Postgres ni en SQLite (donde `select_for_update` no hace nada).
3. **El camino perezoso no toca la red ni manda push.** `vencer_pedidos` corre
   dentro de GETs; los avisos los manda el cron.
"""

import logging
from collections import namedtuple
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from tienda.models import (
    ESTADOS_EN_CURSO,
    ESTADOS_QUE_RETIENEN,
    ESTADOS_SIN_CERRAR,
    MARGEN_PREFERENCIA,
    MAX_LARGO_MOTIVO,
    ConfiguracionTienda,
    CuentaMercadoPago,
    ItemPedido,
    Pedido,
    Variante,
    obtener_configuracion,
)

logger = logging.getLogger(__name__)

MAX_UNIDADES_POR_ITEM = 20
GRACIA_COMPROBANTE = timedelta(minutes=2)
DIAS_ARREPENTIMIENTO = 10
CLAVE_CARRITO = "tienda_carrito"
#: «Quedan N» en el catálogo y «Stock bajo» en la lista del staff.
UMBRAL_POCAS_UNIDADES = 3

_E = Pedido.Estado

Linea = namedtuple("Linea", "variante cantidad subtotal")


class ErrorDeTienda(Exception):
    """Algo que el usuario tiene que leer. `motivo` es una clave de
    `MENSAJES`; `detalle` completa el mensaje (p. ej. el producto sin stock)."""

    def __init__(self, motivo, detalle=""):
        self.motivo = motivo
        self.detalle = detalle
        super().__init__(motivo)

    @property
    def mensaje(self):
        return MENSAJES[self.motivo].format(detalle=self.detalle)


MENSAJES = {
    "tienda_cerrada": "La tienda no está disponible en este momento.",
    "suplantando": "Mientras estás viendo la app como un alumno no se puede comprar en su nombre.",
    "carrito_vacio": "Tu carrito está vacío.",
    "cantidad_invalida": f"Podés llevar entre 1 y {MAX_UNIDADES_POR_ITEM} unidades de cada producto.",
    "producto_no_disponible": "Ese producto ya no está disponible.",
    "sin_stock": "No queda stock suficiente de {detalle}. Ajustá la cantidad y probá de nuevo.",
    "tope_pedidos": (
        "Ya tenés pedidos esperando el pago. Pagalos, cancelá los que no vayas a pagar "
        "o esperá a que se confirmen o venzan antes de hacer otro."
    ),
    "medio_no_disponible": "Elegí cómo pagar: ese medio de pago no está disponible.",
    "pago_en_curso": (
        "El pago con Mercado Pago está en curso: el link todavía se puede pagar. Esperá a "
        "que venza (si no se paga, el pedido se da de baja solo)."
    ),
    "mp_fallo": "No pudimos conectar con Mercado Pago. Probá de nuevo{detalle}.",
    "plazo_vencido": (
        "El plazo para mandar el comprobante venció: el pedido se da de baja y el stock "
        "vuelve a la tienda."
    ),
    "estado_invalido": "Ese pedido ya cambió de estado. Recargá la pantalla.",
    "no_es_tuyo": "Ese pedido no es tuyo.",
    "fuera_de_plazo_arrepentimiento": (
        f"El plazo para pedir la cancelación es de {DIAS_ARREPENTIMIENTO} días desde que "
        "retiraste el pedido."
    ),
    "motivo_largo": f"El motivo puede tener hasta {MAX_LARGO_MOTIVO} caracteres.",
    "stock_negativo": "No hay tantas unidades disponibles para descontar.",
}


def tienda_visible_para_alumnos(gimnasio):
    # `exists()` y no `obtener_configuracion`: lo llama el portal en cada GET y
    # no tiene por qué hacer un INSERT.
    return gimnasio.puede_usar_tienda and ConfiguracionTienda.objects.filter(
        gimnasio=gimnasio, publicada=True
    ).exists()


def mercadopago_disponible(gimnasio, config):
    """Si el alumno puede pagar con Mercado Pago ahora. Con la bandera apagada
    (o en la demo) no se consulta la cuenta: la tienda no carga NI UNA
    instancia de ella. `exists()` tampoco descifra los tokens."""
    if not settings.TIENDA_MP_ENABLED or gimnasio.es_demo or not config.acepta_mercadopago:
        return False
    return CuentaMercadoPago.objects.filter(
        gimnasio=gimnasio, estado=CuentaMercadoPago.Estado.CONECTADA
    ).exists()


def medios_disponibles(gimnasio, config):
    """Los medios que el alumno puede elegir, en el orden del carrito.

    La transferencia es el RESPALDO: aunque el staff la haya destildado, vuelve
    si Mercado Pago no está disponible (cuenta en «reconectar», bandera
    apagada). Así una cuenta caída nunca deja la tienda sin poder vender; la
    configuración le avisa al staff que sus alumnos ven sólo transferencia."""
    mp = mercadopago_disponible(gimnasio, config)
    medios = []
    if config.acepta_transferencia or not mp:
        medios.append(Pedido.Medio.TRANSFERENCIA)
    if mp:
        medios.append(Pedido.Medio.MERCADOPAGO)
    return medios


def preferencia_vigente(pedido, ahora=None):
    """El link de Mercado Pago de este pedido todavía se puede pagar. Mientras
    tanto ni el alumno ni el staff lo cancelan (el pago podría entrar en el
    medio). Sin link (el proceso murió antes de guardarlo) no hay nada en curso."""
    return (
        pedido.medio == Pedido.Medio.MERCADOPAGO
        and pedido.estado == _E.PENDIENTE_PAGO
        and pedido.preference_id != ""
        and pedido.vence_preferencia is not None
        and _ahora(ahora) < pedido.vence_preferencia
    )


def _ahora(ahora):
    return ahora or timezone.now()


def _variantes_vendibles(gimnasio):
    return Variante.objects.filter(
        producto__gimnasio=gimnasio, activa=True, producto__publicado=True
    )


# --- Carrito (en la sesión, sin reservar) ---------------------------------


def leer_carrito(session):
    """`{variante_id: cantidad}` saneado: la sesión es del cliente y puede
    traer cualquier cosa."""
    crudo = session.get(CLAVE_CARRITO) or {}
    limpio = {}
    if not isinstance(crudo, dict):
        return limpio
    for clave, valor in crudo.items():
        try:
            variante_id, cantidad = int(clave), int(valor)
        except (TypeError, ValueError):
            continue
        if 1 <= cantidad <= MAX_UNIDADES_POR_ITEM:
            limpio[variante_id] = cantidad
    return limpio


def _guardar_carrito(session, carrito):
    session[CLAVE_CARRITO] = {str(k): v for k, v in carrito.items()}
    session.modified = True


def agregar_al_carrito(session, *, gimnasio, variante_id, cantidad):
    if not _variantes_vendibles(gimnasio).filter(pk=variante_id).exists():
        raise ErrorDeTienda("producto_no_disponible")
    carrito = leer_carrito(session)
    nueva = carrito.get(variante_id, 0) + cantidad
    if cantidad < 1 or nueva > MAX_UNIDADES_POR_ITEM:
        raise ErrorDeTienda("cantidad_invalida")
    carrito[variante_id] = nueva
    _guardar_carrito(session, carrito)


def quitar_del_carrito(session, variante_id):
    carrito = leer_carrito(session)
    carrito.pop(variante_id, None)
    _guardar_carrito(session, carrito)


def vaciar_carrito(session):
    session.pop(CLAVE_CARRITO, None)
    session.modified = True


def lineas_del_carrito(session, *, gimnasio):
    """Las líneas que se pueden comprar, en UNA query. Lo ajeno o archivado se
    descarta en silencio (sesión vieja)."""
    carrito = leer_carrito(session)
    variantes = (
        _variantes_vendibles(gimnasio)
        .filter(pk__in=carrito)
        .select_related("producto")
        .order_by("producto__nombre", "pk")
    )
    lineas = [
        Linea(v, carrito[v.pk], v.producto.precio * carrito[v.pk]) for v in variantes
    ]
    if len(lineas) != len(carrito):
        # Se saca de la sesión lo que ya no se puede comprar: si no, el alumno
        # no lo ve (no está en las líneas), no lo puede quitar y el checkout
        # falla para siempre con «producto no disponible».
        _guardar_carrito(session, {l.variante.pk: l.cantidad for l in lineas})
    return lineas, sum((l.subtotal for l in lineas), Decimal("0"))


# --- Pedido ----------------------------------------------------------------


def crear_pedido(
    *, gimnasio, alumno, carrito, suplantando, medio=Pedido.Medio.TRANSFERENCIA, ahora=None
):
    ahora = _ahora(ahora)
    if not gimnasio.puede_usar_tienda:
        raise ErrorDeTienda("tienda_cerrada")
    if suplantando:
        raise ErrorDeTienda("suplantando")
    if not carrito:
        raise ErrorDeTienda("carrito_vacio")
    if any(not 1 <= n <= MAX_UNIDADES_POR_ITEM for n in carrito.values()):
        raise ErrorDeTienda("cantidad_invalida")
    config = obtener_configuracion(gimnasio)
    # El POST puede traer cualquier cosa, y la cuenta de MP pudo caerse entre
    # que el alumno vio el carrito y confirmó.
    if medio not in medios_disponibles(gimnasio, config):
        raise ErrorDeTienda("medio_no_disponible")
    en_curso = (
        Pedido.objects.for_gimnasio(gimnasio)
        .filter(alumno=alumno, estado__in=ESTADOS_EN_CURSO)
        # Un checkout de MP cuenta sólo mientras su link es pagable (mismo
        # criterio que `preferencia_vigente`): con el link vencido, o sin link
        # (el proceso murió antes de guardarlo), no hay pago en curso y no
        # tiene por qué trabarle otra compra.
        .exclude(
            Q(medio=Pedido.Medio.MERCADOPAGO, estado=_E.PENDIENTE_PAGO)
            & (Q(reservado_hasta__lte=ahora + MARGEN_PREFERENCIA) | Q(preference_id=""))
        )
        .count()
    )
    if en_curso >= config.max_pedidos_activos:
        # Sin lock a propósito: un doble envío simultáneo puede pasar el tope
        # por uno. Es un freno contra acaparar, no una regla de plata.
        raise ErrorDeTienda("tope_pedidos")

    variantes = {
        v.pk: v
        for v in _variantes_vendibles(gimnasio)
        .filter(pk__in=carrito)
        .select_related("producto")
    }
    if len(variantes) != len(carrito):
        raise ErrorDeTienda("producto_no_disponible")

    try:
        return _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora, medio)
    except IntegrityError:
        logger.warning("CHECK de stock violada al crear un pedido", exc_info=True)
        # Cinturón: la CHECK `stock >= 0` de la base. No debería pasar con el
        # UPDATE sin JOIN de abajo, pero un 500 en el checkout es lo peor.
        raise ErrorDeTienda("sin_stock", "uno de los productos")


def _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora, medio):
    with transaction.atomic():
        # Orden por id: dos compras concurrentes toman los locks de fila en el
        # mismo orden y no se bloquean en cruz.
        for variante_id in sorted(carrito):
            cantidad = carrito[variante_id]
            # SIN JOIN a propósito: con `producto__...` en el filtro Django arma
            # `WHERE id IN (subquery)` y Postgres, después de esperar el lock
            # de fila, no re-evalúa `stock >= n` (sobreventa -> CHECK -> 500).
            # `variantes` ya viene acotado al gimnasio y a lo vendible.
            tocadas = Variante.objects.filter(
                pk=variante_id, stock__gte=cantidad
            ).update(stock=F("stock") - cantidad)
            if tocadas != 1:
                raise ErrorDeTienda("sin_stock", str(variantes[variante_id]))
        total = sum(
            (variantes[i].producto.precio * n for i, n in carrito.items()), Decimal("0")
        )
        if medio == Pedido.Medio.MERCADOPAGO:
            plazo = timedelta(minutes=config.minutos_reserva_mp)
        else:
            plazo = timedelta(hours=config.horas_reserva_transferencia)
        pedido = Pedido.objects.create(
            gimnasio=gimnasio,
            alumno=alumno,
            medio=medio,
            total=total,
            reservado_hasta=ahora + plazo,
        )
        ItemPedido.objects.bulk_create(
            ItemPedido(
                pedido=pedido,
                variante=variantes[i],
                nombre=str(variantes[i])[:190],
                precio_unitario=variantes[i].producto.precio,
                cantidad=n,
            )
            for i, n in sorted(carrito.items())
        )
    return pedido


def _devolver_stock(pedido_ids):
    """Suma a cada variante lo que retenían esos pedidos: una query agregada
    más un UPDATE por variante (el costo depende del catálogo, no de cuántos
    pedidos se devuelven)."""
    por_variante = (
        ItemPedido.objects.filter(pedido_id__in=pedido_ids)
        .values("variante_id")
        .annotate(n=Sum("cantidad"))
        .order_by("variante_id")
    )
    for fila in por_variante:
        Variante.objects.filter(pk=fila["variante_id"]).update(stock=F("stock") + fila["n"])


def _candidatos_a_vencer(gimnasio, limite):
    qs = Pedido.objects.select_for_update(skip_locked=True).filter(
        estado=_E.PENDIENTE_PAGO, reservado_hasta__lt=limite
    )
    if gimnasio is not None:
        qs = qs.filter(gimnasio=gimnasio)
    return list(qs.order_by("pk").values_list("pk", flat=True))


class _Carrera(Exception):
    pass


def vencer_pedidos(*, gimnasio=None, ahora=None):
    """Vence los pedidos sin comprobante cuyo plazo (más la gracia) pasó y
    devuelve su stock. Devuelve los ids vencidos POR ESTE llamado.

    El camino rápido es un UPDATE masivo; si tocó menos filas de las que
    seleccionó (otro proceso movió alguna en el medio), se deshace y se vence
    de a uno, que es lento pero exacto."""
    ahora = _ahora(ahora)
    limite = ahora - GRACIA_COMPROBANTE
    try:
        with transaction.atomic():
            ids = _candidatos_a_vencer(gimnasio, limite)
            if not ids:
                return []
            # `motivo=""`: el de un comprobante rechazado ya no explica nada.
            tocadas = Pedido.objects.filter(pk__in=ids, estado=_E.PENDIENTE_PAGO).update(
                estado=_E.VENCIDO, motivo="", modificado=ahora
            )
            if tocadas != len(ids):
                raise _Carrera
            _devolver_stock(ids)
            return ids
    except _Carrera:
        vencidos = []
        for pk in ids:
            with transaction.atomic():
                if Pedido.objects.filter(pk=pk, estado=_E.PENDIENTE_PAGO).update(
                    estado=_E.VENCIDO, motivo="", modificado=ahora
                ):
                    _devolver_stock([pk])
                    vencidos.append(pk)
        return vencidos


def subir_comprobante(pedido, *, alumno, archivo, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if pedido.medio != Pedido.Medio.TRANSFERENCIA or pedido.estado != _E.PENDIENTE_PAGO:
        raise ErrorDeTienda("estado_invalido")
    # Primero el archivo (red, fuera de cualquier lock), después la transición
    # condicional. Si no entró, se borra el archivo NUEVO: nada huérfano en R2.
    pedido.comprobante.save(archivo.name, archivo, save=False)
    nombre = pedido.comprobante.name
    almacen = pedido.comprobante.storage
    with transaction.atomic():
        # Un pedido con el comprobante rechazado ya tiene archivo. Su nombre
        # sale de la BASE con la fila bloqueada, no de la instancia del
        # request, que puede ser vieja (dos subidas casi juntas).
        anterior = (
            Pedido.objects.select_for_update()
            .values_list("comprobante", flat=True)
            .get(pk=pedido.pk)
        ) or ""
        tocadas = Pedido.objects.filter(
            pk=pedido.pk,
            estado=_E.PENDIENTE_PAGO,
            reservado_hasta__gte=ahora - GRACIA_COMPROBANTE,
        ).update(
            estado=_E.EN_REVISION, comprobante=nombre, comprobante_subido_en=ahora,
            motivo="", modificado=ahora,
        )
    if not tocadas:
        almacen.delete(nombre)
        pedido.refresh_from_db()
        # Otro proceso lo movió (el staff lo pagó, el alumno lo canceló): eso
        # no es un plazo vencido. Sólo es vencido si sigue pendiente.
        if pedido.estado != _E.PENDIENTE_PAGO:
            raise ErrorDeTienda("estado_invalido")
        raise ErrorDeTienda("plazo_vencido")
    if anterior and anterior != nombre:
        # Recién ahora: si la transición no hubiera entrado, el viejo seguía
        # siendo lo único que el staff tenía para revisar.
        almacen.delete(anterior)
    pedido.refresh_from_db()


# --- Stock ----------------------------------------------------------------


def ajustar_stock(variante, delta):
    """Suma o resta unidades al disponible sin pisar las reservas que entren
    mientras el staff tenía la pantalla abierta."""
    if delta == 0:
        return
    qs = Variante.objects.filter(pk=variante.pk)
    if delta < 0:
        qs = qs.filter(stock__gte=-delta)
    if not qs.update(stock=F("stock") + delta):
        raise ErrorDeTienda("stock_negativo")
    variante.refresh_from_db()


def reservado_por_variante(gimnasio):
    return dict(
        ItemPedido.objects.filter(
            pedido__gimnasio=gimnasio, pedido__estado__in=ESTADOS_QUE_RETIENEN
        )
        .values("variante_id")
        .annotate(n=Sum("cantidad"))
        .values_list("variante_id", "n")
    )


# --- Transiciones ----------------------------------------------------------


def _transicionar(pedido, origen, ahora, *, filtro=None, **campos):
    """UPDATE condicional sobre el estado. True si este proceso hizo la
    transición (y por lo tanto le toca devolver el stock, si corresponde).

    `filtro` (un `Q`) agrega condiciones a la del estado: p. ej.
    `Q(payment_id="")` al adjuntar un pago de Mercado Pago a un pedido que el
    staff marcó pagado a mano, para no pisar uno que otro proceso ya adjuntó."""
    qs = Pedido.objects.filter(pk=pedido.pk, estado__in=origen)
    if filtro is not None:
        qs = qs.filter(filtro)
    tocadas = qs.update(modificado=ahora, **campos)
    if tocadas:
        pedido.refresh_from_db()
    return tocadas == 1


def _validar_motivo(motivo):
    if len(motivo) > MAX_LARGO_MOTIVO:
        raise ErrorDeTienda("motivo_largo")


def cancelar_por_alumno(pedido, *, alumno, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if preferencia_vigente(pedido, ahora):
        # El pago puede entrar en cualquier momento: cancelar ahora dejaría un
        # cobro sobre un pedido cancelado. Pasado `vence_preferencia` sí.
        raise ErrorDeTienda("pago_en_curso")
    with transaction.atomic():
        if not _transicionar(pedido, [_E.PENDIENTE_PAGO], ahora, estado=_E.CANCELADO, motivo=""):
            raise ErrorDeTienda("estado_invalido")
        _devolver_stock([pedido.pk])


def marcar_pagado(pedido, *, ahora=None):
    """El staff confirma la plata: desde la revisión del comprobante, o
    directo desde pendiente (pagó en el mostrador o transfirió sin subirlo)."""
    ahora = _ahora(ahora)
    # Cinturón del template: con el link de MP pagable el pago puede entrar en
    # cualquier momento (un POST desde una pantalla vieja no lo saltea).
    if preferencia_vigente(pedido, ahora):
        raise ErrorDeTienda("pago_en_curso")
    if not _transicionar(
        pedido, [_E.PENDIENTE_PAGO, _E.EN_REVISION], ahora, estado=_E.PAGADO, pagado_en=ahora,
        motivo="",  # un rechazo de comprobante viejo no es el motivo de nada ahora
    ):
        raise ErrorDeTienda("estado_invalido")


def rechazar_comprobante(pedido, *, motivo, ahora=None):
    """El comprobante no sirve (borroso, otro monto): el pedido vuelve a
    esperar el pago con un plazo nuevo y el stock SIGUE reservado. El archivo
    viejo queda hasta que el alumno suba otro. Para cortar del todo está
    `cancelar_por_staff`. Si no resube, vence como cualquier pendiente."""
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    horas = obtener_configuracion(pedido.gimnasio).horas_reserva_transferencia
    if not _transicionar(
        pedido, [_E.EN_REVISION], ahora,
        estado=_E.PENDIENTE_PAGO, motivo=motivo,
        reservado_hasta=ahora + timedelta(hours=horas),
    ):
        raise ErrorDeTienda("estado_invalido")


def entregar(pedido, *, ahora=None):
    ahora = _ahora(ahora)
    if not _transicionar(pedido, [_E.PAGADO], ahora, estado=_E.ENTREGADO, entregado_en=ahora):
        raise ErrorDeTienda("estado_invalido")


def cancelar_por_staff(pedido, *, motivo, ahora=None):
    """Sin pagar → CANCELADO; pagado sin entregar → DEVUELTO (el staff le
    devolvió la plata por fuera). Las dos devuelven el stock."""
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    if preferencia_vigente(pedido, ahora):
        # Ver `marcar_pagado`: cancelar ahora dejaría un cobro sobre un
        # pedido cancelado.
        raise ErrorDeTienda("pago_en_curso")
    with transaction.atomic():
        hecho = _transicionar(
            pedido, [_E.PENDIENTE_PAGO, _E.EN_REVISION], ahora,
            estado=_E.CANCELADO, motivo=motivo,
        ) or _transicionar(pedido, [_E.PAGADO], ahora, estado=_E.DEVUELTO, motivo=motivo)
        if not hecho:
            raise ErrorDeTienda("estado_invalido")
        _devolver_stock([pedido.pk])
        # Si el alumno había pedido la cancelación, esto la resuelve: si no,
        # queda contada para siempre en la tarjeta del panel.
        Pedido.objects.filter(
            pk=pedido.pk, cancelacion_pedida_en__isnull=False, cancelacion_resuelta_en__isnull=True
        ).update(cancelacion_resuelta_en=ahora)
    pedido.refresh_from_db()


def puede_pedir_cancelacion(pedido, *, hoy=None):
    """Ley 24.240 art. 34: 10 días corridos desde la entrega del bien o la
    celebración del contrato, LO ÚLTIMO. Con retiro en el gimnasio: mientras
    no lo retiró se puede siempre; retirado, hasta 10 días después del retiro
    (en fecha argentina)."""
    hoy = hoy or timezone.localdate()
    if pedido.cancelacion_pedida_en is not None:
        return False
    if pedido.estado == _E.PAGADO:
        return True
    return (
        pedido.estado == _E.ENTREGADO
        and pedido.entregado_en is not None
        and hoy <= timezone.localdate(pedido.entregado_en) + timedelta(days=DIAS_ARREPENTIMIENTO)
    )


def q_puede_pedir_cancelacion(*, hoy=None):
    """`puede_pedir_cancelacion` como filtro de la base: localdate(entregado)
    >= hoy - 10 equivale a entregado_en >= el inicio (en hora argentina) de
    ese día. Lo fija `test_el_filtro_en_la_base_coincide_con_la_regla`."""
    hoy = hoy or timezone.localdate()
    desde = timezone.make_aware(
        datetime.combine(hoy - timedelta(days=DIAS_ARREPENTIMIENTO), time.min)
    )
    return Q(cancelacion_pedida_en__isnull=True) & (
        Q(estado=_E.PAGADO) | Q(estado=_E.ENTREGADO, entregado_en__gte=desde)
    )


def pedir_cancelacion(pedido, *, alumno, ahora=None):
    ahora = _ahora(ahora)
    if pedido.alumno_id != alumno.pk:
        raise ErrorDeTienda("no_es_tuyo")
    if not puede_pedir_cancelacion(pedido, hoy=timezone.localdate(ahora)):
        raise ErrorDeTienda("fuera_de_plazo_arrepentimiento")
    tocadas = Pedido.objects.filter(
        q_puede_pedir_cancelacion(hoy=timezone.localdate(ahora)), pk=pedido.pk
    ).update(cancelacion_pedida_en=ahora, modificado=ahora)
    if not tocadas:
        raise ErrorDeTienda("estado_invalido")
    pedido.refresh_from_db()


def resolver_cancelacion(pedido, *, aceptar, motivo="", ahora=None):
    ahora = _ahora(ahora)
    _validar_motivo(motivo)
    base = Pedido.objects.filter(
        pk=pedido.pk, cancelacion_pedida_en__isnull=False, cancelacion_resuelta_en__isnull=True
    )
    with transaction.atomic():
        if not aceptar:
            # Un motivo vacío no pisa el que ya hubiera.
            campos = {"motivo": motivo} if motivo else {}
            if not base.update(cancelacion_resuelta_en=ahora, modificado=ahora, **campos):
                raise ErrorDeTienda("estado_invalido")
        elif base.filter(estado=_E.PAGADO).update(
            estado=_E.DEVUELTO, cancelacion_resuelta_en=ahora, motivo=motivo, modificado=ahora
        ):
            _devolver_stock([pedido.pk])
        elif not base.filter(estado=_E.ENTREGADO).update(
            estado=_E.DEVUELTO, cancelacion_resuelta_en=ahora, motivo=motivo, modificado=ahora
        ):
            raise ErrorDeTienda("estado_invalido")
    pedido.refresh_from_db()


# --- Pedidos de Mercado Pago a revisar (staff) ------------------------------


def _resolver_arrepentimiento(pedido, ahora):
    """Toda transición a DEVUELTO resuelve un arrepentimiento pendiente (si
    no, queda contado para siempre en la tarjeta del panel)."""
    Pedido.objects.filter(
        pk=pedido.pk, cancelacion_pedida_en__isnull=False, cancelacion_resuelta_en__isnull=True
    ).update(cancelacion_resuelta_en=ahora)


def _reservar_stock_del_pedido(pedido_id):
    """Vuelve a descontar el stock de un pedido que no lo retenía (REVISAR,
    VENCIDO, CANCELADO): UPDATE condicional SIN JOIN por variante, en orden de
    id (mismo criterio que `_reservar_y_crear`). Levanta `ErrorDeTienda` si a
    alguna le falta: quien llama lo corre dentro de un `atomic` que se deshace
    entero (todo o nada)."""
    por_variante = (
        ItemPedido.objects.filter(pedido_id=pedido_id)
        .values("variante_id")
        .annotate(n=Sum("cantidad"))
        .order_by("variante_id")
    )
    for fila in por_variante:
        if not Variante.objects.filter(
            pk=fila["variante_id"], stock__gte=fila["n"]
        ).update(stock=F("stock") - fila["n"]):
            raise ErrorDeTienda("sin_stock", "uno de los productos")


def entregar_igual(pedido, *, ahora=None):
    """REVISAR -> ENTREGADO: el staff decide entregar igual (monto distinto,
    pago tardío). Pide el stock (REVISAR no lo retiene) y pone `pagado_en` si
    faltaba, así cuenta en «Vendido este mes». Primero la transición y
    después el stock, dentro del mismo `atomic`: sin stock se deshace todo."""
    ahora = _ahora(ahora)
    try:
        with transaction.atomic():
            if not _transicionar(
                pedido, [_E.REVISAR], ahora,
                estado=_E.ENTREGADO, entregado_en=ahora,
                pagado_en=Coalesce(F("pagado_en"), Value(ahora)),
            ):
                raise ErrorDeTienda("estado_invalido")
            _reservar_stock_del_pedido(pedido.pk)
    finally:
        # Sin stock el `atomic` deshace la transición, pero la instancia ya
        # decía ENTREGADO (`_transicionar` la refrescó adentro).
        pedido.refresh_from_db()


def marcar_devuelto(pedido, *, ahora=None):
    """«Ya lo devolví»: REVISAR -> DEVUELTO. El staff devolvió la plata desde
    su cuenta de Mercado Pago. Sin stock: REVISAR no lo retiene."""
    ahora = _ahora(ahora)
    with transaction.atomic():
        if not _transicionar(pedido, [_E.REVISAR], ahora, estado=_E.DEVUELTO):
            raise ErrorDeTienda("estado_invalido")
        _resolver_arrepentimiento(pedido, ahora)
    pedido.refresh_from_db()


def resolver_pago_duplicado(pedido, *, payment_id, ahora=None):
    """«Ya devolví el duplicado»: marca resuelta la entrada de ese pago en
    `pagos_adicionales`. La pestaña se vacía sólo si no queda ninguna sin
    resolver (con dos duplicados, devolver uno no alcanza). Lee y reescribe
    el JSON con la fila bloqueada (Postgres)."""
    from tienda import pagos_mp

    ahora = _ahora(ahora)
    with transaction.atomic():
        bloqueado = Pedido.objects.select_for_update().get(pk=pedido.pk)
        respuesta = pagos_mp.marcar_adicional_resuelto(
            bloqueado.respuesta_mp, str(payment_id), ahora
        )
        if respuesta is None:
            raise ErrorDeTienda("estado_invalido")
        Pedido.objects.filter(pk=pedido.pk).update(
            respuesta_mp=respuesta,
            revision_mp_pendiente=pagos_mp.hay_adicionales_sin_resolver(respuesta),
            modificado=ahora,
        )
    pedido.refresh_from_db()


# --- Ventas y contadores ---------------------------------------------------


def tiene_ventas(producto):
    return ItemPedido.objects.filter(variante__producto=producto).exists()


#: Cancelación pedida por el alumno y todavía sin resolver. La comparten el
#: contador del panel y el filtro del listado del staff.
Q_CANCELACION_PENDIENTE = Q(
    estado__in=[_E.PAGADO, _E.ENTREGADO],
    cancelacion_pedida_en__isnull=False,
    cancelacion_resuelta_en__isnull=True,
)


#: Pestaña «Revisar MP»: Mercado Pago cobró y hace falta una mano humana (el
#: pedido quedó en REVISAR, o tiene un pago duplicado sin devolver). Un
#: booleano y no `has_key` sobre el JSON: así la pestaña se vacía cuando se
#: resuelve. La comparten el contador del panel, el filtro y el push.
Q_REVISAR_MP = Q(estado=_E.REVISAR) | Q(revision_mp_pendiente=True)


def contadores_panel(gimnasio):
    """UNA query agregada. `vivos` = pedidos sin cerrar (retienen stock o
    esperan una decisión del staff sobre plata cobrada: REVISAR)."""
    return Pedido.objects.for_gimnasio(gimnasio).aggregate(
        a_revisar=Count("pk", filter=Q(estado=_E.EN_REVISION)),
        a_revisar_mp=Count("pk", filter=Q_REVISAR_MP),
        a_entregar=Count("pk", filter=Q(estado=_E.PAGADO)),
        cancelaciones=Count("pk", filter=Q_CANCELACION_PENDIENTE),
        vivos=Count("pk", filter=Q(estado__in=ESTADOS_SIN_CERRAR)),
    )
