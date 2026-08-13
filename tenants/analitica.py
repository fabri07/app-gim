"""
Agregaciones para el dashboard de analítica del staff (Fase 7, subproyecto 4
de la serie "impeccable en el frontend"): asistencia por día/hora, mezcla de
géneros y RPE reportado por ejercicio.

Separado de `tenants/views.py` (que solo arma contexto) porque esta lógica
consulta 3 apps distintas (turnos, alumnos, rutinas) y se testea mejor sola,
sin pasar por toda la vista.
"""

from django.db.models import Count
from django.db.models.functions import ExtractHour, ExtractWeekDay

# `ExtractWeekDay` sigue la convención de Django (domingo=1 ... sábado=7),
# no la de Python (`date.weekday()`). Se reordena acá a lunes..domingo, que
# es como un dueño de gimnasio piensa la semana.
_NOMBRES_DIA = {
    2: "Lunes",
    3: "Martes",
    4: "Miércoles",
    5: "Jueves",
    6: "Viernes",
    7: "Sábado",
    1: "Domingo",
}
_ORDEN_DIAS = [2, 3, 4, 5, 6, 7, 1]

#: Cantidad de niveles de intensidad de color para la grilla de calor (más
#: allá de "sin reservas" = nivel 0). Corresponde a 4 pasos de la rampa
#: secuencial azul documentada en la skill dataviz.
_NIVELES_CALOR = 4

#: Cantidad de ejercicios distintos que se muestran en los gráficos de
#: "ejercicios más asignados" -- sin este límite, un gimnasio con una
#: biblioteca grande satura el gráfico con decenas de barras casi ilegibles.
_TOP_N_EJERCICIOS_MAS_ASIGNADOS = 10


