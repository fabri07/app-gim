"""Lector de planillas en MATRIZ ANCHA: una fila por ejercicio, las semanas a
lo ancho.

Es el layout de las planillas comerciales (el archivo del primer cliente pago
dice "Powered by Simplify Trainers") y también el que sale naturalmente de
hacer un plan a mano en Excel: el entrenador quiere ver las cuatro semanas del
ejercicio de un vistazo, no repetir la fila cuatro veces.

    ┌──────────┬─────┬──────────────┬────── SEMANA 1 ──────┬─── SEMANA 2 ───┐
    │          │     │              │Series│Reps│Carga│RPE │Series│Reps│... │
    │ DÍA 1    │ A1. │ Plancha      │  4   │ 20 │     │ 🟡 │  4   │ 25 │    │
    │ • CORE   │ A2. │ Press Pallof │  3   │ 12 │10KG │ 🟡 │  3   │ 15 │    │
    └──────────┴─────┴──────────────┴──────┴────┴─────┴────┴──────┴────┴────┘

Tres cosas lo diferencian del lector tabular (`tabular.py`) y explican por qué
vive aparte:

1. El encabezado ocupa DOS filas (grupos combinados arriba, subcampos abajo).
2. Las columnas de la izquierda no se identifican por su título -- suele estar
   combinado, o directamente vacío -- sino PERFILANDO SU CONTENIDO.
3. Una fila de Excel produce hasta un item por semana, así que un dato malo en
   una semana no puede invalidar la fila entera.

Django-free, igual que el resto del paquete.
"""

import re
from dataclasses import dataclass

from importaciones.parsing.comun import (
    ALIAS_PLANTILLA,
    FilaInvalida,
    HojaParseada,
    ItemParseado,
    _mapa_merges,
    _valor_celda,
    normalizar_texto,
)

# Cuántas filas se miran buscando el encabezado de dos filas. Tiene que cubrir
# el caso real (fila 12) con margen, y ser chico para que una hoja auxiliar de
# 3206 filas se descarte habiendo leído casi nada.
FILAS_ESCANEO_ANCHA = 40

# Cuántas filas de datos se muestrean para decidir qué columna es el nombre y
# cuál el código de bloque. No hace falta la hoja entera.
FILAS_MUESTRA_PERFIL = 40

# Mínimo de filas con un nombre de ejercicio de verdad para aceptar el layout.
# Sin esto, una hoja auxiliar con una tabla de progreso "SEMANA 1 / SEMANA 2"
# pasa los pasos anteriores y genera ejercicios fantasma.
MIN_FILAS_CON_NOMBRE = 3

# El dígito NO es opcional, y es lo que impide que el layout largo -- cuyo
# encabezado dice "Semana" a secas -- se lea como una matriz.
RE_SEMANA = re.compile(r"^(semana|sem|week|wk|microciclo|micro)\s*(\d+)$")

# Sin `$`: el marcador real trae la descripción pegada en la misma celda
# ("DÍA 2\n• TREN SUPERIOR\n• CORE"), y eso es justamente lo que queremos.
RE_DIA = re.compile(r"^(dia|day|sesion|session|jornada)\s*(\d+)\b")

# "A1.", "B2", "C" -- el código con el que se agrupan las superseries.
RE_BLOQUE = re.compile(r"^[a-z]\s*\d{0,2}\.?$")

# El RPE aparece en la fila de subcampos y hay que reconocerlo PARA SALTEARLO:
# `RutinaPlantillaItem` no lo tiene, y el RPE de la app lo carga el alumno
# sobre su propia rutina asignada. Sin listarlo acá, la columna se colaría en
# el corte de bloques.
SUBCAMPOS = {
    "series": ALIAS_PLANTILLA["series"],
    "repeticiones": ALIAS_PLANTILLA["repeticiones"],
    "kilos": ALIAS_PLANTILLA["kilos"],
    "descanso": ALIAS_PLANTILLA["descanso"],
    "notas": ALIAS_PLANTILLA["notas"],
}
SUBCAMPOS_IGNORADOS = {"rpe", "rir", "esfuerzo", "calificacion"}


