"""Lector de planes de entrenamiento en PDF (2026-09-07).

Un PDF no tiene celdas. Lo que sí tiene, casi siempre, es una tabla dibujada
(el plan exportado desde Excel, Google Sheets o Simplify Trainers), y a veces
solo texto suelto (un plan escrito en Word, o una planilla exportada sin
líneas de cuadrícula). Este módulo lo lee en dos intentos, del más exacto al
más tolerante:

1. **Tablas.** `pdfplumber` reconstruye las tablas de cada página; todas se
   apilan en UNA hoja de un `openpyxl.Workbook` en memoria y esa hoja pasa por
   los mismos lectores que el Excel (`leer_hoja_plantilla`, en modo
   tolerante). Los lectores solo tocan `cell().value`, `max_row`,
   `max_column`, `merged_cells` y `title`, así que un workbook armado a mano
   es un adaptador perfecto y **no hay que tocarlos**.
2. **Texto.** Si no salió ninguna tabla, se lee el texto línea por línea con
   una máquina de estados que prioriza lo que de verdad importa del PDF:
   qué ejercicios, en qué día y en qué semanas. Los detalles (series,
   repeticiones, kilos) se toman si se reconocen con claridad y, si no, el
   item queda "a completar" (`ItemParseado.series=None`) para que el
   entrenador lo termine desde la plantilla. **Nunca se inventa un valor.**

Regla de producto detrás de todo esto: la lectura del PDF no tiene que ser
100% certera. Lo que no se puede permitir es perder un ejercicio del plan.

Django-free, igual que el resto del paquete. Fotos y escaneos (PDF sin capa
de texto) se rechazan con `PdfSinTexto`: no hay OCR en el free tier de Render
y el mensaje al staff es mejor que un plan vacío.
"""

import re
from dataclasses import dataclass

import openpyxl
import pdfplumber
from pdfminer.pdfparser import PDFException
from pdfminer.psparser import PSException
from pdfplumber.utils.exceptions import PdfminerException

from importaciones.parsing.ancha import (
    RE_BLOQUE,
    RE_DIA,
    SUBCAMPOS_IGNORADOS,
    _es_subcampo_conocido,
)
from importaciones.parsing.comun import (
    ALIAS_PLANTILLA,
    HojaParseada,
    ItemParseado,
    normalizar_texto,
)

# Un plan de entrenamiento no tiene 30 páginas. El tope existe porque pdfminer
# es lento (del orden de un segundo por página con tablas) y el request tiene
# 30 s de gunicorn: un PDF de 200 páginas subido por error sería un 502.
MAX_PAGINAS_PDF = 30

# Errores de un archivo que dice ser PDF y no lo es (o está roto). Se traducen
# a `ImportacionInvalida` en `services.py`, igual que `ERRORES_ARCHIVO_INVALIDO`.
ERRORES_PDF = (PdfminerException, PDFException, PSException)

class PdfSinTexto(Exception):
    """Ninguna página tiene texto: es una foto o un escaneo."""


class PdfDemasiadoLargo(Exception):
    def __init__(self, paginas):
        self.paginas = paginas
        super().__init__(f"El PDF tiene {paginas} páginas")


def leer_pdf(archivo, nombre_hoja):
    """`HojaParseada` con el plan del PDF. `nombre_hoja` es el nombre del
    archivo sin extensión: termina como nombre de la `RutinaPlantilla`."""
    archivo.seek(0)
    with pdfplumber.open(archivo) as documento:
        if len(documento.pages) > MAX_PAGINAS_PDF:
            raise PdfDemasiadoLargo(len(documento.pages))
        tablas = []
        lineas = []
        for pagina in documento.pages:
            tablas.extend(_tablas_de(pagina))
            texto = pagina.extract_text() or ""
            lineas.extend(texto.splitlines())

    if not any(linea.strip() for linea in lineas):
        raise PdfSinTexto()

    if tablas:
        hoja = _leer_como_tabla(tablas, nombre_hoja)
        if hoja.items:
            return hoja
    return leer_texto_tolerante(lineas, nombre_hoja)


def _tablas_de(pagina):
    """Solo tablas con líneas dibujadas (la estrategia por defecto).

    Se probó la estrategia `text` de pdfplumber como reserva para tablas sin
    bordes y se descartó: alinea por posición y parte palabras por la mitad
    ("PLAN DE ENTRE" | "NAMIEN" | "TO - AG"), o sea produce columnas
    plausibles con basura adentro. Para un PDF sin bordes está el lector de
    texto, que al menos no inventa estructura.
    """
    tablas = pagina.extract_tables()
    # Una "tabla" de una o dos columnas es texto con un borde: no aporta
    # estructura y, apilada, corre las columnas de las tablas reales.
    return [t for t in tablas if t and max(len(f) for f in t) >= 3]


# ---------------------------------------------------------------------------
# Intento 1: tablas -> workbook en memoria -> lectores del Excel
# ---------------------------------------------------------------------------

