"""Cálculo del ranking de kilos por ejercicio.

Se calcula en cada pedido, sin cache (ruling 1 del plan): son DOS queries
que no dependen de la cantidad de alumnos ni de ejercicios, y un cache sería
un dato más que invalidar cada vez que un alumno se da de baja del ranking,
acepta los términos o cambia de plan.

La regla de «cuál es el plan vigente de un alumno» está escrita acá en SQL
(`participantes`) y en Python en `RutinaAsignada.vigente_de`: es la misma
regla dos veces. Si se toca una, hay que tocar la otra (`por_vencer_de` ya
hace lo mismo).

Quién cuenta: alumno ACTIVO, MAYOR DE 18 años (sin fecha de nacimiento no
cuenta: no se puede verificar), que no se dio de baja del ranking, que aceptó
la versión vigente de los términos y cuyo plan vigente no está abandonado
(arrancó hace a lo sumo 4 semanas + `DIAS_GRACIA_RANKING`). De ese plan
cuenta el mayor peso de cada ejercicio entre sus cuatro semanas.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from alumnos.edad import fecha_corte_mayoria
from alumnos.models import Alumno
from importaciones.parsing.comun import normalizar_texto
from rutinas.kilos import parsear_kilos
from rutinas.models import SEMANAS_POR_CICLO, RutinaAsignada, RutinaAsignadaItem
from tenants.terminos import VERSION_TERMINOS

TOP_EJERCICIOS = 10
TOP_POR_EJERCICIO = 10
MINIMO_PARTICIPANTES = 3
#: Días de gracia después de terminado el ciclo de 28 días: un plan que
#: arrancó hace más de 28 + 14 = 42 días se considera abandonado.
DIAS_GRACIA_RANKING = 14


@dataclass(frozen=True)
class FilaRanking:
    puesto: int
    nombre: str
    kilos: Decimal
    es_propia: bool


@dataclass(frozen=True)
class BloqueEjercicio:
    ejercicio: str
    participantes: int
    filas: list
    tu_fila: "FilaRanking | None"


def condiciones_de_persona(hoy, prefijo=""):
    """Q de «quién es una persona visible para sus compañeros»: ACTIVO, no se
    dio de baja del ranking, mayor de 18 y con los términos vigentes aceptados.

    Única definición: la usan `participantes` (con `prefijo="alumno__"`) y
    `honor.alumnos_visibles` (sin prefijo). Todo en SQL."""
    return Q(**{
        f"{prefijo}estado": Alumno.Estado.ACTIVO,
        f"{prefijo}aparece_en_ranking": True,
        f"{prefijo}fecha_nacimiento__lte": fecha_corte_mayoria(hoy),
        f"{prefijo}perfil__terminos_version__gte": VERSION_TERMINOS,
    })


def participantes(gimnasio):
    """Rutinas vigentes de los alumnos que cuentan para el ranking.

    Única definición de «quién participa»: la reusan las piezas siguientes.
    Los menores de 18 (y los alumnos sin fecha de nacimiento) no participan
    en ningún ranking.
    Todo en SQL (nunca `fecha + columna`, que en Postgres falla).
    """
    hoy = timezone.localdate()
    piso = hoy - timedelta(days=SEMANAS_POR_CICLO * 7 + DIAS_GRACIA_RANKING)
    # «Es el plan vigente» <=> no existe OTRO plan activo del mismo alumno,
    # ya empezado, que gane el orden de `vigente_de` (-fecha_inicio, -id).
    gana_el_orden = RutinaAsignada.objects.filter(
        alumno=OuterRef("alumno"), activa=True, fecha_inicio__lte=hoy,
    ).filter(
        Q(fecha_inicio__gt=OuterRef("fecha_inicio"))
        | Q(fecha_inicio=OuterRef("fecha_inicio"), id__gt=OuterRef("id"))
    )
    return RutinaAsignada.objects.for_gimnasio(gimnasio).filter(
        activa=True,
        fecha_inicio__lte=hoy,
        fecha_inicio__gte=piso,
    ).filter(condiciones_de_persona(hoy, "alumno__")).filter(~Exists(gana_el_orden))


def _nombre_publico(nombre, apellido):
    """«Lucía M.»: nunca el apellido completo."""
    nombre = (nombre or "").strip()
    inicial = (apellido or "").strip()[:1].upper()
    return f"{nombre} {inicial}." if inicial else nombre


def calcular_ranking(gimnasio, alumno=None):
    """Bloques de ranking (los ejercicios con más participantes) del gimnasio.

    `alumno`, si viene, marca su fila (`es_propia`) y, si queda fuera del top
    de un ejercicio, la devuelve aparte en `tu_fila`.
    """
    if not gimnasio.reconocimientos_habilitado:
        return []

    rutinas = list(
        participantes(gimnasio).values_list(
            "id", "alumno_id", "alumno__nombre", "alumno__apellido"
        )
    )
    if not rutinas:
        return []
    alumno_de = {rid: aid for rid, aid, _, _ in rutinas}
    nombre_de = {aid: _nombre_publico(n, a) for _, aid, n, a in rutinas}

    # `order_by()` vacío OBLIGATORIO: el `Meta.ordering` (semana, dia, orden)
    # se sumaría al `SELECT DISTINCT` y no colapsaría nada. El distinct
    # reduce el mismo ejercicio/kilos repetido por día y semana a unas pocas
    # filas por alumno.
    items = (
        RutinaAsignadaItem.objects.filter(rutina_asignada_id__in=alumno_de)
        .exclude(kilos="")
        .order_by()
        .values_list("rutina_asignada_id", "ejercicio_nombre_snapshot", "kilos")
        .distinct()
    )

    maximos = {}  # clave normalizada -> {alumno_id: kilos máximos}
    nombres = {}  # clave normalizada -> Counter de nombres tal como se cargaron
    for rutina_id, ejercicio, texto in items:
        kilos = parsear_kilos(texto)
        clave = normalizar_texto(ejercicio)
        if kilos is None or not clave:
            continue
        por_alumno = maximos.setdefault(clave, {})
        aid = alumno_de[rutina_id]
        if kilos > por_alumno.get(aid, Decimal(0)):
            por_alumno[aid] = kilos
        nombres.setdefault(clave, Counter())[ejercicio.strip()] += 1

    candidatos = [
        (clave, por_alumno) for clave, por_alumno in maximos.items()
        if len(por_alumno) >= MINIMO_PARTICIPANTES
    ]
    candidatos.sort(key=lambda c: (-len(c[1]), c[0]))

    alumno_id = alumno.id if alumno is not None else None
    bloques = []
    for clave, por_alumno in candidatos[:TOP_EJERCICIOS]:
        orden = sorted(por_alumno.items(), key=lambda p: (-p[1], nombre_de[p[0]], p[0]))
        filas = []
        puesto = 0
        anterior = None
        for posicion, (aid, kilos) in enumerate(orden, start=1):
            if kilos != anterior:  # ranking por competencia: 1, 1, 3
                puesto, anterior = posicion, kilos
            filas.append(FilaRanking(puesto, nombre_de[aid], kilos, aid == alumno_id))
        tu_fila = None
        if alumno_id is not None and not any(f.es_propia for f in filas[:TOP_POR_EJERCICIO]):
            tu_fila = next((f for f in filas if f.es_propia), None)
        cuenta = nombres[clave]
        ejercicio = sorted(cuenta.items(), key=lambda p: (-p[1], p[0]))[0][0]
        bloques.append(
            BloqueEjercicio(ejercicio, len(por_alumno), filas[:TOP_POR_EJERCICIO], tu_fila)
        )
    return bloques