@dataclass(frozen=True)
class BloqueSemana:
    numero: int
    subcampos: dict  # columna absoluta (1-indexed) -> campo canónico


@dataclass(frozen=True)
class EncabezadoAncho:
    fila_grupos: int
    fila_subcampos: int
    bloques: list
    col_nombre: int
    col_bloque: int | None
    cols_dia: list


def _campo_de_subcampo(valor):
    normalizado = normalizar_texto(valor)
    if not normalizado:
        return None
    for campo, alias in SUBCAMPOS.items():
        if normalizado in alias:
            return campo
    return None


def _es_subcampo_conocido(valor):
    normalizado = normalizar_texto(valor)
    return bool(normalizado) and (
        _campo_de_subcampo(valor) is not None or normalizado in SUBCAMPOS_IGNORADOS
    )


def _labels_de_semana(ws, fila, ncols):
    """`[(columna, numero)]` de las celdas de `fila` que dicen "SEMANA n".

    Se leen los valores CRUDOS, sin resolver merges: openpyxl devuelve el
    valor solo en la esquina del rango combinado, que es exactamente donde
    empieza el bloque de esa semana.
    """
    encontrados = []
    for col in range(1, ncols + 1):
        match = RE_SEMANA.match(normalizar_texto(ws.cell(row=fila, column=col).value))
        if match:
            encontrados.append((col, int(match.group(2))))
    return encontrados


def _cortar_bloques(labels, fila_subcampos, ws, ncols):
    """Cada semana ocupa desde su label hasta la columna anterior al siguiente.

    Devuelve `None` si algún bloque no tiene ni series ni repeticiones: sin uno
    de los dos no hay nada que programar y lo más probable es que no sea una
    matriz de entrenamiento.
    """
    bloques = []
    for i, (col_inicio, numero) in enumerate(labels):
        col_fin = labels[i + 1][0] - 1 if i + 1 < len(labels) else ncols
        subcampos = {}
        for col in range(col_inicio, col_fin + 1):
            campo = _campo_de_subcampo(ws.cell(row=fila_subcampos, column=col).value)
            if campo and campo not in subcampos.values():
                subcampos[col] = campo
        if "series" not in subcampos.values() and "repeticiones" not in subcampos.values():
            return None
        bloques.append(BloqueSemana(numero=numero, subcampos=subcampos))
    return bloques


def _perfilar_columnas_izquierda(ws, merges, fila_subcampos, col_limite, ncols):
    """Decide, MIRANDO EL CONTENIDO, cuál columna es el nombre, cuál el código
    de bloque y cuáles llevan el marcador de día.

    No se puede hacer por encabezado: en la planilla real la celda de arriba de
    la columna de nombres es un merge que dice "EJERCICIOS" y abarca también la
    del código, y la del día está directamente vacía.
    """
    fila_desde = fila_subcampos + 1
    fila_hasta = min(ws.max_row or 0, fila_desde + FILAS_MUESTRA_PERFIL - 1)
    perfil = {}
    for col in range(1, col_limite):
        largos = codigos = dias = 0
        for fila in range(fila_desde, fila_hasta + 1):
            valor = _valor_celda(ws, fila, col, merges)
            if valor is None or not str(valor).strip():
                continue
            texto = str(valor).strip()
            normalizado = normalizar_texto(texto)
            if RE_DIA.match(normalizado):
                dias += 1
            elif RE_BLOQUE.match(normalizado):
                codigos += 1
            elif len(texto) > 4:
                largos += 1
        perfil[col] = (largos, codigos, dias)

    if not perfil:
        return None, None, []
    col_nombre = max(perfil, key=lambda c: perfil[c][0])
    if perfil[col_nombre][0] < MIN_FILAS_CON_NOMBRE:
        return None, None, []
    col_bloque = max(perfil, key=lambda c: perfil[c][1])
    if perfil[col_bloque][1] == 0:
        col_bloque = None
    cols_dia = [c for c in perfil if perfil[c][2]]
    return col_nombre, col_bloque, cols_dia


