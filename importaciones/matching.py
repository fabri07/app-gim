"""Matching difuso de nombres de ejercicio y de grupo muscular
(Proyecto 2). Ver spec §4-5 para el pipeline completo.

`resolver_nombre` y `resolver_grupo_muscular` son puras: no tocan la base.
Solo `construir_indice_ejercicios` toca DB (una única consulta, scopeada
por tenant)."""

from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from ejercicios.models import Ejercicio
from importaciones.parsing import normalizar_texto

UMBRAL_AMBIGUO = 87
PISO_SCORE = 60


@dataclass(frozen=True)
class MatchResultado:
    tipo: Literal["exacto", "ambiguo", "nuevo"]
    # Tipados como `Ejercicio | None` porque en producción SIEMPRE son
    # instancias reales (vienen del índice armado por
    # construir_indice_ejercicios). Los tests puros de más abajo pasan un
    # `indice` armado a mano con strings en vez de `Ejercicio` -- Python no
    # valida tipos de dataclass en runtime, así que eso no rompe nada, solo
    # hace que el type hint documente el contrato de producción, no el de
    # los tests unitarios de esta función.
    ejercicio: Ejercicio | None = None
    candidato: Ejercicio | None = None
    score: int | None = None


def resolver_nombre(nombre_normalizado, indice):
    """`indice` es un dict `{nombre_normalizado: Ejercicio}` ya armado
    (ver `construir_indice_ejercicios`) -- recibirlo como parámetro, en vez
    de tomar `gimnasio`, es lo que hace esta función pura y testeable con
    un dict a mano."""
    if nombre_normalizado in indice:
        return MatchResultado(tipo="exacto", ejercicio=indice[nombre_normalizado])

    if not indice:
        return MatchResultado(tipo="nuevo")

    candidatos = list(indice.keys())
    mejor = process.extractOne(nombre_normalizado, candidatos, scorer=fuzz.WRatio)
    if mejor is None:
        return MatchResultado(tipo="nuevo")

    nombre_candidato, score, _ = mejor
    score = int(score)
    if score < PISO_SCORE:
        return MatchResultado(tipo="nuevo")
    return MatchResultado(tipo="ambiguo", candidato=indice[nombre_candidato], score=score)


def construir_indice_ejercicios(gimnasio):
    """Única función de este módulo que toca DB."""
    ejercicios = Ejercicio.objects.for_gimnasio(gimnasio)
    return {normalizar_texto(e.nombre): e for e in ejercicios}


ALIAS_GRUPO_MUSCULAR = {
    "abdomen": Ejercicio.GrupoMuscular.CORE,
    "abs": Ejercicio.GrupoMuscular.CORE,
    "gluteos": Ejercicio.GrupoMuscular.PIERNAS,
    "glúteos": Ejercicio.GrupoMuscular.PIERNAS,
    "pierna": Ejercicio.GrupoMuscular.PIERNAS,
    "espalda alta": Ejercicio.GrupoMuscular.ESPALDA,
    "espalda baja": Ejercicio.GrupoMuscular.ESPALDA,
    "hombro": Ejercicio.GrupoMuscular.HOMBROS,
    "brazo": Ejercicio.GrupoMuscular.BRAZOS,
    "biceps": Ejercicio.GrupoMuscular.BRAZOS,
    "bíceps": Ejercicio.GrupoMuscular.BRAZOS,
    "triceps": Ejercicio.GrupoMuscular.BRAZOS,
    "tríceps": Ejercicio.GrupoMuscular.BRAZOS,
    "full body": Ejercicio.GrupoMuscular.CUERPO_COMPLETO,
    "cuerpo completo": Ejercicio.GrupoMuscular.CUERPO_COMPLETO,
}


def resolver_grupo_muscular(texto):
    """Normaliza y matchea contra las choices de `Ejercicio.GrupoMuscular`
    + el diccionario de alias de arriba. `None` si no hay match confiable
    -- nunca un default silencioso (decisión 10 del spec)."""
    normalizado = normalizar_texto(texto)
    for valor, _ in Ejercicio.GrupoMuscular.choices:
        if normalizado == valor or normalizado == normalizar_texto(
            dict(Ejercicio.GrupoMuscular.choices)[valor]
        ):
            return valor
    return ALIAS_GRUPO_MUSCULAR.get(normalizado)
