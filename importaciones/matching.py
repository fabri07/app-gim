"""Matching difuso de nombres de ejercicio y de categorías (Proyecto 2).
Ver spec §4-5 para el pipeline completo.

`resolver_nombre` y `resolver_categorias` son puras: reciben su índice ya
armado y no tocan la base. Solo `construir_indice_ejercicios` y
`construir_indice_categorias` consultan (una query cada una, scopeada por
tenant).

Hasta 2026-08-26 acá vivía `resolver_grupo_muscular`, que matcheaba contra un
`TextChoices` global de 8 valores más un diccionario de alias fijo. Con el
catálogo de categorías por gimnasio eso dejó de tener sentido: no hay lista
global contra la cual matchear, y los alias no podían anticipar cómo
clasifica cada gimnasio."""

from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from ejercicios.models import CategoriaEjercicio, Ejercicio
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


UMBRAL_CATEGORIA = 85


@dataclass(frozen=True)
class CategoriaResuelta:
    """Qué hacer con el texto de categoría de una fila del Excel.

    `existente` trae el id de una `CategoriaEjercicio` que ya está en el
    catálogo del gimnasio. `nueva` trae el nombre canónico elegido para
    crearla -- todavía NO existe: el preview no escribe en la base, la crea
    `confirmar_importacion_biblioteca`.

    `nombre` se llena en LOS DOS casos, no solo en `nueva`: es lo que deja
    auditar el dedupe difuso en el preview. Si el archivo dice "MOBILIDAD" y
    eso se fusionó con la "Movilidad" que ya existía (ratio 88.9), el staff
    tiene que poder VER que quedó en "Movilidad" -- si la pantalla muestra el
    texto crudo del Excel, una fusión se lee igual que un match exacto y no
    hay forma de detectar una fusión indebida antes de confirmar.
    """

    tipo: Literal["existente", "nueva"]
    categoria_id: int | None = None
    nombre: str = ""


def construir_indice_categorias(gimnasio):
    """`{nombre_normalizado: (id, nombre)}` del catálogo del gimnasio. Única
    función de categorías que toca DB, para que `resolver_categorias` quede
    pura.

    Sin filtrar por `activo`: una categoría desactivada sigue ocupando su
    `nombre_normalizado` por la UniqueConstraint, así que ignorarla haría que
    el importador intentara crear una duplicada y terminara reusando la misma
    fila igual, pero anunciándola como "nueva" en el preview. Reusarla y
    decirlo es más honesto. Ver ISSUES.md [2026-08-26].
    """
    return {
        c.nombre_normalizado: (c.pk, c.nombre)
        for c in CategoriaEjercicio.objects.for_gimnasio(gimnasio)
    }


def resolver_categorias(textos, indice):
    """`{texto original: CategoriaResuelta}` para cada texto no vacío.

    Pura: `indice` es `{nombre_normalizado: id}` (ver
    `construir_indice_categorias`), igual que `resolver_nombre` recibe su
    índice armado.

    Tres intentos por texto, en orden: (1) match exacto normalizado contra el
    catálogo; (2) similitud >= UMBRAL_CATEGORIA contra el catálogo;
    (3) similitud contra las que ya se encolaron para crear en ESTA misma
    importación. Recién si ninguno da, encola una nueva.

    El paso (3) es lo que evita terminar con "TRACCIÓN" y "TRACION" como dos
    categorías distintas cuando las dos vienen en el mismo archivo. Gana la
    PRIMERA forma vista como nombre canónico: sin un criterio de autoridad
    mejor, el orden del archivo es al menos estable y predecible.

    Se usa `fuzz.ratio` y no `WRatio` (el de `resolver_nombre`): los nombres
    de categoría son palabras sueltas y cortas, donde las heurísticas de
    WRatio sobre subcadenas y orden de tokens inflan el puntaje y fusionarían
    categorías distintas.
    """
    resultado = {}
    nuevas = {}  # nombre_normalizado -> nombre canónico

    for texto in textos:
        normalizado = normalizar_texto(texto)
        if not normalizado:
            continue

        if normalizado in indice:
            pk, nombre = indice[normalizado]
            resultado[texto] = CategoriaResuelta(
                tipo="existente", categoria_id=pk, nombre=nombre
            )
            continue

        parecida = _mas_parecida(normalizado, indice)
        if parecida is not None:
            pk, nombre = indice[parecida]
            resultado[texto] = CategoriaResuelta(
                tipo="existente", categoria_id=pk, nombre=nombre
            )
            continue

        ya_encolada = _mas_parecida(normalizado, nuevas)
        if ya_encolada is not None:
            resultado[texto] = CategoriaResuelta(
                tipo="nueva", nombre=nuevas[ya_encolada]
            )
            continue

        canonico = str(texto).strip()
        nuevas[normalizado] = canonico
        resultado[texto] = CategoriaResuelta(tipo="nueva", nombre=canonico)

    return resultado


def _mas_parecida(normalizado, candidatas):
    """Clave de `candidatas` con similitud >= UMBRAL_CATEGORIA, o `None`."""
    if not candidatas:
        return None
    mejor = process.extractOne(
        normalizado,
        list(candidatas),
        scorer=fuzz.ratio,
        score_cutoff=UMBRAL_CATEGORIA,
    )
    return mejor[0] if mejor else None
