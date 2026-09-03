"""Genera PNGs cuadrados (192/512) para el manifest de la PWA, a partir del
logo del gimnasio o, si no tiene, un placeholder con sus iniciales sobre su
color primario.

Vive acá (no en `tenants`) para no acoplar `Gimnasio` a Pillow/PWA -- mismo
criterio de desacople que `calendario`/`turnos`.

No se persiste un `ImageField` nuevo en `Gimnasio`: el PNG se genera al
vuelo y se cachea con el cache default de Django (`notificaciones/views.py`
usa una clave que incluye `gimnasio.modificado`), así que un logo re-subido
invalida sola la versión vieja sin lógica extra de invalidación.

**El lienzo es el color de fondo del PROPIO logo** (`color_lienzo`), no el
fondo de la paleta. En el splash de Android el ícono flota, a tamaño fijo,
sobre `background_color` del manifest: si el logo trae un fondo opaco (JPEG,
PNG con fondo blanco o de marca) y el relleno del ícono es otro color, se ve
un rectángulo que desentona con la pantalla. Se toma el color más común del
BORDE del logo -- es el que va a tocar el relleno, así que el empalme queda
invisible -- y el manifest usa ese mismo color como `background_color`. Un
logo con borde transparente se pinta sobre el fondo de la paleta (el color
dominante del glifo lo dejaría invisible sobre sí mismo); sin logo, el
placeholder es una baldosa del color primario y el splash también.

Además se recortan los márgenes uniformes del logo antes de encajarlo: un
logo de 400×400 con la marca en el centro llegaba al ícono ocupando la mitad,
y en el splash (que ya lo achica) se veía diminuto.

**Riesgo aceptado a propósito**: sin logo, el placeholder usa la fuente
default de Pillow (no una tipografía cuidada) -- simplificación de MVP, ver
ISSUES.md.
"""

import io
from collections import Counter

from django.urls import reverse
from PIL import Image, ImageChops, ImageDraw, ImageFont

TAMANOS_PERMITIDOS = (192, 512)

#: Fracción del ícono que ocupa la marca. `any`: casi todo (Android y iOS
#: recortan las esquinas con su propia máscara, pero el lienzo es uniforme
#: así que no se pierde nada). `maskable`: la zona segura de la spec (círculo
#: central del 80%).
_OCUPACION_ANY = 0.92
_OCUPACION_MASKABLE = 0.80

_ALFA_MINIMO = 128
#: Diferencia de luminancia (0-255) a partir de la cual un píxel se considera
#: "marca" y no fondo al recortar márgenes -- tolera el ruido de un JPEG.
_TOLERANCIA_RECORTE = 24
#: Si menos de esta fracción del borde es opaca, el logo se trata como de
#: fondo transparente.
_BORDE_OPACO_MINIMO = 0.5


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


def _abrir_logo(gimnasio) -> Image.Image:
    with gimnasio.logo.open("rb") as archivo:
        original = Image.open(archivo)
        original.load()
    return original.convert("RGBA")


def _pixeles_del_borde(imagen: Image.Image):
    ancho, alto = imagen.size
    franjas = (
        imagen.crop((0, 0, ancho, 1)),
        imagen.crop((0, alto - 1, ancho, alto)),
        imagen.crop((0, 0, 1, alto)),
        imagen.crop((ancho - 1, 0, ancho, alto)),
    )
    for franja in franjas:
        yield from franja.getdata()


def _color_de_borde(imagen: Image.Image):
    """Color (r, g, b) más común del borde, o `None` si el borde es
    mayormente transparente."""
    borde = list(_pixeles_del_borde(imagen))
    opacos = [(r, g, b) for r, g, b, a in borde if a >= _ALFA_MINIMO]
    if not borde or len(opacos) / len(borde) < _BORDE_OPACO_MINIMO:
        return None
    return Counter(opacos).most_common(1)[0][0]


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _lienzo_de_logo(gimnasio, imagen: Image.Image) -> str:
    borde = _color_de_borde(imagen)
    return _hex(borde) if borde is not None else gimnasio.color_fondo_css


