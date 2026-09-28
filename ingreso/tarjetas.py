"""Tarjetas imprimibles con el QR de ingreso, en PDF.

Django-free a propósito (`SimpleTestCase`, sin base), mismo reparto que
`rutinas/pdf.py` ↔ `RutinaAsignadaPdfView`: acá se arman bytes, y la vista
(`ingreso/views.py::TarjetasPdfView`) es la única capa que sabe de
`HttpResponse`, de quién puede pedirlas y de qué credenciales entran.

Existe por la decisión que ordena todo el QR fijo: **tiene que servir
impreso**, para el alumno que no tiene smartphone. El proyecto no tiene ningún
estilo de impresión, así que la tarjeta es un PDF y no CSS de print.

Decisiones que conviene no deshacer:

1. **Cada tarjeta es una llave al portador.** Lleva el nombre corto del alumno,
   el del gimnasio y el QR. Nada más: ni email, ni teléfono, ni el `valor`
   escrito en texto (se podría tipear en un lector sin tener la tarjeta), ni
   una URL. Este módulo ni siquiera recibe esos datos.
2. **Tamaño de tarjeta de crédito (ID-1, 85,6 × 54 mm), diez por A4** en una
   grilla fija de 2 × 5. La página se agrega a mano cada diez tarjetas, con el
   salto automático apagado: así ninguna tarjeta —y ningún QR— puede quedar
   partida entre dos hojas. El borde de cada tarjeta es la línea de corte,
   punteada y gris claro.
3. **El QR impreso mide `LADO_QR_MM` con su zona silenciosa**, y el código
   propiamente dicho (sin el borde blanco) nunca baja de `MINIMO_CODIGO_MM`,
   ~2,5 cm: más chico, la cámara del lector tiene que acercarse tanto que
   pierde el foco. `lado_del_codigo_mm` hace esa cuenta y el test la fija para
   el `valor` más largo que admite el modelo.
4. **Fuentes core con cp1252**, como `rutinas/pdf.py`: cubre tildes, ñ, ü y
   la raya sin embeber ninguna fuente. Lo que cp1252 no tiene (una «Ł», un
   emoji) no puede tumbar la generación de las tarjetas de TODO el gimnasio
   por un solo nombre: se degrada (primero sacando el diacrítico, después a
   «?») en `_imprimible`.
"""

import io
import unicodedata
from dataclasses import dataclass

from fpdf import FPDF

from ingreso import qr

#: Tarjeta ID-1 (la de crédito): entra en cualquier billetera o portacarnet.
ANCHO_TARJETA_MM = 85.6
ALTO_TARJETA_MM = 54.0
COLUMNAS = 2
FILAS = 5
TARJETAS_POR_HOJA = COLUMNAS * FILAS

#: Lado de la imagen del QR, zona silenciosa incluida.
LADO_QR_MM = 42.0
#: Lado mínimo del código SIN la zona silenciosa (ver docstring, punto 3).
MINIMO_CODIGO_MM = 25.0

_ANCHO_HOJA_MM = 210.0
_ALTO_HOJA_MM = 297.0
_MARGEN_IZQ_MM = (_ANCHO_HOJA_MM - COLUMNAS * ANCHO_TARJETA_MM) / 2
_MARGEN_SUP_MM = (_ALTO_HOJA_MM - FILAS * ALTO_TARJETA_MM) / 2
_RELLENO_MM = 5.0
# La zona silenciosa del PNG ya es blanca: el QR puede ir casi pegado al
# borde derecho de la tarjeta sin quedar sin margen.
_AIRE_QR_MM = 2.0
_SEPARACION_MM = 2.0

_ENCODING = "cp1252"


@dataclass(frozen=True)
class Tarjeta:
    """Lo único que se imprime de un alumno. La vista arma una por
    credencial QR vigente; `nombre` es `services.nombre_corto`."""

    nombre: str
    valor: str


def lado_del_codigo_mm(valor):
    """Lado impreso del QR de `valor` SIN la zona silenciosa, en mm."""
    modulos = qr.modulos_de(valor)
    return LADO_QR_MM * modulos / (modulos + 2 * qr.BORDE)


def posicion_de(indice):
    """`(x, y)` en mm de la esquina superior izquierda de la tarjeta número
    `indice` DENTRO de su hoja (0 a `TARJETAS_POR_HOJA - 1`)."""
    fila, columna = divmod(indice % TARJETAS_POR_HOJA, COLUMNAS)
    return (
        _MARGEN_IZQ_MM + columna * ANCHO_TARJETA_MM,
        _MARGEN_SUP_MM + fila * ALTO_TARJETA_MM,
    )


