"""Piezas compartidas por los tres lectores de `.xlsx` (Proyecto 2).

Este módulo NO importa nada de Django ni de los modelos de dominio
(`Ejercicio`, `RutinaPlantilla`): recibe celdas, devuelve dataclasses. Es lo
que lo hace testeable con `SimpleTestCase` sin fixtures de tenant, y lo que
permite reusarlo desde `services.py` sin acoplar el parseo a la persistencia.

`normalizar_texto` vive acá (no en `matching.py`) porque el parseo la necesita
primero, para detectar encabezados; `matching.py` la importa desde acá -- un
solo lugar. Ojo: la importan también `ejercicios/` y **dos migraciones
históricas** (`rutinas/0006`, `ejercicios/0003`), siempre por la ruta
`importaciones.parsing`, que la fachada del paquete preserva.
"""

import unicodedata
from dataclasses import dataclass, field

# El vocabulario de entrenamiento no es uno solo: cada entrenador nombra las
# cosas distinto y las planillas compradas suelen venir con términos en inglés
# mezclados. "Microciclo" es semana y "sesión" es día -- no son sinónimos
# sueltos, es la jerga de periodización.
#
# Ojo con los alias de UNA letra ("s" por series, "r" por reps): no van. La
# segunda pasada de `detectar_columnas` matchea por prefijo en borde de
# palabra, y con alias de una letra cualquier encabezado corto se robaría una
# columna que no le corresponde.
#
# Las tildes NO hacen falta: `normalizar_texto` las saca antes de comparar, así
# que "día" y "sesión" ya están cubiertos por "dia" y "sesion" (el alias "día"
# que había acá era código muerto, mismo caso que el "músculo" que se sacó de
# ALIAS_BIBLIOTECA en 2026-08-26).
ALIAS_PLANTILLA = {
    "semana": ["semana", "sem", "week", "wk", "microciclo", "micro"],
    "dia": ["dia", "day", "sesion", "session", "jornada"],
    "ejercicio": [
        "ejercicio", "ejercicios", "exercise", "movimiento", "movement", "nombre",
    ],
    "series": ["series", "serie", "sets", "set"],
    "repeticiones": ["repeticiones", "reps", "repes", "rep", "repetitions"],
    "kilos": [
        "kilos", "kilogramos", "carga", "peso", "kg", "kgs", "load", "weight",
    ],
    "descanso": ["descanso", "pausa", "rest", "recuperacion"],
    "notas": [
        "notas", "nota", "observaciones", "obs", "comentarios", "notes", "comments",
    ],
}

ALIAS_BIBLIOTECA = {
    "nombre": ["nombre", "ejercicio", "ejercicios", "exercise"],
    # "categoria" es el encabezado que usan los gimnasios reales; faltaba, y
    # por eso un Excel de 748 ejercicios entraba entero sin clasificar.
    # "músculo" NO va: `normalizar_texto` saca las tildes antes de comparar,
    # así que ese alias era código muerto -- "musculo" ya lo cubre.
    "grupo_muscular": [
        "grupo muscular",
        "grupo_muscular",
        "categoria",
        "categorias",
        "grupo",
        "musculo",
        "zona",
    ],
    "url_video": ["video", "url_video", "link", "youtube"],
}


def normalizar_texto(texto):
    """lowercase + sin tildes + espacios colapsados. `None` -> `""`."""
    if not texto:
        return ""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", str(texto))
        if not unicodedata.combining(c)
    )
    return " ".join(sin_tildes.lower().split())


class ColumnaRequeridaFaltante(Exception):
    """El archivo no tiene una columna sin la cual no se puede importar nada.

    Lleva los encabezados que SÍ se leyeron y de QUÉ FILA salieron: es lo que
    le permite al staff entender qué miró la app en vez de quedarse
    adivinando. Antes de 2026-08-26 este caso devolvía una lista vacía y la
    app mostraba un preview de cero filas con el botón de confirmar
    habilitado, sin ningún mensaje.

    `fila` se sumó cuando la búsqueda de encabezado dejó de ser siempre la
    fila 1 (2026-08-31): decir "en la primera fila leí..." pasó a ser
    directamente falso, porque ahora se miran las primeras
    `FILAS_BUSQUEDA_ENCABEZADO`.
    """

    def __init__(self, campo, encabezados, *, fila=1):
        self.campo = campo
        self.fila = fila
        self.encabezados = [str(e) for e in encabezados if e is not None]
        super().__init__(f"Falta la columna '{campo}'")


def _prefijo_de_palabra(alias, encabezado):
    """`alias` abre `encabezado` y termina donde termina una palabra.

    "nombre" prefija "nombre del ejercicio" pero no "nombres": el corte tiene
    que caer en un borde, o "core" matchearía "coreografia".
    """
    if not encabezado.startswith(alias):
        return False
    resto = encabezado[len(alias):]
    return resto == "" or not resto[0].isalnum()


