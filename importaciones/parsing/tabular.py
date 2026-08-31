"""Los dos lectores de layout TABULAR: un encabezado, un registro por fila.

Van juntos porque son el mismo patrón, no por tamaño. `leer_hoja_larga`
espera una fila por combinación (semana, día, ejercicio); `leer_hoja_biblioteca`
una fila por ejercicio suelto. La matriz ancha, donde una fila de Excel produce
un item POR SEMANA, vive en `ancha.py` -- es otro patrón y no se lee junto con
estos.
"""

from importaciones.parsing.comun import (
    ALIAS_BIBLIOTECA,
    ALIAS_PLANTILLA,
    ColumnaRequeridaFaltante,
    FilaInvalida,
    HojaParseada,
    ItemParseado,
    _fila_vacia,
    _mapa_merges,
    _valor_celda,
    buscar_fila_encabezado,
    mejor_encabezado_parcial,
)

# Sin estas tres columnas no hay nada que importar en una plantilla. El orden
# importa: es el que decide qué columna se nombra en el mensaje de error.
CAMPOS_REQUERIDOS_PLANTILLA = ("ejercicio", "series", "repeticiones")

def leer_hoja_larga(ws):
    """Parsea una hoja de un archivo de PLANTILLAS. `ws` es una worksheet
    de `openpyxl` ya abierta (no toca el filesystem acá)."""
    encabezado = buscar_fila_encabezado(ws, ALIAS_PLANTILLA, CAMPOS_REQUERIDOS_PLANTILLA)
    if encabezado is None:
        # Se nombra la primera requerida que no aparece ni en la fila que MÁS
        # se parecía a un encabezado: es el dato accionable, no "no encontré
        # la tabla".
        parcial = mejor_encabezado_parcial(ws, ALIAS_PLANTILLA)
        faltante = next(
            c for c in CAMPOS_REQUERIDOS_PLANTILLA if c not in parcial.campos
        )
        return HojaParseada(
            nombre_hoja=ws.title,
            dias_por_semana=0,
            motivo_exclusion=f"No se pudo importar: falta la columna '{faltante}'",
            advertencias_columnas=parcial.advertencias,
        )

    campos = encabezado.campos
    advertencias = encabezado.advertencias
    mapa_merges = _mapa_merges(ws)
    ncols = len(encabezado.valores)
    items = []
    filas_invalidas = []
    contador_orden = {}  # (semana, dia) -> próximo orden

    for fila_idx in range(encabezado.fila + 1, ws.max_row + 1):
        valores = [_valor_celda(ws, fila_idx, c, mapa_merges) for c in range(1, ncols + 1)]
        if _fila_vacia(valores):
            continue

        ejercicio = valores[campos["ejercicio"]]
        if not ejercicio or not str(ejercicio).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta el nombre del ejercicio"))
            continue

        series_raw = valores[campos["series"]]
        try:
            series = int(series_raw)
        except (TypeError, ValueError):
            filas_invalidas.append(
                FilaInvalida(fila_idx, "La columna 'series' no es un número")
            )
            continue

        repeticiones = valores[campos["repeticiones"]]
        if repeticiones is None or not str(repeticiones).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta 'repeticiones'"))
            continue

        semana_raw = valores[campos["semana"]] if "semana" in campos else None
        try:
            semana = int(semana_raw) if semana_raw is not None else 1
        except (TypeError, ValueError):
            semana = 1

        dia_raw = valores[campos["dia"]] if "dia" in campos else None
        try:
            dia = int(dia_raw) if dia_raw is not None else 1
        except (TypeError, ValueError):
            filas_invalidas.append(FilaInvalida(fila_idx, "La columna 'dia' no es un número"))
            continue

        clave_orden = (semana, dia)
        contador_orden[clave_orden] = contador_orden.get(clave_orden, 0) + 1

        kilos = valores[campos["kilos"]] if "kilos" in campos else None
        descanso = valores[campos["descanso"]] if "descanso" in campos else None
        notas = valores[campos["notas"]] if "notas" in campos else None

        items.append(ItemParseado(
            semana=semana,
            dia=dia,
            orden=contador_orden[clave_orden],
            ejercicio_original=str(ejercicio).strip(),
            series=series,
            repeticiones=str(repeticiones).strip(),
            kilos=str(kilos).strip() if kilos else "",
            descanso=str(descanso).strip() if descanso else "",
            notas=str(notas).strip() if notas else "",
            fila_excel=fila_idx,
        ))

    dias_por_semana = max((i.dia for i in items), default=0)
    return HojaParseada(
        nombre_hoja=ws.title,
        dias_por_semana=dias_por_semana,
        items=items,
        filas_invalidas=filas_invalidas,
        advertencias_columnas=advertencias,
    )


def leer_hoja_biblioteca(ws):
    """Parsea una hoja del import de BIBLIOTECA: solo nombre + grupo
    muscular (opcional) + video (opcional), sin días/semanas/series."""
    encabezado = buscar_fila_encabezado(ws, ALIAS_BIBLIOTECA, ("nombre",))

    if encabezado is None:
        encabezado = mejor_encabezado_parcial(ws, ALIAS_BIBLIOTECA)
        encabezados = encabezado.valores
        # Antes esto devolvía `[], [], advertencias` y la app mostraba un
        # preview vacío con el botón de confirmar habilitado. Sin el nombre
        # del ejercicio no hay nada que importar: es un error, no un archivo
        # de cero filas.
        raise ColumnaRequeridaFaltante("nombre", encabezados, fila=encabezado.fila)

    campos = encabezado.campos
    advertencias = encabezado.advertencias
    mapa_merges = _mapa_merges(ws)
    ncols = len(encabezado.valores)
    items = []
    filas_invalidas = []

    for fila_idx in range(encabezado.fila + 1, ws.max_row + 1):
        valores = [_valor_celda(ws, fila_idx, c, mapa_merges) for c in range(1, ncols + 1)]
        if _fila_vacia(valores):
            continue

        nombre = valores[campos["nombre"]]
        if not nombre or not str(nombre).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta el nombre del ejercicio"))
            continue

        grupo_muscular = valores[campos["grupo_muscular"]] if "grupo_muscular" in campos else None
        url_video = valores[campos["url_video"]] if "url_video" in campos else None

        items.append({
            "fila_excel": fila_idx,
            "nombre_original": str(nombre).strip(),
            "grupo_muscular_original": str(grupo_muscular).strip() if grupo_muscular else None,
            "url_video": str(url_video).strip() if url_video else "",
        })

    return items, filas_invalidas, advertencias


