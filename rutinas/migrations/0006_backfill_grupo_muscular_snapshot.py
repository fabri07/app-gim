"""Backfillea `RutinaAsignadaItem.grupo_muscular_snapshot` para los items
creados antes de la migración 0005 (que agregó el campo sin backfill).

Busca, por gimnasio, un `Ejercicio` cuyo `nombre` coincida (case-insensitive)
con `ejercicio_nombre_snapshot` y copia su `grupo_muscular`. Si no hay match
o el ejercicio no tiene grupo muscular cargado, el item queda como está
(mismo comportamiento actual: `rutinas/agrupacion.py` lo bucketea bajo "Sin
grupo muscular").

Precarga un mapa `{gimnasio_id: {nombre_lower: grupo_muscular}}` en vez de
consultar `Ejercicio` por cada item (N+1) y actualiza todo con un solo
`bulk_update` -- importa en gimnasios con backlog grande, donde el paso de
`migrate` del deploy tiene presupuesto de tiempo acotado (Render free
tier). Si hay dos `Ejercicio` con el mismo nombre (case-insensitive) en el
mismo gimnasio, gana el de `id` más chico -- desempate determinístico, en
vez de un `.first()` sin `order_by` que dependía del orden de la base."""

from django.db import migrations


def backfill_grupo_muscular(apps, schema_editor):
    RutinaAsignadaItem = apps.get_model("rutinas", "RutinaAsignadaItem")
    Ejercicio = apps.get_model("ejercicios", "Ejercicio")

    items_sin_grupo = list(
        RutinaAsignadaItem.objects.filter(
            grupo_muscular_snapshot=""
        ).select_related("rutina_asignada")
    )
    if not items_sin_grupo:
        return

    gimnasio_ids = {item.rutina_asignada.gimnasio_id for item in items_sin_grupo}

    mapa_por_gimnasio = {}
    for ejercicio in (
        Ejercicio.objects.filter(gimnasio_id__in=gimnasio_ids)
        .exclude(grupo_muscular="")
        .order_by("id")
    ):
        mapa_por_gimnasio.setdefault(ejercicio.gimnasio_id, {}).setdefault(
            ejercicio.nombre.lower(), ejercicio.grupo_muscular
        )

    actualizados = []
    for item in items_sin_grupo:
        mapa = mapa_por_gimnasio.get(item.rutina_asignada.gimnasio_id, {})
        grupo = mapa.get(item.ejercicio_nombre_snapshot.lower())
        if grupo:
            item.grupo_muscular_snapshot = grupo
            actualizados.append(item)

    if actualizados:
        RutinaAsignadaItem.objects.bulk_update(
            actualizados, ["grupo_muscular_snapshot"]
        )


def noop_reverse(apps, schema_editor):
    """Irreversible a propósito: no hay forma de distinguir un snapshot
    backfillado acá de uno cargado por `crear_desde_plantilla`."""


class Migration(migrations.Migration):

    dependencies = [
        ("rutinas", "0005_rutinaasignadaitem_grupo_muscular_snapshot_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_grupo_muscular, noop_reverse),
    ]
