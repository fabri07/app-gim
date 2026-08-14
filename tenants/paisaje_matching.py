"""Sugerencia de paisaje de color a partir del logo subido por el gimnasio.

El dueño elige un paisaje (Bosque/Océano/Arena/Pizarra) de
`Gimnasio.PALETAS` a mano, sin ninguna relación con los colores reales de su
logo. Este módulo cierra esa brecha: extrae el color dominante del logo
(ignorando fondo blanco/negro/transparente, que no es la marca sino el
lienzo del archivo) y devuelve el paisaje curado cuyo `primario` está más
cerca en distancia euclidiana RGB.

No hace matching contra colores libres a propósito -- sigue "The Landscape
Rule" de DESIGN.md: el resultado siempre es uno de los 4 paisajes ya
armonizados, nunca un color inventado que podría resultar ilegible. La
distancia RGB simple (no Lab/CIEDE2000) es una simplificación aceptada: con
solo 4 candidatos alcanza, y evita sumar una dependencia nueva
(colormath/scikit-image) solo para esto.
"""

from collections import Counter

from PIL import Image

from tenants.models import Gimnasio

# Píxeles por encima/debajo de estos umbrales (en los 3 canales) se tratan
# como fondo del archivo, no como color de marca -- la mayoría de los logos
# son PNG con fondo blanco o transparente.
_UMBRAL_CLARO = 235
_UMBRAL_OSCURO = 20
_ALFA_MINIMO = 128  # píxeles con menos alfa que esto se consideran fondo
_TAMANIO_MUESTREO = (64, 64)


def _es_fondo(r, g, b):
    if r > _UMBRAL_CLARO and g > _UMBRAL_CLARO and b > _UMBRAL_CLARO:
        return True
    if r < _UMBRAL_OSCURO and g < _UMBRAL_OSCURO and b < _UMBRAL_OSCURO:
        return True
    return False


def _color_dominante(imagen):
    img = Image.open(imagen).convert("RGBA")
    img.thumbnail(_TAMANIO_MUESTREO)

    opacos = [(r, g, b) for r, g, b, a in img.getdata() if a >= _ALFA_MINIMO]

    # Primera pasada: solo color "de marca" (ni fondo claro/oscuro).
    contador = Counter(
        (r // 16 * 16, g // 16 * 16, b // 16 * 16)
        for r, g, b in opacos
        if not _es_fondo(r, g, b)
    )
    if contador:
        return contador.most_common(1)[0][0]

    # Fallback: la imagen es casi enteramente fondo claro/oscuro (p. ej. un
    # isotipo monocromático) -- usar el color opaco más común tal cual.
    contador = Counter(opacos)
    if contador:
        return contador.most_common(1)[0][0]

    # Imagen totalmente transparente: gris neutro, ningún paisaje gana por
    # mucho margen y el dueño lo corrige a mano.
    return (128, 128, 128)


def _hex_a_rgb(hexadecimal):
    valor = hexadecimal.lstrip("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))


def sugerir_paisaje(imagen):
    """Devuelve el valor de `Gimnasio.Paleta` cuyo primario está más cerca
    del color dominante de `imagen` (cualquier fuente que acepte
    `PIL.Image.open`: ruta, file-like, `UploadedFile`)."""
    color = _color_dominante(imagen)

    mejor_paisaje = None
    mejor_distancia = None
    for paisaje, roles in Gimnasio.PALETAS.items():
        candidato = _hex_a_rgb(roles["primario"])
        distancia = sum((a - b) ** 2 for a, b in zip(color, candidato))
        if mejor_distancia is None or distancia < mejor_distancia:
            mejor_distancia = distancia
            mejor_paisaje = paisaje

    return mejor_paisaje
