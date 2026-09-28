"""El QR de la credencial de ingreso, en SVG para la web y en PNG para el PDF.

Django-free a propósito (`SimpleTestCase`, sin base), mismo criterio que
`rutinas/pdf.py`.

El QR es una llave AL PORTADOR: quien tenga la imagen abre la puerta. Este
módulo solo dibuja; quién puede verla lo decide la vista que la sirve (solo el
propio alumno o el staff de SU gimnasio, con `Cache-Control: no-store,
private`, igual que el padrón).

Decisiones que conviene no deshacer:

1. **Codifica EXACTAMENTE `Credencial.valor`, nada más.** Ni una URL, ni el
   nombre del alumno, ni el gimnasio: el lector compara el string leído contra
   `valor`, y cualquier dato de más sería un dato personal impreso en una
   tarjeta que se pierde.
2. **`segno.make_qr` y nunca `segno.make`**: para valores cortos `make` elige
   Micro QR, que la cámara del celular no lee.
3. **Nivel de corrección Q** (~25%): la tarjeta impresa se raya, se dobla y se
   gasta en el llavero. Con un token de 43 caracteres Q entra en la misma
   versión 4 que M, así que no agranda el código.
4. **Zona silenciosa de 4 módulos y fondo blanco pintado**: sin el borde el
   lector no encuentra el código, y sobre el color del paisaje del gimnasio un
   fondo transparente le baja el contraste.
5. **El SVG no lleva tamaño fijo** (solo `viewBox`): escala con el contenedor y
   no se pixela. El template que lo incluya tiene que darle un ancho.
"""

import io

import segno

NIVEL_ERROR = "q"
BORDE = 4
# Píxeles por módulo en el PNG. Un QR versión 4 son 33 módulos + 8 de borde =
# 41, o sea 410 px: sobra para imprimirlo a ~3 cm sin que fpdf2 lo pixele.
ESCALA_PNG = 10


def _codigo(valor):
    return segno.make_qr(valor, error=NIVEL_ERROR)


def svg_de(valor):
    """SVG para meter inline en el HTML (sin declaración XML ni `xmlns`)."""
    return _codigo(valor).svg_inline(
        border=BORDE, omitsize=True, dark="#000", light="#fff"
    )


def png_de(valor):
    """PNG en bytes, para `FPDF.image(io.BytesIO(...))` en las tarjetas."""
    salida = io.BytesIO()
    _codigo(valor).save(
        salida, kind="png", scale=ESCALA_PNG, border=BORDE,
        dark="#000", light="#fff",
    )
    return salida.getvalue()
