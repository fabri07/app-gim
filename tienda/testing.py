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
