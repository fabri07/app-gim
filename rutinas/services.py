"""Lógica de negocio de la EDICIÓN de una rutina ya asignada a un alumno.

Vive acá y no en las vistas por el mismo criterio que `turnos/services.py` y
`alumnos/services.py`: cada operación toca varias filas a la vez y tiene que
ser atómica.

REGLA DE PROPAGACIÓN ENTRE SEMANAS (el corazón de este módulo)
--------------------------------------------------------------
Un ejercicio de un día existe hasta 4 veces, una por `semana`. Los "hermanos"
de un item son los que comparten la clave
`(rutina_asignada, dia, ejercicio_nombre_snapshot)`.

- `ejercicio_nombre_snapshot` y `ejercicio_video_snapshot` -> LAS 4 SEMANAS.
- `series`, `repeticiones`, `kilos`, `descanso`, `notas`, `bloque` -> SOLO la
  semana editada (progresar la carga semana a semana es justamente el punto).
- agregar y quitar -> LAS 4 SEMANAS.

El NOMBRE propaga por integridad, no por comodidad:
`rutinas/agrupacion.py::listar_ejercicios_del_dia` identifica "el mismo
ejercicio a través de las semanas" agrupando por `ejercicio_nombre_snapshot`,
así que renombrar UNA semana parte el ejercicio en dos filas distintas en el
portal del alumno (`mi_dia_detalle.html`) y en el PDF. El VIDEO propaga por lo
mismo aguas abajo: `agrupacion.py` toma el primer valor no vacío entre semanas,
de modo que dejarlo desparejo hace que el link que ve el alumno dependa de qué
semana se cargó primero.

Los tres campos de la clave importan y hay un test por cada uno: sacar `dia`
renombraría el mismo ejercicio en los otros días, y sacar `rutina_asignada`
lo renombraría en otras rutinas del mismo alumno.

AISLAMIENTO DE TENANT: ninguna función de este módulo toca
`RutinaAsignadaItem.objects`. Todas reciben la `RutinaAsignada` padre (que la
vista ya resolvió con `for_gimnasio()`) y operan sobre `asignada.items` --
mismo patrón que `rutinas/views.py::ItemPlantillaMixin`.

CONSECUENCIA ACEPTADA sobre la analítica del dueño: `tenants/analitica.py`
(`rpe_por_ejercicio`, `ejercicios_mas_asignados`) agrupa por
`ejercicio_nombre_snapshot`. Hasta que existió este módulo ese texto era
inmutable; ahora es editable, así que renombrar mueve las calificaciones
viejas de bucket y cambia el ranking del dashboard retroactivamente. Es la
extensión directa del riesgo que `CLAUDE.md` ya documenta para el snapshot, y
no tiene arreglo limpio: una FK viva a `Ejercicio` rompería el snapshot, que
es justamente lo que protege la rutina histórica del alumno.
"""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from rutinas.models import RutinaAsignada, RutinaAsignadaItem

# Campos que se escriben SOLO en la semana editada.
CAMPOS_DE_LA_SEMANA = (
    "series",
    "repeticiones",
    "kilos",
    "descanso",
    "notas",
    "bloque",
)


class ErrorDeEdicionDeRutina(Exception):
    """Base de los errores de dominio de este módulo."""


class NombreDuplicadoEnElDia(ErrorDeEdicionDeRutina):
    """Ya hay OTRO ejercicio con ese nombre en el mismo día.

    No es un choque de unicidad de base (no hay constraint): es que
    `agrupacion.py` fusionaría los dos en una sola fila en la vista del alumno
    y en el PDF, mezclando las cargas de dos ejercicios distintos.
    """


class DiaInexistente(ErrorDeEdicionDeRutina):
    """El día no tiene ningún item en esta rutina.

    Crear días nuevos está fuera de alcance -- mismo criterio con el que
    `RutinaMiDiaDetailView` 404ea un día que no existe en la rutina.
    """


def hermanos(asignada, item):
    """Los items del MISMO ejercicio en las 4 semanas del MISMO día, incluido
    `item`. Ordenados por semana vía `Meta.ordering`."""
    return asignada.items.filter(
        dia=item.dia, ejercicio_nombre_snapshot=item.ejercicio_nombre_snapshot
    )


