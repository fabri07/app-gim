"""Parsing puro de archivos `.xlsx` (Proyecto 2).

Este módulo NO importa nada de Django ni de los modelos de dominio
(`Ejercicio`, `RutinaPlantilla`): recibe un archivo, devuelve dataclasses.
Es lo que lo hace testeable con `SimpleTestCase` sin fixtures de tenant, y
lo que permite reusarlo desde `services.py` sin acoplar el parseo a la
persistencia. `normalizar_texto` vive acá (no en `matching.py`) porque este
módulo la necesita primero, para detectar encabezados; `matching.py` la
importa desde acá para normalizar nombres de ejercicio -- un solo lugar.
"""

import unicodedata

ALIAS_PLANTILLA = {
    "semana": ["semana", "week", "sem"],
    "dia": ["dia", "día", "day"],
    "ejercicio": ["ejercicio", "ejercicios", "exercise", "movimiento"],
    "series": ["series", "serie", "sets"],
    "repeticiones": ["repeticiones", "reps", "repes", "rep"],
    "descanso": ["descanso", "pausa", "rest"],
    "notas": ["notas", "nota", "observaciones", "comentarios"],
}

ALIAS_BIBLIOTECA = {
    "nombre": ["nombre", "ejercicio", "ejercicios", "exercise"],
    "grupo_muscular": ["grupo muscular", "grupo_muscular", "musculo", "músculo", "zona"],
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


def detectar_columnas(encabezados, alias_por_campo):
    """Devuelve (campo_canonico -> índice de columna, advertencias).

    Para cada campo canónico, busca la PRIMERA columna (izquierda a
    derecha) cuyo encabezado normalizado esté en su lista de alias. Un
    campo sin ninguna columna que matchee simplemente no aparece en el
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
    return campos, advertencias
