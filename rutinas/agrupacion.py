"""Agrupa los items de UN día de una `RutinaAsignada` por grupo muscular.

Punto único de esta lógica: la usan tanto `RutinaMiDiaDetailView` (portal
del alumno, un día por vez) como `generar_pdf_rutina_asignada` (recorre
todos los días) -- así el portal y el PDF muestran exactamente el mismo
agrupamiento sin duplicar la lógica.

Django-free a propósito (no hace queries, solo itera lo que le pasan) --
se testea con instancias armadas a mano, mismo criterio que
`tenants/paisaje_matching.py`.
"""

from core.catalogos import orden_con_bucket_vacio
from ejercicios.models import Ejercicio
from rutinas.models import SEMANAS_POR_CICLO

_ORDEN_GRUPOS = orden_con_bucket_vacio(Ejercicio.GrupoMuscular.choices)
_SIN_GRUPO_DISPLAY = "Sin grupo muscular"
_DISPLAY_POR_VALOR = dict(Ejercicio.GrupoMuscular.choices)


def agrupar_items_por_grupo_muscular(items, semanas=None):
    """`items`: iterable de `RutinaAsignadaItem` de UN día (cualquier
    orden). `semanas`: lista de números de semana a incluir como columna
    en cada fila -- pasarla explícita desde el caller que también arma
    la metadata de columnas del header (p. ej. `semanas_meta` en
    `RutinaMiDiaDetailView`) evita que las dos listas puedan desalinearse
    si alguna cambia de forma independiente; por defecto es
    `1..SEMANAS_POR_CICLO` (usado por `generar_pdf_rutina_asignada`, que
    arma su propio header a partir de la misma constante).

    Devuelve una lista de grupos en el orden fijo del catálogo
    `Ejercicio.GrupoMuscular`, con un bucket final "Sin grupo muscular"
    para items snapshoteados antes de que existiera `grupo_muscular_snapshot`
    (rutinas asignadas viejas) -- nunca se descartan.

    Dentro de un grupo, "el mismo ejercicio" a través de las 4 semanas se
    identifica por `ejercicio_nombre_snapshot` (no hay FK viva al
    `Ejercicio` original) -- mismo riesgo aceptado que ya documenta
    `CLAUDE.md` para agrupar RPE por nombre. El orden entre ejercicios de
    un mismo grupo usa el `orden` de la semana más baja disponible para
    ese ejercicio (no un mínimo entre semanas, que podría no corresponder
    a ninguna fila real si el staff cargó órdenes distintos por semana).
    """
    if semanas is None:
        semanas = list(range(1, SEMANAS_POR_CICLO + 1))

    por_grupo = {}
    for item in items:
        grupo = item.grupo_muscular_snapshot
        ejercicios_del_grupo = por_grupo.setdefault(grupo, {})
        entrada = ejercicios_del_grupo.setdefault(
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

    resultado = []
    for grupo in _ORDEN_GRUPOS:
        ejercicios_del_grupo = por_grupo.get(grupo)
        if not ejercicios_del_grupo:
            continue

        ejercicios_ordenados = []
        for entrada in ejercicios_del_grupo.values():
            semana_mas_baja = min(entrada["semanas"])
            orden = entrada["semanas"][semana_mas_baja].orden
            ejercicios_ordenados.append((orden, entrada))
        ejercicios_ordenados.sort(key=lambda par: par[0])

        ejercicios = []
        for orden, entrada in ejercicios_ordenados:
            ejercicios.append(
                {
                    "nombre": entrada["nombre"],
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
                        }
                        for semana in semanas
                    ],
                }
            )

        resultado.append(
            {
                "grupo_muscular": grupo,
                "grupo_muscular_display": _DISPLAY_POR_VALOR.get(
                    grupo, _SIN_GRUPO_DISPLAY
                ),
                "ejercicios": ejercicios,
            }
        )
    return resultado
