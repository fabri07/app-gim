"""`grupo_muscular_snapshot` -> `categoria_snapshot`, y de slug a nombre visible.

Hasta acá el snapshot guardaba el VALOR del `TextChoices` global
(`"cuerpo_completo"`) y `rutinas/agrupacion.py` lo traducía a la etiqueta con
un dict module-level construido desde `Ejercicio.GrupoMuscular.choices`. Desde
que las categorías son por gimnasio (`ejercicios.0002`/`0003`) ese dict global
dejó de ser correcto: no hay ningún catálogo único contra el cual traducir.

La salida es guardar el nombre ya renderizado. `agrupacion.py` queda sin
lookup y sin importar `ejercicios`, que es lo que su docstring venía
prometiendo.

**Es un `RenameField`, no un `Remove` + `Add`.** `makemigrations` propone lo
segundo (no puede adivinar que es el mismo campo) y eso borraría el snapshot
de todas las rutinas ya asignadas. `max_length` sube de 20 a 60: 20 alcanzaba
para slugs de un catálogo cerrado, no para una categoría que el gimnasio
escribe.

Reversible en las dos direcciones: el mapa de los 8 valores es fijo y
biyectivo. La vuelta atrás deja en blanco cualquier categoría propia que no
esté en ese mapa -- no hay slug al cual mapearla, y el campo viejo era
`blank=True`, así que es un valor válido y `agrupacion.py` lo bucketea.
"""

from django.db import migrations, models

_TAMANIO_LOTE = 500

_SLUG_A_NOMBRE = {
    "pecho": "Pecho",
    "espalda": "Espalda",
    "piernas": "Piernas",
    "hombros": "Hombros",
    "brazos": "Brazos",
    "core": "Core",
    "cardio": "Cardio",
    "cuerpo_completo": "Cuerpo completo",
}
_NOMBRE_A_SLUG = {nombre: slug for slug, nombre in _SLUG_A_NOMBRE.items()}


def _reescribir(apps, mapa):
    RutinaAsignadaItem = apps.get_model("rutinas", "RutinaAsignadaItem")

    pendientes = list(
        RutinaAsignadaItem.objects.exclude(categoria_snapshot="").filter(
            categoria_snapshot__in=list(mapa)
        )
    )
    if not pendientes:
        return
    for item in pendientes:
        item.categoria_snapshot = mapa[item.categoria_snapshot]
    RutinaAsignadaItem.objects.bulk_update(
        pendientes, ["categoria_snapshot"], batch_size=_TAMANIO_LOTE
    )


def slug_a_nombre(apps, schema_editor):
    _reescribir(apps, _SLUG_A_NOMBRE)


def nombre_a_slug(apps, schema_editor):
    """Las categorías propias del gimnasio quedan en blanco: no existe un
    slug del catálogo viejo al cual mapearlas."""
    RutinaAsignadaItem = apps.get_model("rutinas", "RutinaAsignadaItem")

    _reescribir(apps, _NOMBRE_A_SLUG)
    RutinaAsignadaItem.objects.exclude(categoria_snapshot="").exclude(
        categoria_snapshot__in=list(_SLUG_A_NOMBRE)
    ).update(categoria_snapshot="")


class Migration(migrations.Migration):

    dependencies = [
        ("rutinas", "0006_backfill_grupo_muscular_snapshot"),
        ("ejercicios", "0003_backfill_categorias"),
    ]

    operations = [
        migrations.RenameField(
            model_name="rutinaasignadaitem",
            old_name="grupo_muscular_snapshot",
            new_name="categoria_snapshot",
        ),
        migrations.AlterField(
            model_name="rutinaasignadaitem",
            name="categoria_snapshot",
            field=models.CharField(
                blank=True,
                help_text=(
                    "NOMBRE VISIBLE de la categoría del ejercicio al momento "
                    "de asignar la rutina (no un slug ni una FK): las "
                    "categorías son por gimnasio desde 2026-08-26, así que no "
                    "hay ningún catálogo global contra el cual traducir un "
                    "código. Guardarlo ya renderizado es lo que deja a "
                    "rutinas/agrupacion.py sin lookups. Vacío si el ejercicio "
                    "no tenía categoría, o en asignaciones anteriores al "
                    "campo -- agrupacion.py bucketea esos casos bajo 'Sin "
                    "categoría' en vez de romper."
                ),
                max_length=60,
            ),
        ),
        migrations.RunPython(slug_a_nombre, nombre_a_slug),
    ]