def asistencia_por_dia_y_hora(gimnasio):
    """Grilla lunes..domingo × hora del día con la cantidad HISTÓRICA de
    reservas en cada celda -- entendemos "hora pico" como un patrón
    recurrente, no algo que dependa de qué mes se mire (ver brainstorming:
    el dueño eligió explícitamente todo el historial en vez de una ventana
    de 30 días).

    Devuelve `{"horas": [...], "dias": [{"nombre", "celdas": [...]}], "maximo": n}`.
    Solo se listan las horas que efectivamente tienen alguna reserva alguna
    vez (evita una grilla enorme con columnas vacías si el gimnasio abre,
    p.ej., de 7 a 21 pero todas las reservas caen entre las 8 y las 20).
    Si el gimnasio no tiene ninguna reserva todavía, `horas`/`dias` vuelven
    vacíos para que el template muestre un estado vacío en vez de una
    grilla en blanco.
    """
    from turnos.models import Reserva

    filas = (
        Reserva.objects.for_gimnasio(gimnasio)
        .annotate(dia_semana=ExtractWeekDay("fecha"), hora=ExtractHour("hora_inicio"))
        .values("dia_semana", "hora")
        .annotate(total=Count("id"))
    )
    conteos = {(fila["dia_semana"], fila["hora"]): fila["total"] for fila in filas}
    if not conteos:
        return {"horas": [], "dias": [], "maximo": 0}

    horas = sorted({hora for (_dia, hora) in conteos})
    maximo = max(conteos.values())

    dias = []
    for numero in _ORDEN_DIAS:
        celdas = []
        for hora in horas:
            valor = conteos.get((numero, hora), 0)
            if valor == 0:
                nivel = 0
            else:
                nivel = max(1, min(_NIVELES_CALOR, -(-valor * _NIVELES_CALOR // maximo)))
            celdas.append({"hora": hora, "valor": valor, "nivel": nivel})
        dias.append({"nombre": _NOMBRES_DIA[numero], "celdas": celdas})

    return {"horas": horas, "dias": dias, "maximo": maximo}


def distribucion_por_genero(gimnasio):
    """Cantidad de alumnos por `sexo`, en un orden fijo (no alfabético) para
    que el gráfico no reordene las barras solas cuando cambian los conteos.
    Los que nunca cargaron el dato (`sexo=""`) se muestran como "No
    informado", distinto de "Prefiere no decir" (una respuesta explícita)."""
    from alumnos.models import Alumno

    filas = Alumno.objects.for_gimnasio(gimnasio).values("sexo").annotate(total=Count("id"))
    conteos = {fila["sexo"]: fila["total"] for fila in filas}

    etiquetas = dict(Alumno.Sexo.choices)
    etiquetas[""] = "No informado"
    orden = [
        Alumno.Sexo.MASCULINO,
        Alumno.Sexo.FEMENINO,
        Alumno.Sexo.NO_DECIR,
        "",
    ]
    return [
        {"etiqueta": etiquetas[valor], "total": conteos.get(valor, 0)} for valor in orden
    ]


def rpe_por_ejercicio(gimnasio):
    """Distribución de RPE por ejercicio, ordenada por cantidad total de
    calificaciones recibidas (de más a menos reportado).

    Agrupa por `ejercicio_nombre_snapshot` (texto), no por un id estable de
    `Ejercicio` -- `RutinaAsignadaItem` es un snapshot sin FK viva (ver
    docstring de `rutinas.models`), así que si un ejercicio se renombra en
    la biblioteca después de haber recibido calificaciones, esa historia
    vieja no se fusiona con el nombre nuevo. Riesgo aceptado, documentado en
    CLAUDE.md.
    """
    from rutinas.models import RutinaAsignadaItem

    filas = (
        RutinaAsignadaItem.objects.filter(rutina_asignada__gimnasio=gimnasio)
        .exclude(rpe="")
        .values("ejercicio_nombre_snapshot", "rpe")
        .annotate(total=Count("id"))
    )

    niveles_en_cero = {valor: 0 for valor, _etiqueta in RutinaAsignadaItem.RPE.choices}
    por_ejercicio = {}
    for fila in filas:
        nombre = fila["ejercicio_nombre_snapshot"]
        niveles = por_ejercicio.setdefault(nombre, dict(niveles_en_cero))
        niveles[fila["rpe"]] = fila["total"]

    resultado = [
        {"ejercicio": nombre, "niveles": niveles, "total": sum(niveles.values())}
        for nombre, niveles in por_ejercicio.items()
    ]
    resultado.sort(key=lambda fila: fila["total"], reverse=True)
    return resultado


def ejercicios_mas_asignados(gimnasio, limite=_TOP_N_EJERCICIOS_MAS_ASIGNADOS):
    """Ranking de ejercicios por cantidad de veces que aparecen en un
    `RutinaAsignadaItem`, sobre TODO el histórico (no solo rutinas activas --
    mismo criterio que `asistencia_por_dia_y_hora`).

    A diferencia de `rpe_por_ejercicio`, acá NO se excluye `rpe=""`: se
    cuenta "cuántas veces se asignó este ejercicio", no "cuántas veces se
    calificó" -- un ejercicio sin calificación todavía cuenta como asignado.

    Agrupa por `ejercicio_nombre_snapshot` (texto), mismo riesgo aceptado
    que `rpe_por_ejercicio` (ver su docstring y CLAUDE.md): un ejercicio
    renombrado en la biblioteca no fusiona su historial viejo con el nombre
    nuevo.

    Devuelve como mucho `limite` filas, de más a menos asignado (empate
    alfabético para que el orden no varíe entre corridas). Lista vacía si
    el gimnasio no tiene ningún item de rutina asignada todavía.
    """
    from rutinas.models import RutinaAsignadaItem

    filas = (
        RutinaAsignadaItem.objects.filter(rutina_asignada__gimnasio=gimnasio)
        .values("ejercicio_nombre_snapshot")
        .annotate(total=Count("id"))
        .order_by("-total", "ejercicio_nombre_snapshot")[:limite]
    )
    return [
        {"ejercicio": fila["ejercicio_nombre_snapshot"], "total": fila["total"]}
        for fila in filas
    ]


def ejercicios_mas_asignados_por_genero(gimnasio, limite=_TOP_N_EJERCICIOS_MAS_ASIGNADOS):
    """Mismo ranking que `ejercicios_mas_asignados` (mismo top-`limite`,
    mismo orden), desglosado por `sexo` del alumno dueño de la rutina --
    para poder leer los dos gráficos lado a lado sin que el orden de las
    barras cambie entre uno y otro.

    Mismo tratamiento de `sexo=""` -> "no informado" que
    `distribucion_por_genero` (distinto de `Alumno.Sexo.NO_DECIR` =
    "Prefiere no decir", una respuesta explícita). Las claves del dict
    `generos` son slugs seguros para lookup de template (masculino/femenino/
    no_decir/no_informado), no las etiquetas legibles -- igual que
    `niveles` en `rpe_por_ejercicio` usa los `value` de
    `RutinaAsignadaItem.RPE`, no sus labels (el punto-lookup de un template
    no soporta claves con espacios).

    Lista vacía si `ejercicios_mas_asignados` ya es vacía.
    """
    from alumnos.models import Alumno
    from rutinas.models import RutinaAsignadaItem

    ranking = ejercicios_mas_asignados(gimnasio, limite=limite)
    if not ranking:
        return []
    nombres_en_orden = [fila["ejercicio"] for fila in ranking]

    filas = (
        RutinaAsignadaItem.objects.filter(
            rutina_asignada__gimnasio=gimnasio,
            ejercicio_nombre_snapshot__in=nombres_en_orden,
        )
        .values("ejercicio_nombre_snapshot", "rutina_asignada__alumno__sexo")
        .annotate(total=Count("id"))
    )

    claves_sexo = {
        Alumno.Sexo.MASCULINO: "masculino",
        Alumno.Sexo.FEMENINO: "femenino",
        Alumno.Sexo.NO_DECIR: "no_decir",
        "": "no_informado",
    }
    en_cero = {clave: 0 for clave in claves_sexo.values()}

    por_ejercicio = {nombre: dict(en_cero) for nombre in nombres_en_orden}
    for fila in filas:
        nombre = fila["ejercicio_nombre_snapshot"]
        # .get() con fallback, no indexado directo: `sexo` es un CharField
        # con choices, no un enum forzado por la DB -- un valor fuera de
        # catálogo (dato viejo, choice eliminada) no debe tirar abajo todo
        # el dashboard de staff, mismo criterio que distribucion_por_genero.
        clave = claves_sexo.get(fila["rutina_asignada__alumno__sexo"], "no_informado")
        por_ejercicio[nombre][clave] = fila["total"]

    return [
        {
            "ejercicio": nombre,
            "generos": por_ejercicio[nombre],
            "total": sum(por_ejercicio[nombre].values()),
        }
        for nombre in nombres_en_orden
    ]