def detectar_matriz_ancha(ws, *, max_filas=FILAS_ESCANEO_ANCHA):
    """`EncabezadoAncho` si la hoja es una matriz por semanas, `None` si no.

    Corta en el primer paso que falla, sin mirar nunca `ws.max_row` completo:
    una hoja auxiliar de miles de filas tiene que descartarse barata.

    **Esta detección se prueba ANTES que el lector largo, siempre.** Si se
    probara al revés, esta misma hoja matchearía el layout largo (la fila de
    grupos tiene "EJERCICIOS", la de subcampos tiene "Series"/"Reps"/"Carga") y
    produciría filas plausibles con las columnas corridas -- basura silenciosa,
    mucho peor que no leer nada.
    """
    ncols = min(ws.max_column or 0, 80)
    tope = min(ws.max_row or 0, max_filas)

    for fila in range(1, tope + 1):
        labels = _labels_de_semana(ws, fila, ncols)
        if len(labels) < 2:
            continue

        for fila_subcampos in (fila, fila + 1):
            if fila_subcampos > (ws.max_row or 0):
                continue
            conocidos = sum(
                1
                for col in range(1, ncols + 1)
                if _es_subcampo_conocido(ws.cell(row=fila_subcampos, column=col).value)
            )
            if conocidos < 2:
                continue

            bloques = _cortar_bloques(labels, fila_subcampos, ws, ncols)
            if bloques is None:
                continue

            merges = _mapa_merges(ws)
            col_nombre, col_bloque, cols_dia = _perfilar_columnas_izquierda(
                ws, merges, fila_subcampos, labels[0][0], ncols
            )
            if col_nombre is None:
                continue

            return EncabezadoAncho(
                fila_grupos=fila,
                fila_subcampos=fila_subcampos,
                bloques=bloques,
                col_nombre=col_nombre,
                col_bloque=col_bloque,
                cols_dia=cols_dia,
            )
    return None


def _marcador_de_dia(ws, fila, merges, cols_dia):
    """`(numero, nombre)` del día que empieza en `fila`, o `(None, None)`.

    Entre varias columnas gana la celda con MÁS texto. En la planilla real el
    marcador aparece repetido por los merges: `DÍA 2` pelado en una columna y
    `DÍA 2 + • TREN SUPERIOR + • CORE` en otra. Quedarse con la primera que
    aparece perdería el nombre del día.
    """
    mejor = ""
    for col in cols_dia:
        valor = _valor_celda(ws, fila, col, merges)
        if valor is None:
            continue
        texto = str(valor).strip()
        if RE_DIA.match(normalizar_texto(texto)) and len(texto) > len(mejor):
            mejor = texto
    if not mejor:
        return None, None

    numero = int(RE_DIA.match(normalizar_texto(mejor)).group(2))
    # Lo que sobra después de "DÍA n" es la descripción, casi siempre en
    # líneas con viñeta dentro de la misma celda.
    resto = re.sub(
        r"^\s*(d[ií]a|day|sesi[oó]n|session|jornada)\s*\d+\s*",
        "",
        mejor,
        flags=re.IGNORECASE,
    )
    partes = [p.strip(" •\t-·") for p in resto.splitlines()]
    return numero, " · ".join(p for p in partes if p)


