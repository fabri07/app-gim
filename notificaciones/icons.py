"""Genera PNGs cuadrados (192/512) para el manifest de la PWA, a partir del
logo del gimnasio o, si no tiene, un placeholder con sus iniciales sobre su
color primario.

Vive acá (no en `tenants`) para no acoplar `Gimnasio` a Pillow/PWA -- mismo
criterio de desacople que `calendario`/`turnos`.

No se persiste un `ImageField` nuevo en `Gimnasio`: el PNG se genera al
vuelo y se cachea con el cache default de Django (`notificaciones/views.py`
usa una clave que incluye `gimnasio.modificado`), así que un logo re-subido
invalida sola la versión vieja sin lógica extra de invalidación.

**Riesgo aceptado a propósito**: sin logo, el placeholder usa la fuente
default de Pillow (no una tipografía cuidada) -- simplificación de MVP, ver
ISSUES.md.
"""

import io

from PIL import Image, ImageDraw, ImageFont, ImageOps

TAMANOS_PERMITIDOS = (192, 512)


def _iniciales(nombre: str) -> str:
    palabras = nombre.split()
    if not palabras:
        return "?"
    if len(palabras) == 1:
        return palabras[0][:2].upper()
    return (palabras[0][0] + palabras[-1][0]).upper()


def _placeholder(gimnasio, size: int) -> Image.Image:
    imagen = Image.new("RGB", (size, size), gimnasio.color_primario_css)
    dibujo = ImageDraw.Draw(imagen)
    texto = _iniciales(gimnasio.nombre)
    fuente = ImageFont.load_default(size=int(size * 0.4))
    caja = dibujo.textbbox((0, 0), texto, font=fuente)
    ancho_texto = caja[2] - caja[0]
    alto_texto = caja[3] - caja[1]
    posicion = ((size - ancho_texto) / 2 - caja[0], (size - alto_texto) / 2 - caja[1])
    dibujo.text(posicion, texto, fill=gimnasio.color_fondo_css, font=fuente)
    return imagen


def generar_icono(gimnasio, size: int) -> bytes:
    """Devuelve un PNG cuadrado de `size`x`size` px, en bytes."""
    if size not in TAMANOS_PERMITIDOS:
        raise ValueError(f"Tamaño de ícono no soportado: {size}")

    if gimnasio.logo:
        with gimnasio.logo.open("rb") as archivo:
            original = Image.open(archivo)
            original.load()
        original = original.convert("RGBA")
        cuadrada = ImageOps.pad(
            original, (size, size), color=gimnasio.color_fondo_css, centering=(0.5, 0.5)
        )
        salida = cuadrada.convert("RGB")
    else:
        salida = _placeholder(gimnasio, size)

    buffer = io.BytesIO()
    salida.save(buffer, format="PNG")
    return buffer.getvalue()