def detectar_columnas(encabezados, alias_por_campo):
    """Devuelve (campo_canonico -> índice de columna, advertencias).

    Dos pasadas. Primero coincidencia EXACTA del encabezado normalizado
    contra la lista de alias; después, solo para los campos que quedaron sin
    columna, coincidencia por PREFIJO en borde de palabra ("nombre" al
    principio de "nombre del ejercicio").

    Es prefijo y no "contiene" a propósito. Con "contiene", una fila de
    título como "Biblioteca de ejercicios 2026" -- el caso real que dejaba
    un preview vacío -- matchea el alias "ejercicios" y se hace pasar por la
    columna de nombre, que es peor que no detectar nada. Como prefijo, esa
    fila no matchea ("biblioteca" no es alias de nada) y el archivo se
    rechaza con un error que se entiende.

    El orden importa: la parcial es útil pero ambigua -- "Grupo muscular del
    ejercicio" empieza con "grupo muscular" y también contiene "ejercicio".
    Resolver primero todo lo exacto y no reasignar columnas ya tomadas evita
    que un campo se robe la columna de otro. Entre varios alias que prefijan
    el mismo encabezado gana el más largo, por la misma razón.

    Un campo sin ninguna columna que matchee simplemente no aparece en el
    dict de salida -- el caller decide si es requerido u opcional.
    """
    normalizados = [normalizar_texto(e) for e in encabezados]
    campos = {}
    advertencias = []

    for campo, alias in alias_por_campo.items():
        indices = [i for i, valor in enumerate(normalizados) if valor in alias]
        if not indices:
            continue
        campos[campo] = indices[0]
        if len(indices) > 1:
            advertencias.append(
                f"Se encontraron {len(indices)} columnas parecidas a "
                f"'{campo}'; se usó la columna {indices[0] + 1}."
            )

    tomadas = set(campos.values())
    for campo, alias in alias_por_campo.items():
        if campo in campos:
            continue
        mejor = None  # (largo del alias, -indice de columna, indice)
        for i, valor in enumerate(normalizados):
            if i in tomadas or not valor:
                continue
            contenidos = [a for a in alias if _prefijo_de_palabra(a, valor)]
            if not contenidos:
                continue
            candidato = (len(max(contenidos, key=len)), -i, i)
            if mejor is None or candidato > mejor:
                mejor = candidato
        if mejor is not None:
            campos[campo] = mejor[2]
            tomadas.add(mejor[2])
            advertencias.append(
                f"No hay una columna llamada exactamente '{campo}'; se usó "
                f"'{encabezados[mejor[2]]}' (columna {mejor[2] + 1})."
            )

    return campos, advertencias


@dataclass(frozen=True)
class ItemParseado:
    semana: int
    dia: int
    orden: int
    ejercicio_original: str
    series: int
    repeticiones: str
    kilos: str
    descanso: str
    notas: str
    # Van al final y con default a propósito: los tests del lector largo
    # comparan contra `ItemParseado(...)` construido con kwargs, y como el
    # lector largo también deja "", la igualdad del frozen dataclass sigue
    # dando True sin tocar un solo test.
    bloque: str = ""       # "A1" -- agrupa superseries
    dia_nombre: str = ""   # "Tren superior · Core"
    # La fila REAL de Excel, la que el staff ve en la planilla. Sin esto, un
    # item descartado más adelante (por largo, por semana fuera del ciclo)
    # solo se puede reportar por su `orden`, que es la posición dentro del día
    # y no le sirve a nadie para encontrar la celda.
    fila_excel: int = 0


@dataclass(frozen=True)
class FilaInvalida:
    fila_excel: int
    motivo: str


@dataclass(frozen=True)
class HojaParseada:
    nombre_hoja: str
    dias_por_semana: int
    items: list = field(default_factory=list)
    filas_invalidas: list = field(default_factory=list)
    # Por qué la hoja quedó con 0 items cuando falta una columna requerida
    # (`None` en el caso normal, con items). Sin esto, una hoja excluida por
    # falta de columna se ve idéntica a una hoja válida pero vacía -- el
    # staff no tiene forma de saber por qué (constraint no negociable:
    # "se excluye con motivo", fix post-review, hallazgo 2).
    motivo_exclusion: str | None = None
    # Advertencias de `detectar_columnas` (p. ej. columna duplicada) --
    # antes se calculaban y se descartaban sin llegar nunca al staff (fix
    # post-review, hallazgo 3).
    advertencias_columnas: list = field(default_factory=list)
    # Cómo se leyó la hoja. No es decoración: con dos layouts posibles y una
    # búsqueda de encabezado que puede caer en la fila equivocada, esto es lo
    # que le permite al staff (y a quien lo asista) ver de un vistazo si la
    # app entendió el archivo antes de confirmar nada.
    layout: str = ""            # "tabular" | "ancha"
    fila_encabezado: int = 0    # 1-indexed, la que se usó como títulos