def color_lienzo(gimnasio) -> str:
    """Color hex sobre el que se pinta el ícono -- y, por lo tanto, el
    `background_color` del manifest, para que el splash sea una sola
    superficie continua con el ícono."""
    if gimnasio.logo:
        return _lienzo_de_logo(gimnasio, _abrir_logo(gimnasio))
    return gimnasio.color_primario_css


def _recortar_margenes(imagen: Image.Image, lienzo: str) -> Image.Image:
    """Recorta los márgenes que son del color del lienzo o transparentes.
    Todo vectorizado en Pillow: un logo de 2000 px son millones de píxeles,
    un bucle en Python acá se paga en cada regeneración."""
    alfa = imagen.getchannel("A").point(lambda a: 255 if a >= _ALFA_MINIMO else 0)
    fondo = Image.new("RGB", imagen.size, lienzo)
    distinto = (
        ImageChops.difference(imagen.convert("RGB"), fondo)
        .convert("L")
        .point(lambda v: 255 if v > _TOLERANCIA_RECORTE else 0)
    )
    marca = ImageChops.multiply(alfa, distinto)
    caja = marca.getbbox()
    if caja is None:
        return imagen
    return imagen.crop(caja)


def _componer(imagen: Image.Image, lienzo: str, size: int, ocupacion: float) -> Image.Image:
    salida = Image.new("RGBA", (size, size), lienzo)
    marca = _recortar_margenes(imagen, lienzo)
    # `thumbnail` solo achica; un logo chico (o ya recortado) tiene que
    # agrandarse hasta ocupar el ícono, así que se calcula la escala a mano.
    lado = max(1, int(size * ocupacion))
    escala = min(lado / marca.width, lado / marca.height)
    dimensiones = (max(1, round(marca.width * escala)), max(1, round(marca.height * escala)))
    encajada = marca.resize(dimensiones, Image.LANCZOS)
    posicion = ((size - encajada.width) // 2, (size - encajada.height) // 2)
    salida.alpha_composite(encajada, posicion)
    return salida.convert("RGB")


def generar_icono(gimnasio, size: int, maskable: bool = False) -> bytes:
    """Devuelve un PNG cuadrado de `size`x`size` px, en bytes."""
    if size not in TAMANOS_PERMITIDOS:
        raise ValueError(f"Tamaño de ícono no soportado: {size}")

    if gimnasio.logo:
        original = _abrir_logo(gimnasio)
        lienzo = _lienzo_de_logo(gimnasio, original)
        ocupacion = _OCUPACION_MASKABLE if maskable else _OCUPACION_ANY
        salida = _componer(original, lienzo, size, ocupacion)
    else:
        salida = _placeholder(gimnasio, size)

    buffer = io.BytesIO()
    salida.save(buffer, format="PNG")
    return buffer.getvalue()


def version_icono(gimnasio) -> int:
    """Milisegundos, no segundos: dos guardados en el mismo segundo (pasa en
    los tests, y en un doble submit) compartirían clave de cache y URL."""
    return int(gimnasio.modificado.timestamp() * 1000)


def icono_pwa_url(gimnasio, size: int = 192, maskable: bool = False) -> str:
    """URL del ícono con la versión en la query string. El navegador guarda el
    ícono al instalar la PWA y solo lo vuelve a pedir cuando la URL que
    figura en el manifest CAMBIA -- con una URL fija, re-subir el logo no
    cambiaba el ícono instalado ni el del splash."""
    nombre = "notificaciones:pwa_icono_maskable" if maskable else "notificaciones:pwa_icono"
    return f"{reverse(nombre, args=[gimnasio.slug, size])}?v={version_icono(gimnasio)}"