def _fila_normalizada(fila):
    return tuple(normalizar_texto(c) for c in fila)


# Un salto de línea seguido de viñeta es un renglón de verdad (el marcador de
# día trae "DÍA 2\n• TREN SUPERIOR\n• CORE"); cualquier otro es la celda
# ajustando un nombre largo al ancho de la columna ("PUENTE\nSUPINO").
RE_SALTO_POR_ANCHO = re.compile(r"\n(?![\s•\-·*–—])")


def _sin_saltos_por_ancho(valor):
    if valor in ("", None):
        return None
    return RE_SALTO_POR_ANCHO.sub(" ", str(valor)).strip()


def _leer_como_tabla(tablas, nombre_hoja):
    """Apila las tablas de todas las páginas en una sola hoja.

    Excel repite las filas de título en cada página ("repetir filas de
    título"), así que las dos primeras filas de la primera tabla se recuerdan
    y se saltean cada vez que vuelven a aparecer: sin esto, cada encabezado
    repetido caía en el lector como un ejercicio llamado "Series" con series
    ilegibles.
    """
    from importaciones.parsing import leer_hoja_plantilla  # evita el ciclo

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = nombre_hoja[:31] or "PDF"  # límite de openpyxl para títulos
    encabezados = {_fila_normalizada(f) for f in tablas[0][:2]}
    vistos = 0
    for tabla in tablas:
        for fila in tabla:
            clave = _fila_normalizada(fila)
            if clave in encabezados:
                vistos += 1
                if vistos > 2:
                    continue
            ws.append([_sin_saltos_por_ancho(c) for c in fila])
    return leer_hoja_plantilla(ws, tolerante=True)


# ---------------------------------------------------------------------------
# Intento 2: texto suelto, línea por línea
# ---------------------------------------------------------------------------

RE_SEMANAS_EN_LINEA = re.compile(r"(?:semana|sem|week|wk|microciclo|micro)\s*(\d+)")
RE_VINETA = re.compile(r"^[\s•\-·*–—]+")
RE_SERIES_X_REPS = re.compile(r"^(\d+)\s*[x×]\s*(\d+(?:-\d+)?)$", re.IGNORECASE)
RE_KILOS = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:kg|kgs|k)$", re.IGNORECASE)
RE_RANGO_REPS = re.compile(r"^\d+-\d+$")
RE_ENTERO = re.compile(r"^\d+$")
RE_KILOS_PEGADO = re.compile(r"^(\d+(?:[.,]\d+)?)(kg|kgs|k)$", re.IGNORECASE)

# Palabras que solas forman una línea de encabezado, no un ejercicio.
PALABRAS_ENCABEZADO = (
    {alias for aliases in ALIAS_PLANTILLA.values() for alias in aliases}
    | SUBCAMPOS_IGNORADOS
    | {"ejercicios", "ejercicio", "videos", "video", "carga", "peso"}
)
MIN_LETRAS_NOMBRE = 3


@dataclass
class _Semana:
    series: int | None = None
    repeticiones: str = ""
    kilos: str = ""


def _es_linea_de_encabezado(tokens):
    palabras = [normalizar_texto(t) for t in tokens if not re.search(r"\d", t)]
    return bool(palabras) and all(
        p in PALABRAS_ENCABEZADO or _es_subcampo_conocido(p) for p in palabras
    )


def _separar_bloque(tokens):
    if tokens and RE_BLOQUE.match(normalizar_texto(tokens[0])):
        return tokens[0].rstrip(".").upper().replace(" ", ""), tokens[1:]
    return "", tokens


def _separar_nombre(tokens):
    """`(nombre, tokens numéricos)`: el nombre es todo hasta el primer token
    que empieza con un dígito."""
    for i, token in enumerate(tokens):
        if i > 0 and token[0].isdigit():
            return " ".join(tokens[:i]), tokens[i:]
    return " ".join(tokens), []


def _repartir_detalles(tokens, cantidad_semanas):
    """Reparte los números que siguen al nombre entre las semanas, en orden.

    "4 20 4 25" -> (4, "20") y (4, "25"). "4x8 40kg 4x8 42kg" -> (4, "8",
    "40kg") y (4, "8", "42kg"). Lo que no alcanza queda a completar; lo que
    sobra se ignora. Best-effort a propósito: el entrenador revisa el preview.
    """
    semanas = [_Semana() for _ in range(cantidad_semanas)]
    actual = 0
    series_pendiente = None

    def _cerrar(series, reps):
        nonlocal actual, series_pendiente
        if actual < cantidad_semanas:
            semanas[actual].series = series
            semanas[actual].repeticiones = reps
        actual += 1
        series_pendiente = None

    for token in tokens:
        token = token.strip(",;")
        if not token:
            continue
        match = RE_SERIES_X_REPS.match(token)
        if match:
            _cerrar(int(match.group(1)), match.group(2))
            continue
        if RE_KILOS.match(token) or RE_KILOS_PEGADO.match(token):
            destino = actual - 1 if series_pendiente is None and actual > 0 else actual
            if destino < cantidad_semanas:
                semanas[destino].kilos = token
            continue
        if RE_ENTERO.match(token):
            if series_pendiente is None:
                series_pendiente = int(token)
            else:
                _cerrar(series_pendiente, token)
            continue
        if RE_RANGO_REPS.match(token) and series_pendiente is not None:
            _cerrar(series_pendiente, token)
            continue
        # "seg", "min", "AMRAP", texto suelto: no se interpreta.
    if series_pendiente is not None and actual < cantidad_semanas:
        semanas[actual].series = series_pendiente
    return semanas


