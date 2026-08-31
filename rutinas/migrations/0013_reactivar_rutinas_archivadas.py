"""Deshace el archivado de `0011` y el de `crear_desde_plantilla`.

CONTEXTO. `rutinas/0011` corrió en producción y, por alumno, conservó activa la
rutina más reciente por `(fecha_inicio, id)` archivando el resto. Ese criterio
**no miraba si esa fecha ya había llegado**, porque en ese momento la vigencia
la daba el flag `activa` y no las fechas.

Ahora la vigencia la decide `RutinaAsignada.vigente_de`: la más reciente que YA
arrancó. Con ese criterio, las filas que `0011` archivó rompen un caso real:

    Alumno con P1 (empezó el 01/08, es la que está entrenando) y P2
    (programada al 01/10). `0011` conservó P2 -- la más reciente -- y archivó
    P1. Con el criterio nuevo P1 queda fuera por `activa=False` y P2 por ser
    futura: el alumno se queda SIN NINGUNA rutina.

Lo mismo vale para las asignaciones hechas desde el deploy de `c6f2a5c`, donde
`crear_desde_plantilla` archivaba la anterior al crear la nueva -- eso es
justamente lo que este cambio revierte, porque el alumno tiene que poder
terminar sus 4 semanas aunque el profesor ya haya cargado el siguiente.

POR QUÉ ES SEGURO REACTIVAR TODO. Los únicos dos escritores de `activa=False`
en la historia del repo son `crear_desde_plantilla` (revertido en este mismo
cambio) y `0011`. **Ningún camino de UI escribe ese flag** -- no existía
pantalla para archivar -- así que reactivar en bloque no puede estar pisando
una decisión humana. (El botón "Archivar" que se agrega ahora es posterior a
esta migración, así que tampoco.)

Después de esto, `activa` pasa a significar exclusivamente "archivada a mano".
"""

from django.db import migrations


def reactivar(apps, schema_editor):
    RutinaAsignada = apps.get_model("rutinas", "RutinaAsignada")
    RutinaAsignada.objects.filter(activa=False).update(activa=True)


def revertir(apps, schema_editor):
    """No se puede deshacer con fidelidad -- igual que `0011.revertir`, no
    queda registro de cuáles estaban archivadas antes de esta migración.
    Volver a archivarlas a ciegas dejaría alumnos sin rutina, que es
    exactamente lo que esta migración viene a evitar."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("rutinas", "0012_alter_rutinaasignada_activa_and_more"),
    ]

    operations = [
        migrations.RunPython(reactivar, revertir),
    ]
