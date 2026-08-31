"""Cierra las rutinas activas duplicadas que dejó el bug de `activa=True`.

Hasta `rutinas/0010`, `RutinaAsignada.activa` era `default=True` y NADIE la
ponía nunca en `False`: asignarle un plan nuevo a un alumno que ya tenía uno
dejaba las dos activas. Las cinco consultas del repo hacen
`filter(activa=True).first()`, así que la que veía el alumno la decidía el
`Meta.ordering` -- y con la misma fecha de inicio (el caso típico, reasignar
el mismo día) ganaba la VIEJA.

`crear_desde_plantilla` ya cierra la anterior, pero eso solo vale de acá en
adelante: los duplicados que ya están en la base hay que cerrarlos, o el
alumno sigue viendo el plan equivocado.

Criterio: por alumno se conserva activa UNA sola -- la más reciente por
`(fecha_inicio, id)`, el mismo desempate que el `Meta.ordering` nuevo, así que
la que queda es exactamente la que el alumno ya está viendo hoy con el código
nuevo. Las demás se archivan (`activa=False`); **no se borra nada**: son el
historial del alumno y sus items siguen ahí.

`fecha_fin` solo se completa si estaba vacía, y con la `fecha_inicio` de la
rutina que la reemplazó -- no con la fecha del deploy, que no significaría
nada para el alumno.
"""

from django.db import migrations


def cerrar_duplicadas(apps, schema_editor):
    RutinaAsignada = apps.get_model("rutinas", "RutinaAsignada")

    activas = list(
        RutinaAsignada.objects.filter(activa=True)
        .order_by("alumno_id", "-fecha_inicio", "-id")
        .values("id", "alumno_id", "fecha_inicio")
    )

    por_alumno = {}
    for fila in activas:
        por_alumno.setdefault(fila["alumno_id"], []).append(fila)

    for filas in por_alumno.values():
        if len(filas) < 2:
            continue
        # `filas[0]` es la que gana (ya vienen ordenadas como el Meta nuevo).
        vigente = filas[0]
        for reemplazada in filas[1:]:
            RutinaAsignada.objects.filter(
                id=reemplazada["id"], fecha_fin__isnull=True
            ).update(fecha_fin=vigente["fecha_inicio"])
            RutinaAsignada.objects.filter(id=reemplazada["id"]).update(activa=False)


def revertir(apps, schema_editor):
    """No se puede deshacer con fidelidad: no queda registro de cuáles estaban
    activas antes. Se deja explícito en vez de reactivar todo a ciegas, que
    reintroduciría el bug."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("rutinas", "0010_alter_rutinaasignada_options"),
    ]

    operations = [
        migrations.RunPython(cerrar_duplicadas, revertir),
    ]