def nombre_libre_en_el_dia(*, asignada, dia, nombre, excepto_nombre=""):
    """Una sola implementación del invariante, compartida por el form (que lo
    traduce a un error de campo) y por el servicio (que es su dueño y lo
    revalida bajo lock).

    `iexact` y no exacto: "Press banca" y "press banca" en el mismo día no se
    fusionarían (`agrupacion.py` agrupa por string exacto), pero son
    claramente el mismo error del entrenador y producen dos filas casi
    idénticas en el portal del alumno.
    """
    nombre = (nombre or "").strip()
    candidatos = asignada.items.filter(dia=dia, ejercicio_nombre_snapshot__iexact=nombre)
    if excepto_nombre:
        candidatos = candidatos.exclude(
            ejercicio_nombre_snapshot__iexact=excepto_nombre.strip()
        )
    return not candidatos.exists()


@transaction.atomic
def editar_ejercicio_asignado(
    *,
    asignada,
    item,
    ejercicio_nombre,
    ejercicio_video,
    series,
    repeticiones,
    kilos="",
    descanso="",
    notas="",
    bloque="",
):
    """Aplica la regla de propagación. Devuelve cuántas semanas tocó el
    renombre.

    Cuatro queries fijas, independientes de la cantidad de semanas o de items:
    lock del padre, chequeo de duplicado, UPDATE de los hermanos, UPDATE del
    item.
    """
    # Lock sobre el PADRE, no sobre los items: serializa todas las ediciones
    # de la misma rutina, que es lo que protege el invariante de "no hay dos
    # nombres iguales en un día" contra un doble submit. Mismo patrón
    # anti-TOCTOU que `turnos/services.py::crear_reserva` (lock sobre
    # `ConfiguracionTurnos`). Es no-op en SQLite, así que la carrera no se
    # puede reproducir en dev -- misma familia de trampa que el `varchar` de
    # Postgres que ya documenta CLAUDE.md.
    RutinaAsignada.objects.select_for_update().get(pk=asignada.pk)

    # El nombre viejo se lee de la BASE, no de `item`. Cuando quien llama es
    # un `UpdateView`, la instancia que recibimos ya pasó por
    # `ModelForm._post_clean`, que le escribió el nombre NUEVO encima: usarla
    # haría que el UPDATE de los hermanos filtre por el nombre nuevo y no
    # actualice ninguna fila, dejando el renombre aplicado a una sola semana
    # -- justo el bug que la regla de propagación existe para evitar. Hay un
    # test de vista que lo cubre.
    nombre_viejo = (
        asignada.items.filter(pk=item.pk)
        .values_list("ejercicio_nombre_snapshot", flat=True)
        .first()
    )
    nombre_nuevo = (ejercicio_nombre or "").strip()

    if not nombre_libre_en_el_dia(
        asignada=asignada,
        dia=item.dia,
        nombre=nombre_nuevo,
        excepto_nombre=nombre_viejo,
    ):
        raise NombreDuplicadoEnElDia(nombre_nuevo)

    ahora = timezone.now()

    # Un solo UPDATE para las 4 semanas. Se filtra por el nombre VIEJO, así
    # que el orden importa: esto tiene que correr antes de tocar el item.
    #
    # `modificado` va explícito porque `QuerySet.update()` NO dispara
    # `auto_now` (`core.models.TimeStampedModel.modificado`). Sin esto el
    # campo de auditoría quedaría mintiendo, sin costar ninguna query extra.
    semanas_tocadas = asignada.items.filter(
        dia=item.dia, ejercicio_nombre_snapshot=nombre_viejo
    ).update(
        ejercicio_nombre_snapshot=nombre_nuevo,
        ejercicio_video_snapshot=ejercicio_video or "",
        modificado=ahora,
    )

    # Los campos de la semana, solo en este item. Se escribe con `update()` y
    # NUNCA con `item.save()`: la instancia en memoria todavía tiene el nombre
    # viejo (y `ModelForm._post_clean` ya le pudo haber puesto el nuevo), así
    # que un `save()` acá pisaría el UPDATE de arriba.
    asignada.items.filter(pk=item.pk).update(
        series=series,
        repeticiones=repeticiones,
        kilos=kilos or "",
        descanso=descanso or "",
        notas=notas or "",
        bloque=bloque or "",
        modificado=ahora,
    )

    return semanas_tocadas


