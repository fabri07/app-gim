"""Descarta las importaciones de biblioteca que quedaron a medio revisar.

`0003_backfill_categorias` de `ejercicios` cambió la forma de
`Importacion.resultado`: cada item pasó de tener `grupo_muscular_resuelto`
(un slug del catálogo global) a `categoria_resuelta` (un dict que apunta a
una `CategoriaEjercicio` o describe una a crear).

Una `Importacion` en `en_revision` guardada ANTES de ese cambio tiene el
`resultado` con la forma vieja, y su pantalla de preview la lee para decidir
qué le falta resolver al staff. Sin esta migración, abrir el link de una de
esas importaciones después del deploy da un 500. En producción había al menos
dos así al momento de escribir esto.

Se descartan en vez de intentar convertirlas: su `resultado` no tiene ninguna
categoría resuelta -- se generó justamente cuando la columna CATEGORÍA no se
detectaba-- así que convertirlo daría un preview con todo pendiente de
resolver a mano, que es peor que volver a subir el archivo y que entre
clasificado solo. El `.xlsx` original queda guardado en la fila, no se pierde
nada.

Solo BIBLIOTECA: el `resultado` de las de plantillas no cambió de forma.
"""

from django.db import migrations


def descartar_en_revision(apps, schema_editor):
    Importacion = apps.get_model("importaciones", "Importacion")

    Importacion.objects.filter(tipo="biblioteca", estado="en_revision").update(
        estado="descartada"
    )


def noop_reverse(apps, schema_editor):
    """Irreversible a propósito: una vez descartadas no hay forma de saber
    cuáles estaban en revisión por esta migración y cuáles las descartó el
    staff a mano."""


class Migration(migrations.Migration):

    dependencies = [
        ("importaciones", "0001_initial"),
        ("ejercicios", "0003_backfill_categorias"),
    ]

    operations = [
        migrations.RunPython(descartar_en_revision, noop_reverse),
    ]
