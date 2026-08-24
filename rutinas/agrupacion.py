"""Arma la lista de ejercicios de UN día de una `RutinaAsignada`.

Punto único de esta lógica: la usan tanto `RutinaMiDiaDetailView` (portal
del alumno, un día por vez) como `generar_pdf_rutina_asignada` (recorre
todos los días) -- así el portal y el PDF muestran exactamente la misma
lista sin duplicar la lógica.

Hasta 2026-08-24 esta función subdividía el resultado en secciones por
grupo muscular; se sacó esa subdivisión a pedido de un cliente real (la
encontró confusa) y ahora devuelve una lista plana -- el grupo muscular de
cada ejercicio se sigue calculando y devolviendo (`grupo_muscular_display`),
solo que ya no se usa para dividir en secciones.

Django-free a propósito (no hace queries, solo itera lo que le pasan) --
se testea con instancias armadas a mano, mismo criterio que
`tenants/paisaje_matching.py`.
"""

from ejercicios.models import Ejercicio
from rutinas.models import SEMANAS_POR_CICLO

_SIN_GRUPO_DISPLAY = "Sin grupo muscular"
_DISPLAY_POR_VALOR = dict(Ejercicio.GrupoMuscular.choices)


def listar_ejercicios_del_dia(items, semanas=None, semana_actual=None):
    """`items`: iterable de `RutinaAsignadaItem` de UN día (cualquier
    orden). `semanas`: lista de números de semana a incluir como columna
    en cada fila -- pasarla explícita desde el caller que también arma
    la metadata de columnas del header (p. ej. `semanas_meta` en
    `RutinaMiDiaDetailView`) evita que las dos listas puedan desalinearse
    si alguna cambia de forma independiente; por defecto es
    `1..SEMANAS_POR_CICLO` (usado por `generar_pdf_rutina_asignada`, que
    arma su propio header a partir de la misma constante).

    `semana_actual`: si se pasa, cada celda de semana trae su propio
    `es_actual` ya calculado (`semana == semana_actual`) -- así el
    template resalta la columna actual sin repetir la comparación a
    mano en cada `<td>`. El PDF no la necesita (no hay nada que resaltar
    en papel) y la deja en su default `None`, que da `es_actual=False`
    en todas las celdas.

    "El mismo ejercicio" a través de las 4 semanas se identifica por
    `ejercicio_nombre_snapshot` (no hay FK viva al `Ejercicio` original)
    -- mismo riesgo aceptado que ya documenta `CLAUDE.md` para agrupar RPE
    por nombre. El orden de la lista devuelta usa el `orden` de la semana
    más baja disponible para cada ejercicio (no un mínimo entre semanas,
    que podría no corresponder a ninguna fila real si el staff cargó
    órdenes distintos por semana) -- es el mismo campo que ya describe el
    orden real dentro del día (`RutinaAsignadaItem.orden`, "Orden dentro
    del día"), así que una lista plana ordenada por él respeta el orden
    que el staff cargó.

    Cada ejercicio del resultado trae su propio `grupo_muscular_display`,
    tomado del mismo item de la semana más baja disponible que ya define
    `orden` -- no del primer item iterado. Un ejercicio reasignado a otro
    grupo muscular en la biblioteca entre semanas de una misma rutina
    (caso raro) queda así determinado por la semana más baja, igual que
    `orden`, y no por el orden en que el caller haya iterado `items` (que
    esta función, según el docstring de arriba, no puede asumir).
    """
    if semanas is None:
        semanas = list(range(1, SEMANAS_POR_CICLO + 1))

    por_nombre = {}
    for item in items:
        entrada = por_nombre.setdefault(
            item.ejercicio_nombre_snapshot,
            {
                "nombre": item.ejercicio_nombre_snapshot,
                "video": "",
                "semanas": {},
            },
        )
        entrada["semanas"][item.semana] = item
        if not entrada["video"] and item.ejercicio_video_snapshot:
            entrada["video"] = item.ejercicio_video_snapshot

    ejercicios_ordenados = []
    for entrada in por_nombre.values():
        semana_mas_baja = min(entrada["semanas"])
        item_semana_mas_baja = entrada["semanas"][semana_mas_baja]
        orden = item_semana_mas_baja.orden
        grupo_muscular_display = _DISPLAY_POR_VALOR.get(
            item_semana_mas_baja.grupo_muscular_snapshot, _SIN_GRUPO_DISPLAY
        )
        ejercicios_ordenados.append((orden, entrada, grupo_muscular_display))
    ejercicios_ordenados.sort(key=lambda par: par[0])

    resultado = []
    for orden, entrada, grupo_muscular_display in ejercicios_ordenados:
        resultado.append(
            {
                "nombre": entrada["nombre"],
                "grupo_muscular_display": grupo_muscular_display,
                "video": entrada["video"],
                "orden": orden,
                # Lista (no dict): los templates de Django no pueden
                # indexar un dict con una clave dinámica sin un filtro
                # custom, así que cada celda ya trae su propio número
                # de semana -- se itera en paralelo con la metadata de
                # columnas del caller (mismo `semanas`, ver arriba).
                "semanas": [
                    {
                        "numero": semana,
                        "item": entrada["semanas"].get(semana),
                        "es_actual": semana == semana_actual,
                    }
                    for semana in semanas
                ],
            }
        )
    return resultado
