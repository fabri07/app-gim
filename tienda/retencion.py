"""Retención de fotos de producto (ver `FotoRetenida`).

Una señal `pre_delete` sobre `FotoProducto` copia el registro antes de cada
borrado, venga de donde venga (la edición del staff, el borrado del producto
en cascada, el rechazo de la plataforma, el admin, un shell). Va dentro de la
misma transacción que el `DELETE`: si el borrado se deshace, la copia también.

El motivo y el usuario los pone el llamador con `motivo(...)`; sin contexto
queda `OTRO`. La cuenta demo no retiene: `restaurar_demo` la vacía cada 6 h.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_contexto = ContextVar("retencion_de_fotos", default=None)


@contextmanager
def motivo(motivo, usuario=None):
    """Todo borrado de fotos dentro del bloque se retiene con este motivo y
    este usuario (`None`, o un anónimo, quedan sin usuario)."""
    if usuario is not None and not usuario.is_authenticated:
        usuario = None
    token = _contexto.set((motivo, usuario))
    try:
        yield
    finally:
        _contexto.reset(token)


def retener(sender, instance, **kwargs):
    from tienda.models import FotoRetenida, Producto

    producto = (
        Producto.objects.filter(pk=instance.producto_id)
        .values("gimnasio_id", "nombre", "gimnasio__es_demo")
        .first()
    )
    if producto is None or producto["gimnasio__es_demo"]:
        return
    motivo_actual, usuario = _contexto.get() or (FotoRetenida.Motivo.OTRO, None)
    FotoRetenida.objects.create(
        gimnasio_id=producto["gimnasio_id"],
        producto_original=instance.producto_id,
        producto_nombre=producto["nombre"],
        imagen=instance.imagen.name,
        estaba_aprobada=instance.aprobada,
        subida=instance.creado,
        motivo=motivo_actual,
        usuario=usuario,
    )
