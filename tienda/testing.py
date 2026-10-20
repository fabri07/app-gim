"""Helpers compartidos por los tests de la tienda."""

import io

from PIL import Image


def _png():
    """PNG de 1x1 generado por Pillow: uno escrito a mano en bytes falla el
    `verify()` del form del comprobante."""
    salida = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(salida, "PNG")
    return salida.getvalue()


PNG = _png()


def crear_producto(**campos):
    """`Producto.objects.create` ya aprobado: un producto nuevo nace
    `pendiente` (ver `Producto.moderacion`), y casi todos los tests quieren
    uno a la venta. Un `moderacion=` explícito gana."""
    from tienda.models import Producto

    campos.setdefault("moderacion", Producto.Moderacion.APROBADO)
    return Producto.objects.create(**campos)


def crear_foto(**campos):
    """`FotoProducto.objects.create` ya aprobada (ver `crear_producto`)."""
    from tienda.models import FotoProducto

    campos.setdefault("aprobada", True)
    return FotoProducto.objects.create(**campos)
