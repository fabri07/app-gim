"""Cómo viene el alumno en una rutina asignada: adherencia (cuántas sesiones
entrenó sobre las que le tocaban) y traducción del RPE que reportó a una señal
de carga para el entrenador.

Existe porque el alumno ya venía generando feedback que el staff no veía en
ningún lado: `RutinaAsignadaDiaCompletado` no se leía en NINGUNA vista de
staff (solo en el portal del propio alumno), y el `rpe` solo aparecía como una
columna más entre ~128 filas planas, o promediado con todo el gimnasio en
`tenants/analitica.py`. Este módulo pone ese dato donde el entrenador decide.

Separado de `views.py` y de `services.py` por el mismo criterio que
`tenants/analitica.py`: es agregación de LECTURA y se testea mejor sola.
Separado de `tenants/analitica.py` porque aquello agrega a nivel gimnasio
entero y esto es por rutina de un alumno.

`senal_de_carga`, `anotar_senales`, `historial_rpe` y `sesiones_previstas_de`
son PURAS (se testean con `SimpleTestCase`, sin base). `sesiones_previstas` y
`sesiones_entrenadas` hacen UNA query cada una y nunca una por item.
"""

from dataclasses import dataclass

from rutinas.models import SEMANAS_POR_CICLO, RutinaAsignadaItem


@dataclass(frozen=True)
class SenalDeCarga:
    """Cómo leer, del lado del entrenador, lo que el alumno reportó."""

    flecha: str
    accion: str
    badge: str


# Mapeo LITERAL de los 4 valores del `TextChoices` a una señal. No hay
# promedio, ni ponderación, ni ventana temporal, ni inferencia: es el dato que
# el alumno cargó, mostrado con una flecha. El ROADMAP veta "IA de rutinas" y
# esto no lo es -- si algún día alguien quiere sugerir cargas de verdad, eso es
# otra discusión de producto, no una extensión de este dict.
#
# `AL_LIMITE` mapea a "mantener" y no a "bajar": "Estoy al límite" describe
# haber llegado al tope buscado, no haberse pasado. Lleva badge de alerta para
# que igual se distinga de "podría seguir con esta intensidad".
SENALES_POR_RPE = {
    RutinaAsignadaItem.RPE.MAS_INTENSO: SenalDeCarga(
        flecha="↑", accion="Subir la carga", badge="badge badge--ok"
    ),
    RutinaAsignadaItem.RPE.SEGUIR_INTENSIDAD: SenalDeCarga(
        flecha="=", accion="Mantener", badge="badge"
    ),
    RutinaAsignadaItem.RPE.AL_LIMITE: SenalDeCarga(
        flecha="=", accion="Mantener, está al límite", badge="badge badge--alerta"
    ),
    RutinaAsignadaItem.RPE.BAJAR_INTENSIDAD: SenalDeCarga(
        flecha="↓", accion="Bajar la carga", badge="badge badge--riesgo"
    ),
}


def senal_de_carga(rpe):
    """`None` si el alumno todavía no calificó (`rpe=""`), o si el valor no
    está en el catálogo.

    Ese segundo caso es defensivo a propósito: un valor viejo o fuera de
    catálogo no debe voltear la pantalla del staff (mismo criterio que
    `analitica.py`, que bucketea lo desconocido en vez de romper)."""
    if not rpe:
        return None
    return SENALES_POR_RPE.get(rpe)


def anotar_senales(ejercicios):
    """Agrega `senal` a cada celda de semana de la salida de
    `listar_ejercicios_del_dia`. Pura: muta y devuelve la misma lista.

    Vive acá y NO dentro de `agrupacion.py` a propósito: la señal de carga es
    una lectura de ENTRENADOR ("subile la carga"), y `agrupacion.py` también
    alimenta el portal del alumno y el PDF, donde no corresponde mostrarla --
    al alumno se le muestra la etiqueta que él mismo eligió, no una
    instrucción sobre su propio entrenamiento.
    """
    for ejercicio in ejercicios:
        for celda in ejercicio["semanas"]:
            item = celda.get("item")
            celda["senal"] = senal_de_carga(item.rpe) if item is not None else None
    return ejercicios


