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

from collections import namedtuple
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from tienda.models import (
    ESTADOS_EN_CURSO,
    ESTADOS_QUE_RETIENEN,
    MAX_LARGO_MOTIVO,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Variante,
    obtener_configuracion,
)

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
        "Ya tenés pedidos esperando el pago. Pagalos o cancelalos antes de hacer otro."
    ),
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


def crear_pedido(*, gimnasio, alumno, carrito, suplantando, ahora=None):
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
    en_curso = Pedido.objects.for_gimnasio(gimnasio).filter(
        alumno=alumno, estado__in=ESTADOS_EN_CURSO
    ).count()
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
        return _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora)
    except IntegrityError:
        # Cinturón: la CHECK `stock >= 0` de la base. No debería pasar con el
        # UPDATE sin JOIN de abajo, pero un 500 en el checkout es lo peor.
        raise ErrorDeTienda("sin_stock", "uno de los productos")


def _reservar_y_crear(gimnasio, alumno, carrito, variantes, config, ahora):
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
        pedido = Pedido.objects.create(
            gimnasio=gimnasio,
            alumno=alumno,
            total=total,
            reservado_hasta=ahora + timedelta(hours=config.horas_reserva_transferencia),
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
    # condicional. Si no entró, se borra el archivo: nada huérfano en R2.
    pedido.comprobante.save(archivo.name, archivo, save=False)
    nombre = pedido.comprobante.name
    tocadas = Pedido.objects.filter(
        pk=pedido.pk,
        estado=_E.PENDIENTE_PAGO,
        reservado_hasta__gte=ahora - GRACIA_COMPROBANTE,
    ).update(
        estado=_E.EN_REVISION, comprobante=nombre,
        comprobante_subido_en=ahora, modificado=ahora,
    )
    if not tocadas:
        pedido.comprobante.storage.delete(nombre)
        pedido.refresh_from_db()
        raise ErrorDeTienda("plazo_vencido")
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