def _nombre_del_dia(texto):
    resto = re.sub(
        r"^\s*(d[ií]a|day|sesi[oó]n|session|jornada)\s*\d+\s*", "", texto,
        flags=re.IGNORECASE,
    )
    return RE_VINETA.sub("", resto).strip(" :·-")


def leer_texto_tolerante(lineas, nombre_hoja):
    """Lee un plan desde texto suelto. Ver el docstring del módulo."""
    semanas_activas = [1]
    dia_actual, nombre_dia = 1, ""
    partes_nombre_dia = []
    en_encabezado_de_dia = False
    hubo_dia = False
    hubo_marcador = False  # ya apareció un "DÍA n" o un "SEMANA n"
    items = []
    contador_orden = {}

    for numero_linea, cruda in enumerate(lineas, start=1):
        linea = cruda.strip()
        if not linea:
            continue
        normalizada = normalizar_texto(linea)

        semanas = [int(n) for n in RE_SEMANAS_EN_LINEA.findall(normalizada)]
        if semanas:
            semanas_activas = sorted(set(semanas))
            en_encabezado_de_dia = False
            hubo_marcador = True
            continue

        match_dia = RE_DIA.match(normalizada)
        if match_dia:
            dia_actual = int(match_dia.group(2))
            hubo_dia = True
            hubo_marcador = True
            partes_nombre_dia = []
            resto = _nombre_del_dia(linea)
            # "DIA 1 - CORE A1. Plancha 4 20": el ejercicio puede venir pegado
            # en la misma línea, detrás de su código de bloque.
            tokens = resto.split()
            corte = next(
                (i for i, t in enumerate(tokens) if i > 0 and RE_BLOQUE.match(normalizar_texto(t))),
                None,
            )
            if corte is not None:
                partes_nombre_dia.append(" ".join(tokens[:corte]))
                nombre_dia = " · ".join(p for p in partes_nombre_dia if p)
                en_encabezado_de_dia = False
                linea = " ".join(tokens[corte:])
            else:
                if resto:
                    partes_nombre_dia.append(resto)
                nombre_dia = " · ".join(p for p in partes_nombre_dia if p)
                en_encabezado_de_dia = True
                continue

        if en_encabezado_de_dia and RE_VINETA.match(linea) and not re.search(r"\d", linea):
            partes_nombre_dia.append(RE_VINETA.sub("", linea).strip())
            nombre_dia = " · ".join(p for p in partes_nombre_dia if p)
            continue
        en_encabezado_de_dia = False

        tokens = RE_VINETA.sub("", linea).split()
        if not tokens or _es_linea_de_encabezado(tokens):
            continue
        bloque, tokens = _separar_bloque(tokens)
        nombre, numericos = _separar_nombre(tokens)
        if sum(c.isalpha() for c in nombre) < MIN_LETRAS_NOMBRE:
            continue
        # Antes del primer "DÍA n"/"SEMANA n", una línea sin números es el
        # título del documento ("PLAN DE ENTRENAMIENTO - AGOSTO"), el nombre
        # del alumno o del gimnasio: no un ejercicio a completar.
        if not hubo_marcador and not numericos and not bloque:
            continue

        detalles = _repartir_detalles(numericos, len(semanas_activas))
        for semana, detalle in zip(semanas_activas, detalles):
            clave = (semana, dia_actual)
            contador_orden[clave] = contador_orden.get(clave, 0) + 1
            items.append(ItemParseado(
                semana=semana,
                dia=dia_actual,
                orden=contador_orden[clave],
                ejercicio_original=nombre,
                series=detalle.series,
                repeticiones=detalle.repeticiones,
                kilos=detalle.kilos,
                descanso="",
                notas="",
                bloque=bloque,
                dia_nombre=nombre_dia,
                fila_excel=numero_linea,
            ))

    if not items or not hubo_dia and len(items) < 2:
        return HojaParseada(
            nombre_hoja=nombre_hoja,
            dias_por_semana=0,
            layout="pdf_texto",
            motivo_exclusion=(
                "Leí el texto del PDF pero no reconocí ejercicios ni días de "
                "entrenamiento. Si el plan está en una tabla, exportalo desde "
                "Excel o Google Sheets con las líneas de cuadrícula visibles."
            ),
        )
    return HojaParseada(
        nombre_hoja=nombre_hoja,
        dias_por_semana=max(i.dia for i in items),
        items=items,
        layout="pdf_texto",
    )