@transaction.atomic
def agregar_ejercicio_asignado(
    *,
    asignada,
    dia,
    ejercicio,
    series,
    repeticiones,
    kilos="",
    descanso="",
    notas="",
    bloque="",
):
    """Suma un ejercicio de la biblioteca a un día de la rutina de ese alumno.

    Crea una fila por cada semana que ESE día ya tiene (en el caso normal, las
    4). No siempre las 4 a ciegas: si la planilla cargó el día solo en las
    semanas 1-3, crear la de la semana 4 inventaría una sesión que no existía
    y habilitaría retroactivamente que el alumno la marque como entrenada
    (`RutinaAsignadaDiaCompletadoToggleView` valida contra la existencia de
    items), ensuciando además el denominador de la adherencia.

    Copia `nombre`, `url_video` y `categoria.nombre` al snapshot, igual que
    `RutinaAsignada.crear_desde_plantilla`: es la misma operación de
    congelado, no una FK viva.

    Cinco queries fijas; ninguna dentro de un loop.
    """
    RutinaAsignada.objects.select_for_update().get(pk=asignada.pk)

    if not nombre_libre_en_el_dia(
        asignada=asignada, dia=dia, nombre=ejercicio.nombre
    ):
        raise NombreDuplicadoEnElDia(ejercicio.nombre)

    del_dia = asignada.items.filter(dia=dia)
    # `orden` es el mismo en las 4 filas nuevas: `agrupacion.py` toma el
    # `orden` del item de la semana más baja, así que órdenes distintos entre
    # semanas darían una posición de fila que no corresponde a ninguna semana
    # en particular. `max + 1` (al final) y no una inserción con renumeración
    # porque reordenar está fuera de alcance.
    orden = (del_dia.aggregate(Max("orden"))["orden__max"] or 0) + 1

    # `dia_nombre` se hereda del día para no dejar el item nuevo como el único
    # sin etiqueta: está denormalizado por item (ver `RutinaPlantillaItem`), y
    # la regla del proyecto para leerlo es "gana la semana más baja".
    semanas_y_nombres = list(
        del_dia.order_by("semana").values_list("semana", "dia_nombre")
    )
    if not semanas_y_nombres:
        raise DiaInexistente(dia)

    semanas = sorted({semana for semana, _ in semanas_y_nombres})
    dia_nombre = next(
        (nombre for _, nombre in semanas_y_nombres if nombre),
        "",
    )

    categoria = ejercicio.categoria.nombre if ejercicio.categoria_id else ""

    return RutinaAsignadaItem.objects.bulk_create(
        [
            RutinaAsignadaItem(
                rutina_asignada=asignada,
                ejercicio_nombre_snapshot=ejercicio.nombre.strip(),
                ejercicio_video_snapshot=ejercicio.url_video,
                categoria_snapshot=categoria,
                semana=semana,
                dia=dia,
                dia_nombre=dia_nombre,
                orden=orden,
                series=series,
                repeticiones=repeticiones,
                kilos=kilos or "",
                descanso=descanso or "",
                notas=notas or "",
                bloque=bloque or "",
            )
            for semana in semanas
        ]
    )


@transaction.atomic
def quitar_ejercicio_asignado(*, asignada, item):
    """Borra el ejercicio en TODAS las semanas de ese día. Devuelve cuántas
    filas borró.

    Se lleva puesto el `rpe` que el alumno haya cargado en ese ejercicio, y no
    hay deshacer -- por eso el botón de la UI dice explícitamente "Quitar de
    las 4 semanas" en vez de un "Eliminar" genérico.

    Los `RutinaAsignadaDiaCompletado` del día NO se tocan aunque este haya
    sido su último ejercicio: que el alumno entrenó ese día es un hecho
    histórico. `progreso.adherencia_de_rutina` los ignora intersecando contra
    las sesiones que todavía tienen items.
    """
    RutinaAsignada.objects.select_for_update().get(pk=asignada.pk)
    borradas, _ = hermanos(asignada, item).delete()
    return borradas