def _imprimible(texto):
    """`texto` reducido a lo que la fuente core puede dibujar: nunca levanta.

    Primero se intenta tal cual (lo normal: tildes y ñ están en cp1252).
    Después, carácter por carácter, se prueba la forma sin diacrítico («Ł» no
    se descompone, «ș» sí) y, si tampoco entra, «?».
    """
    texto = " ".join((texto or "").split())
    try:
        texto.encode(_ENCODING)
        return texto
    except UnicodeEncodeError:
        pass
    salida = []
    for caracter in texto:
        try:
            caracter.encode(_ENCODING)
            salida.append(caracter)
            continue
        except UnicodeEncodeError:
            pass
        base = "".join(
            c for c in unicodedata.normalize("NFKD", caracter)
            if not unicodedata.combining(c)
        )
        try:
            base.encode(_ENCODING)
            salida.append(base or "?")
        except UnicodeEncodeError:
            salida.append("?")
    return "".join(salida)


def _en_una_linea(pdf, texto, ancho, estilo, puntos_max, puntos_min):
    """Deja `pdf` con la fuente del tamaño más grande (entre `puntos_max` y
    `puntos_min`) en que `texto` entra en `ancho` mm, y devuelve el texto a
    escribir: recortado con «…» si ni al mínimo entra. Un nombre de gimnasio
    largo no puede pisar el QR."""
    for puntos in range(puntos_max, puntos_min - 1, -1):
        pdf.set_font("Helvetica", estilo, puntos)
        if pdf.get_string_width(texto) <= ancho:
            return texto
    recortado = texto
    while recortado and pdf.get_string_width(recortado + "…") > ancho:
        recortado = recortado[:-1]
    return recortado.rstrip() + "…"


def _dibujar_tarjeta(pdf, tarjeta, nombre_gimnasio, x, y):
    pdf.set_draw_color(170, 170, 170)
    pdf.set_line_width(0.2)
    with pdf.local_context():
        pdf.set_dash_pattern(dash=1.5, gap=1.5)
        pdf.rect(x, y, ANCHO_TARJETA_MM, ALTO_TARJETA_MM)

    x_qr = x + ANCHO_TARJETA_MM - _AIRE_QR_MM - LADO_QR_MM
    y_qr = y + (ALTO_TARJETA_MM - LADO_QR_MM) / 2
    pdf.image(io.BytesIO(qr.png_de(tarjeta.valor)), x=x_qr, y=y_qr, w=LADO_QR_MM, h=LADO_QR_MM)

    x_texto = x + _RELLENO_MM
    ancho_texto = x_qr - _SEPARACION_MM - x_texto

    pdf.set_text_color(90, 90, 90)
    linea = _en_una_linea(pdf, nombre_gimnasio, ancho_texto, "B", 10, 6)
    pdf.set_xy(x_texto, y + _RELLENO_MM + 2)
    pdf.cell(ancho_texto, 5, linea)

    pdf.set_text_color(0, 0, 0)
    linea = _en_una_linea(pdf, tarjeta.nombre, ancho_texto, "B", 16, 9)
    pdf.set_xy(x_texto, y + ALTO_TARJETA_MM / 2 - 4)
    pdf.cell(ancho_texto, 8, linea)

    pdf.set_text_color(90, 90, 90)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_xy(x_texto, y + ALTO_TARJETA_MM - _RELLENO_MM - 8)
    pdf.multi_cell(ancho_texto, 3.5, "Mostrá este código\nen el lector de la puerta.")


def generar_pdf_credenciales(nombre_gimnasio, tarjetas):
    """Los bytes del PDF con una tarjeta por elemento de `tarjetas` (en ese
    orden), diez por hoja A4."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.core_fonts_encoding = _ENCODING
    pdf.set_auto_page_break(auto=False)
    pdf.set_title("Tarjetas de ingreso")
    nombre_gimnasio = _imprimible(nombre_gimnasio)

    tarjetas = list(tarjetas)
    if not tarjetas:
        pdf.add_page()
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, "No hay códigos de ingreso para imprimir.")
        return bytes(pdf.output())

    for indice, tarjeta in enumerate(tarjetas):
        if indice % TARJETAS_POR_HOJA == 0:
            pdf.add_page()
        x, y = posicion_de(indice)
        _dibujar_tarjeta(
            pdf,
            Tarjeta(nombre=_imprimible(tarjeta.nombre), valor=tarjeta.valor),
            nombre_gimnasio,
            x,
            y,
        )
    return bytes(pdf.output())
