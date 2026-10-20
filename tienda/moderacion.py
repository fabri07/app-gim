"""Moderación de la tienda: lo que hace el superadmin (aprobar, bajar, fotos),
las denuncias de los alumnos, la cola, su contador y el aviso por mail.

Mismo criterio que `services`: el estado de la BASE manda sobre la instancia
en memoria. Toda transición es un `filter(...).update()` condicional y se mira
si tocó la fila; nunca un `if producto.moderacion == ...` antes de escribir.

La cuenta demo queda fuera de la cola, del contador y del mail (decisión 9 del
plan): se ve aparte, en `cola()["demo"]`.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, OuterRef, Prefetch, Q, Value, When
from django.template.loader import render_to_string
from django.utils import timezone

from tienda import services
from tienda.models import (
    ConfiguracionTienda,
    DenunciaProducto,
    FotoProducto,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
)

logger = logging.getLogger(__name__)

UMBRAL_DENUNCIAS = 3
MOTIVO_PEDIDO_CANCELADO = "El producto fue dado de baja por la plataforma."
AVISO_FOTO_RECHAZADA = "Una foto fue rechazada por la plataforma."
MOTIVO_POR_DENUNCIAS = "Lo denunciaron varios alumnos: lo estamos revisando."

_M = Producto.Moderacion
_ABIERTA = DenunciaProducto.Estado.ABIERTA
_RESUELTA = DenunciaProducto.Estado.RESUELTA
#: Largo de `Producto.moderacion_motivo` (Postgres no corta solo: `DataError`).
_LARGO_MOTIVO = Producto._meta.get_field("moderacion_motivo").max_length


def _ahora(ahora):
    return ahora or timezone.now()


# --- Cola y contador ---------------------------------------------------------


def q_para_revisar():
    """Sobre `Producto`: lo nuevo que espera aprobación, y lo que está a la
    venta con algo para mirar (texto cambiado, fotos sin aprobar, denuncias
    abiertas). Un BAJADO nunca: ya se decidió."""
    fotos_nuevas = FotoProducto.objects.filter(producto=OuterRef("pk"), aprobada=False)
    denuncias = DenunciaProducto.objects.filter(producto=OuterRef("pk"), estado=_ABIERTA)
    return Q(moderacion=_M.PENDIENTE) | (
        Q(moderacion=_M.APROBADO)
        & (Q(cambio_pendiente=True) | Exists(fotos_nuevas) | Exists(denuncias))
    )


def contador():
    """Cuántos productos esperan al superadmin, sin la demo. UNA query (el
    inicio del panel de plataforma lo muestra en cada carga)."""
    return (
        Producto.objects.filter(q_para_revisar()).exclude(gimnasio__es_demo=True).count()
    )


def cola():
    """La pantalla de moderación, agrupada por producto. Cantidad de queries
    constante: una por sección más sus prefetch, sin importar cuántos haya.

    `denuncias_abiertas` (lista, por `to_attr`) es lo que la tarjeta muestra
    y lo que la confirmación de «Aprobar» manda como `denuncias_vistas`."""
    abiertas = (
        DenunciaProducto.objects.filter(estado=_ABIERTA)
        .select_related("alumno")
        .order_by("creado", "pk")
    )
    base = (
        Producto.objects.filter(q_para_revisar())
        .select_related("gimnasio", "categoria")
        .prefetch_related(
            "fotos",
            "variantes",
            Prefetch("denuncias", queryset=abiertas, to_attr="denuncias_abiertas"),
        )
        .order_by("creado", "pk")
    )
    reales = base.exclude(gimnasio__es_demo=True)
    return {
        "pendientes": list(reales.filter(moderacion=_M.PENDIENTE)),
        "a_la_venta": list(reales.filter(moderacion=_M.APROBADO)),
        "demo": list(base.filter(gimnasio__es_demo=True)),
    }


# --- Productos -----------------------------------------------------------------


def aprobar_producto(producto_id, *, version, fotos_vistas, denuncias_vistas, ahora=None):
    """El superadmin aprueba SÓLO lo que vio (decisión 3 del plan).

    `version` es el `modificado` que tenía el producto cuando se armó la
    pantalla: si el staff editó el texto después, no se aprueba nada y el
    cambio sigue en revisión. Una foto que entró después de la pantalla, o
    una denuncia nueva, siguen pendientes. El `update()` no mueve
    `modificado` (no dispara `auto_now`), así que aprobar no invalida otra
    pantalla abierta sobre el mismo producto."""
    ahora = _ahora(ahora)
    with transaction.atomic():
        tocadas = Producto.objects.filter(
            pk=producto_id, moderacion__in=[_M.PENDIENTE, _M.APROBADO], modificado=version
        ).update(
            moderacion=_M.APROBADO, cambio_pendiente=False,
            # El aviso de foto rechazada SOBREVIVE a la aprobación (el staff
            # tiene que enterarse de por qué desapareció la foto; lo limpia
            # él al volver a guardar el producto). Cualquier otro motivo se va.
            moderacion_motivo=Case(
                When(moderacion_motivo=AVISO_FOTO_RECHAZADA, then=Value(AVISO_FOTO_RECHAZADA)),
                default=Value(""),
            ),
            moderacion_desde=ahora,
        )
        if not tocadas:
            return False
        # Acotadas al producto: el POST puede traer cualquier id.
        FotoProducto.objects.filter(
            producto_id=producto_id, pk__in=list(fotos_vistas), aprobada=False
        ).update(aprobada=True)
        DenunciaProducto.objects.filter(
            producto_id=producto_id, pk__in=list(denuncias_vistas), estado=_ABIERTA
        ).update(estado=_RESUELTA, modificado=ahora)
    return True


def bajar_producto(producto_id, *, motivo, ahora=None):
    """Lo saca de la venta para siempre (el staff ya no lo puede editar ni
    borrar) y cancela sus pedidos SIN PAGAR (decisión 2: sólo
    `PENDIENTE_PAGO`; lo que tiene plata lo resuelve el staff). Devuelve los
    ids cancelados, o `None` si ya estaba bajado.

    Contra la carrera con el checkout (foco 6): PRIMERO se bloquean las
    variantes del producto, igual que las bloquea el UPDATE de stock de
    `services._reservar_y_crear`. Si el checkout llegó antes, esto espera a
    que commitee y su pedido cae en la cancelación; si llegó después, su
    UPDATE espera a esta baja y la revalidación de adentro del `atomic` ya ve
    el producto bajado. En SQLite `select_for_update` no hace nada (escribe
    una conexión a la vez)."""
    ahora = _ahora(ahora)
    with transaction.atomic():
        list(
            Variante.objects.select_for_update()
            .filter(producto_id=producto_id)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        tocadas = Producto.objects.filter(
            pk=producto_id, moderacion__in=[_M.PENDIENTE, _M.APROBADO]
        ).update(
            moderacion=_M.BAJADO, moderacion_motivo=motivo[:_LARGO_MOTIVO],
            moderacion_desde=ahora, cambio_pendiente=False,
        )
        if not tocadas:
            return None
        DenunciaProducto.objects.filter(producto_id=producto_id, estado=_ABIERTA).update(
            estado=_RESUELTA, modificado=ahora
        )
        cancelados = services.cancelar_pedidos_de_producto(
            producto_id, motivo=MOTIVO_PEDIDO_CANCELADO, ahora=ahora
        )
        if cancelados:
            transaction.on_commit(lambda: _avisar_cancelados(cancelados))
    return cancelados


def _avisar_cancelados(pedido_ids):
    """Un push por pedido, después del commit. «cancelado_plataforma» y no
    «cancelado»: ese dice «El gimnasio canceló», que acá sería falso. Un push
    caído no frena nada (mismo criterio que `pagos_mp._avisar`)."""
    from notificaciones import services as avisos

    pedidos = Pedido.objects.filter(pk__in=pedido_ids).select_related(
        "alumno__perfil__usuario", "gimnasio"
    )
    for pedido in pedidos.order_by("pk"):
        try:
            avisos.notificar_pedido_al_alumno(pedido, "cancelado_plataforma")
        except Exception:  # noqa: BLE001 - un push caído no frena nada
            logger.exception("No se pudo avisar la cancelación del pedido %s", pedido.pk)


# --- Fotos ---------------------------------------------------------------------


def aprobar_foto(foto_id):
    return bool(
        FotoProducto.objects.filter(pk=foto_id, aprobada=False)
        .exclude(producto__moderacion=_M.BAJADO)
        .update(aprobada=True)
    )


def rechazar_foto(foto_id):
    """Borra la FILA (el archivo queda en R2, como logos y comprobantes) y le
    deja el aviso al staff en el producto. También vale para una foto ya
    aprobada (decisión 6): las copias que bajaron los navegadores no se
    pueden borrar. El aviso no pisa otro motivo (p. ej. el de denuncias)."""
    with transaction.atomic():
        producto_id = (
            FotoProducto.objects.filter(pk=foto_id)
            .exclude(producto__moderacion=_M.BAJADO)
            .values_list("producto_id", flat=True)
            .first()
        )
        if producto_id is None:
            return False
        borradas, _ = FotoProducto.objects.filter(pk=foto_id).delete()
        if not borradas:
            return False
        Producto.objects.filter(pk=producto_id).filter(
            Q(moderacion_motivo="") | Q(moderacion_motivo=AVISO_FOTO_RECHAZADA)
        ).update(moderacion_motivo=AVISO_FOTO_RECHAZADA)
    return True


# --- Denuncias -----------------------------------------------------------------


def _ya_denuncio(producto, alumno):
    return DenunciaProducto.objects.filter(
        producto=producto, alumno=alumno, estado=_ABIERTA
    ).exists()


def registrar_denuncia(*, producto, alumno, motivo, detalle, ahora=None):
    """`False` si el alumno ya tenía una abierta sobre el producto (mirada en
    Python para el caso común; la constraint parcial ataja dos envíos
    simultáneos). Con `UMBRAL_DENUNCIAS` alumnos distintos, el producto sale
    de la venta y vuelve a la cola, pero sus pedidos sin pagar siguen vivos
    (decisión 8): se cancelan sólo si el superadmin lo baja.

    Cuentan sólo las abiertas: después de aprobar con las denuncias vistas,
    hacen falta tres nuevas. Una de un alumno borrado (SET_NULL) no suma: no
    se puede saber si es distinto de los demás."""
    ahora = _ahora(ahora)
    if _ya_denuncio(producto, alumno):
        return False
    try:
        with transaction.atomic():
            DenunciaProducto.objects.create(
                gimnasio_id=producto.gimnasio_id, producto=producto, alumno=alumno,
                motivo=motivo, detalle=detalle,
            )
    except IntegrityError:
        return False
    alumnos = (
        DenunciaProducto.objects.filter(
            producto=producto, estado=_ABIERTA, alumno__isnull=False
        )
        .values("alumno")
        .distinct()
        .count()
    )
    if alumnos >= UMBRAL_DENUNCIAS:
        Producto.objects.filter(pk=producto.pk, moderacion=_M.APROBADO).update(
            moderacion=_M.PENDIENTE, moderacion_motivo=MOTIVO_POR_DENUNCIAS,
            moderacion_desde=ahora,
        )
    return True


# --- Aviso por mail ------------------------------------------------------------


def avisar_a_la_plataforma(gimnasio, *, url_cola, ahora=None):
    """Mail al dueño del producto: «hay algo para revisar». Tope de UNO por
    hora por gimnasio, con un UPDATE condicional sobre
    `ultimo_aviso_moderacion` (dos altas casi juntas no mandan dos). La demo
    no avisa ni escribe nada (decisión 9). `url_cola` la arma quien llama (la
    vista tiene el `request`). Devuelve si mandó."""
    if gimnasio.es_demo:
        return False
    destino = settings.SOLICITUDES_AVISO_EMAIL or settings.DEFAULT_FROM_EMAIL
    if not destino:
        # Sin casilla no hay a quién avisar: no se gasta el tope de la hora.
        return False
    ahora = _ahora(ahora)
    obtener_configuracion(gimnasio)
    tocadas = (
        ConfiguracionTienda.objects.filter(gimnasio=gimnasio)
        .filter(
            Q(ultimo_aviso_moderacion__isnull=True)
            | Q(ultimo_aviso_moderacion__lte=ahora - timedelta(hours=1))
        )
        .update(ultimo_aviso_moderacion=ahora)
    )
    if not tocadas:
        return False
    asunto = f"Moderación: {gimnasio.nombre} tiene algo para revisar"
    cuerpo = render_to_string(
        "tienda/email/aviso_moderacion.txt", {"gimnasio": gimnasio, "url_cola": url_cola}
    )

    def enviar():
        # `fail_silently=True`: corre por `on_commit`, con la fila ya
        # commiteada. Un SMTP caído no puede tirarle un 500 al staff que
        # guardó el producto (ver `solicitudes/emails.py::_enviar`).
        EmailMessage(
            subject=asunto, body=cuerpo,
            from_email=settings.DEFAULT_FROM_EMAIL or None, to=[destino],
        ).send(fail_silently=True)

    transaction.on_commit(enviar)
    return True