def historial_rpe(items):
    """`[{semana, etiqueta, senal}]` ordenado por semana, para el formulario de
    edición. Puro: recibe los hermanos ya resueltos por `services.hermanos()`.

    Es lo que convierte el form de "cargar datos a ciegas" en "ajustar con
    información": el entrenador ve, en la misma pantalla donde cambia los
    kilos, cómo sintió el alumno ese ejercicio las semanas anteriores.
    """
    return [
        {
            "semana": item.semana,
            "etiqueta": item.get_rpe_display() if item.rpe else "",
            "senal": senal_de_carga(item.rpe),
        }
        for item in sorted(items, key=lambda item: item.semana)
    ]


def sesiones_previstas_de(items):
    """Pares `(dia, semana)` que tienen al menos un item. Puro.

    Una SESIÓN es un día de una semana, no un ejercicio: un día con 8
    ejercicios cuenta como una sola sesión, porque eso es lo que el alumno
    marca como entrenado (`RutinaAsignadaDiaCompletado` es por día+semana).
    """
    return {(item.dia, item.semana) for item in items}


def sesiones_previstas(asignada):
    """Una query."""
    return set(asignada.items.values_list("dia", "semana").distinct())


def sesiones_entrenadas(asignada):
    """Una query."""
    return set(asignada.dias_completados.values_list("dia", "semana"))


@dataclass(frozen=True)
class SemanaAdherencia:
    numero: int
    previstas: int
    entrenadas: int
    es_actual: bool


@dataclass(frozen=True)
class Adherencia:
    previstas: int
    entrenadas: int
    porcentaje: int
    previstas_hasta_hoy: int
    entrenadas_hasta_hoy: int
    porcentaje_hasta_hoy: int
    semana_actual: int
    por_semana: list


def _porcentaje(entrenadas, previstas):
    """0 si no hay nada previsto -- nunca `ZeroDivisionError`. Una rutina
    recién asignada sin items es un caso real (plantilla vacía)."""
    if not previstas:
        return 0
    return round(100 * entrenadas / previstas)


def adherencia_de_rutina(asignada, *, previstas=None, entrenadas=None):
    """Cuántas sesiones entrenó el alumno sobre las que le tocaban.

    Dos queries, o CERO si el caller ya trae los dos conjuntos (lo hace
    `RutinaAsignadaDetailView`, que de todos modos materializa los items para
    armar las tablas). Nunca una query por item ni por semana.

    El número TITULAR es `porcentaje_hasta_hoy`, acotado a
    `asignada.semana_actual`: en la semana 2 de 4 la adherencia sobre el ciclo
    completo no puede superar el 50% aunque el alumno haya venido a todo, y
    leerla así sería acusarlo de algo que todavía no pasó. El porcentaje sobre
    el ciclo entero se devuelve igual, como cierre.

    Un `(dia, semana)` marcado como entrenado que ya NO tiene items se ignora
    en el numerador y en el denominador (se interseca contra `previstas`).
    Puede pasar con datos viejos, y pasa más seguido desde que el staff puede
    QUITAR el último ejercicio de un día: la fila de "entrenado" queda a
    propósito (es un hecho histórico, el alumno sí entrenó), pero no debe
    inflar la adherencia sobre una sesión que ya no existe.
    """
    if previstas is None:
        previstas = sesiones_previstas(asignada)
    if entrenadas is None:
        entrenadas = sesiones_entrenadas(asignada)

    entrenadas = entrenadas & previstas
    semana_actual = asignada.semana_actual

    previstas_hasta_hoy = {s for s in previstas if s[1] <= semana_actual}
    entrenadas_hasta_hoy = {s for s in entrenadas if s[1] <= semana_actual}

    por_semana = [
        SemanaAdherencia(
            numero=semana,
            previstas=sum(1 for s in previstas if s[1] == semana),
            entrenadas=sum(1 for s in entrenadas if s[1] == semana),
            es_actual=semana == semana_actual,
        )
        for semana in range(1, SEMANAS_POR_CICLO + 1)
    ]

    return Adherencia(
        previstas=len(previstas),
        entrenadas=len(entrenadas),
        porcentaje=_porcentaje(len(entrenadas), len(previstas)),
        previstas_hasta_hoy=len(previstas_hasta_hoy),
        entrenadas_hasta_hoy=len(entrenadas_hasta_hoy),
        porcentaje_hasta_hoy=_porcentaje(
            len(entrenadas_hasta_hoy), len(previstas_hasta_hoy)
        ),
        semana_actual=semana_actual,
        por_semana=por_semana,
    )