def _mapa_merges(ws):
    """(fila, col) 1-indexed -> (fila_ancla, col_ancla) para cada celda
    dentro de un rango combinado. openpyxl devuelve `None` para toda celda
    de un merge salvo la esquina superior-izquierda; sin este mapa, una
    columna mergeada verticalmente (típico de "Semana 1" armada a mano)
    se leería como si esas filas no tuvieran valor."""
    mapa = {}
    for rango in ws.merged_cells.ranges:
        ancla = (rango.min_row, rango.min_col)
        for fila in range(rango.min_row, rango.max_row + 1):
            for col in range(rango.min_col, rango.max_col + 1):
                mapa[(fila, col)] = ancla
    return mapa


def _valor_celda(ws, fila, col, mapa_merges):
    fila_ancla, col_ancla = mapa_merges.get((fila, col), (fila, col))
    return ws.cell(row=fila_ancla, column=col_ancla).value


def _fila_vacia(valores):
    return all(v is None or str(v).strip() == "" for v in valores)




# Cuántas filas se miran, desde arriba, buscando la de los títulos. 15 alcanza
# de sobra para el caso real que motivó esto (la planilla del primer cliente
# pago la tiene en la 12, debajo del logo, el objetivo y las fechas) sin
# arriesgar que una fila de datos de más abajo se haga pasar por encabezado.
FILAS_BUSQUEDA_ENCABEZADO = 15


@dataclass(frozen=True)
class Encabezado:
    """Dónde están los títulos de una hoja y qué columna es cada campo.

    `fila` es 1-indexed (la numeración que ve el staff en Excel, la misma que
    usa `FilaInvalida.fila_excel`); los índices de `campos` son 0-indexed
    sobre la lista `valores`, que es lo que espera `detectar_columnas`.
    """

    fila: int
    valores: list
    campos: dict
    advertencias: list = field(default_factory=list)


def _valores_de_fila(ws, fila, ncols):
    return [ws.cell(row=fila, column=c).value for c in range(1, ncols + 1)]


def buscar_fila_encabezado(ws, alias_por_campo, requeridos, *,
                           max_filas=FILAS_BUSQUEDA_ENCABEZADO):
    """La PRIMERA fila (de las primeras `max_filas`) donde se detectan TODOS
    los campos de `requeridos`. `None` si ninguna sirve.

    Antes los tres lectores hacían `next(ws.iter_rows(min_row=1, max_row=1))`,
    así que cualquier título arriba de la tabla -- el caso más común en una
    planilla hecha a mano -- rechazaba el archivo entero.

    Es "la primera que califica" y no "la que más campos detecta" a propósito:
    con la segunda regla, en una matriz por semanas la fila de subcampos
    (Series/Reps/Carga repetidos cuatro veces) ganaría siempre y las columnas
    quedarían corridas. Exigir el conjunto completo de requeridos y quedarse
    con la primera es lo que hace que una fila de DATOS no pueda hacerse pasar
    por encabezado: sus celdas no son alias de nada.
    """
    ncols = ws.max_column or 0
    for fila in range(1, min(ws.max_row or 0, max_filas) + 1):
        valores = _valores_de_fila(ws, fila, ncols)
        if _fila_vacia(valores):
            continue
        campos, advertencias = detectar_columnas(valores, alias_por_campo)
        if all(campo in campos for campo in requeridos):
            return Encabezado(
                fila=fila, valores=valores, campos=campos, advertencias=advertencias
            )
    return None


def mejor_encabezado_parcial(ws, alias_por_campo, *,
                             max_filas=FILAS_BUSQUEDA_ENCABEZADO):
    """La fila que MÁS se parece a un encabezado, para el mensaje de error.

    No sirve para parsear (por eso está separada de `buscar_fila_encabezado`):
    sirve para que el staff vea qué leyó la app y entienda por qué no encontró
    la tabla. Cae en la fila 1 si ninguna detecta nada, que es exactamente lo
    que se mostraba antes de que existiera la búsqueda multi-fila.
    """
    ncols = ws.max_column or 0
    mejor = None
    for fila in range(1, min(ws.max_row or 0, max_filas) + 1):
        valores = _valores_de_fila(ws, fila, ncols)
        if _fila_vacia(valores):
            continue
        campos, advertencias = detectar_columnas(valores, alias_por_campo)
        candidato = Encabezado(
            fila=fila, valores=valores, campos=campos, advertencias=advertencias
        )
        if mejor is None or len(campos) > len(mejor.campos):
            mejor = candidato
    if mejor is None:
        return Encabezado(fila=1, valores=_valores_de_fila(ws, 1, ncols), campos={})
    return mejor
