"""Foto de producto: se valida con el mismo helper que el logo del gimnasio
y se reescala a ≤1200 px en JPEG antes de guardarla, para que la vista que la
sirve nunca mande una foto de 6 MB de celular."""

import io
import uuid

from django import forms
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# `_validar_imagen` es «privado» de `tenants/forms.py`; se reusa a propósito
# (mismas reglas que el logo del gimnasio) en vez de copiarlo.
from tenants.forms import _validar_imagen

LADO_MAXIMO = 1200
TAMANIO_MAXIMO = 8 * 1024 * 1024


def preparar_foto(archivo):
    _validar_imagen(
        archivo,
        resolucion_referencia=(300, 300),
        lado_minimo=300,
        tamanio_maximo_bytes=TAMANIO_MAXIMO,
        formatos_validos={"JPEG", "PNG"},
        etiqueta="La foto",
        mensaje_tamanio="La foto pesa más de 8 MB.",
    )
    try:
        imagen = Image.open(archivo)
        # Decodifica el JPEG a escala reducida (no-op en PNG): menos CPU en
        # el worker de Render con fotos de celular de 12 MP. `draft` nunca
        # baja de lo pedido, así que la salida sigue llegando a LADO_MAXIMO;
        # el mínimo de resolución ya se validó sobre el original arriba.
        imagen.draft("RGB", (LADO_MAXIMO, LADO_MAXIMO))
        imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    except Exception:
        raise forms.ValidationError("El archivo no es una imagen válida.")
    imagen.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    salida = io.BytesIO()
    imagen.save(salida, "JPEG", quality=85, optimize=True)
    return ContentFile(salida.getvalue(), name=f"{uuid.uuid4().hex}.jpg")