def leer_hoja_ancha(ws, encabezado):
    """Parsea una hoja ya reconocida como matriz ancha.

    Emite un `ItemParseado` por cada (fila de ejercicio × semana con datos).
    """
    merges = _mapa_merges(ws)
    items = []
    filas_invalidas = []
    contador_orden = {}
    dia_actual, nombre_dia_actual = 1, ""

    for fila in range(encabezado.fila_subcampos + 1, (ws.max_row or 0) + 1):
        numero, nombre = _marcador_de_dia(ws, fila, merges, encabezado.cols_dia)
        if numero is not None:
            dia_actual, nombre_dia_actual = numero, nombre

        nombre_crudo = _valor_celda(ws, fila, encabezado.col_nombre, merges)
        nombre_ejercicio = str(nombre_crudo).strip() if nombre_crudo is not None else ""

        crudos_por_semana = {}
        for bloque in encabezado.bloques:
            crudos_por_semana[bloque.numero] = {
                campo: _valor_celda(ws, fila, col, merges)
                for col, campo in bloque.subcampos.items()
            }
        hay_datos = any(
            v is not None and str(v).strip()
            for crudos in crudos_por_semana.values()
            for v in crudos.values()
        )

        if not nombre_ejercicio or RE_DIA.match(normalizar_texto(nombre_ejercicio)):
            # Un slot vacío con solo el código de bloque cargado ("D3.") es
            # parte normal de la planilla, no un error que valga reportar.
            # Con datos cargados y sin nombre, en cambio, se pierde algo.
            if hay_datos:
                filas_invalidas.append(
                    FilaInvalida(fila, "Falta el nombre del ejercicio")
                )
            continue

        codigo_bloque = ""
        if encabezado.col_bloque is not None:
            crudo = _valor_celda(ws, fila, encabezado.col_bloque, merges)
            if crudo is not None:
                codigo_bloque = str(crudo).strip().rstrip(".")

        for bloque in encabezado.bloques:
            crudos = crudos_por_semana[bloque.numero]
            if not any(v is not None and str(v).strip() for v in crudos.values()):
                continue  # semana no programada para este ejercicio

            try:
                series = int(crudos.get("series"))
            except (TypeError, ValueError):
                filas_invalidas.append(FilaInvalida(
                    fila, f"Semana {bloque.numero}: 'series' no es un número"
                ))
                continue

            repeticiones = crudos.get("repeticiones")
            if repeticiones is None or not str(repeticiones).strip():
                filas_invalidas.append(FilaInvalida(
                    fila, f"Semana {bloque.numero}: falta 'repeticiones'"
                ))
                continue

            clave = (bloque.numero, dia_actual)
            contador_orden[clave] = contador_orden.get(clave, 0) + 1

            def _texto(campo):
                valor = crudos.get(campo)
                return str(valor).strip() if valor is not None else ""

            items.append(ItemParseado(
                semana=bloque.numero,
                dia=dia_actual,
                orden=contador_orden[clave],
                ejercicio_original=nombre_ejercicio,
                series=series,
                repeticiones=str(repeticiones).strip(),
                kilos=_texto("kilos"),
                descanso=_texto("descanso"),
                notas=_texto("notas"),
                bloque=codigo_bloque,
                dia_nombre=nombre_dia_actual,
                fila_excel=fila,
            ))

    if not items:
        # Nunca una hoja vacía y muda: si se reconoció la matriz pero no salió
        # nada, hay que decir por qué (constraint del review original).
        return HojaParseada(
            nombre_hoja=ws.title,
            dias_por_semana=0,
            filas_invalidas=filas_invalidas,
            motivo_exclusion=(
                "Reconocí una tabla con las semanas a lo ancho, pero no "
                "encontré ningún ejercicio con series y repeticiones cargadas."
            ),
        )

    return HojaParseada(
        nombre_hoja=ws.title,
        # `max`, no `len(set(...))`: mismo criterio que el lector largo.
        # `dia` es "día N de la rutina (1..dias_por_semana)", así que con
        # días 1, 2 y 4 el plan tiene 4, no 3.
        dias_por_semana=max(i.dia for i in items),
        items=items,
        filas_invalidas=filas_invalidas,
    )
