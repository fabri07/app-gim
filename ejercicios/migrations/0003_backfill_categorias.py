"""Convierte `Ejercicio.grupo_muscular` (texto de un catálogo global cerrado)
en `Ejercicio.categoria` (FK a un catálogo propio de cada gimnasio).

Por qué el cambio: el catálogo cerrado era anatómico (Pecho/Espalda/...) y un
gimnasio funcional real clasifica por patrón de movimiento (EMPUJE, TRACCIÓN,
RODILLA, CADERA) más bloques y skills (MOVILIDAD, MUSCLE UP, HANDSTAND). De
sus 13 categorías, una sola entraba en la lista fija. Ver `ISSUES.md`
[2026-08-26].

Siembra **solo las categorías que cada gimnasio realmente usa**, no las 8: un
gimnasio con ejercicios únicamente en Pecho y Piernas no tiene por qué
arrancar con seis categorías vacías ensuciándole el filtro y las zonas de
drag-and-drop del importador. Decisión explícita del dueño del producto.

Los nombres se siembran con la ETIQUETA legible ("Cuerpo completo"), no con el
valor de base ("cuerpo_completo"), porque de acá en más el nombre ES lo que el
staff lee y edita: ya no hay una capa de `get_..._display()` que lo traduzca.

Idempotente (`get_or_create` + solo toca ejercicios sin categoría), porque un
`migrate` cortado a la mitad en Render se reintenta.

Ojo con `apps.get_model`: los modelos históricos NO conservan el `save()`
custom de `CategoriaEjercicio`, así que `nombre_normalizado` se calcula acá a
mano. Si se olvidara, la `UniqueConstraint` quedaría sostenida por una columna
vacía y el importador crearía una categoría por cada variante de mayúsculas.
"""

from django.db import migrations

from importaciones.parsing import normalizar_texto

_TAMANIO_LOTE = 500

# Mismo orden de declaración que el `TextChoices` original: el staff ya está
# acostumbrado a ver Pecho antes que Core, no un listado alfabético.
_ETIQUETAS_EN_ORDEN = [
    ("pecho", "Pecho"),
    ("espalda", "Espalda"),
    ("piernas", "Piernas"),
    ("hombros", "Hombros"),
    ("brazos", "Brazos"),
    ("core", "Core"),
    ("cardio", "Cardio"),
    ("cuerpo_completo", "Cuerpo completo"),
]


def backfill_categorias(apps, schema_editor):
    CategoriaEjercicio = apps.get_model("ejercicios", "CategoriaEjercicio")
    Ejercicio = apps.get_model("ejercicios", "Ejercicio")

    pendientes = list(
        Ejercicio.objects.filter(categoria__isnull=True)
        .exclude(grupo_muscular__isnull=True)
        .exclude(grupo_muscular="")
    )
    if not pendientes:
        return

    grupos_por_gimnasio = {}
    for ejercicio in pendientes:
        grupos_por_gimnasio.setdefault(ejercicio.gimnasio_id, set()).add(
            ejercicio.grupo_muscular
        )

    categoria_por_clave = {}
    for gimnasio_id, grupos_en_uso in grupos_por_gimnasio.items():
        orden = 0
        for valor, etiqueta in _ETIQUETAS_EN_ORDEN:
            if valor not in grupos_en_uso:
                continue
            categoria, _ = CategoriaEjercicio.objects.get_or_create(
                gimnasio_id=gimnasio_id,
                nombre_normalizado=normalizar_texto(etiqueta),
                defaults={"nombre": etiqueta, "orden": orden},
            )
            categoria_por_clave[(gimnasio_id, valor)] = categoria
            orden += 1

    for ejercicio in pendientes:
        ejercicio.categoria = categoria_por_clave[
            (ejercicio.gimnasio_id, ejercicio.grupo_muscular)
        ]
    Ejercicio.objects.bulk_update(
        pendientes, ["categoria"], batch_size=_TAMANIO_LOTE
    )


def revertir(apps, schema_editor):
    """Sirve como vuelta atrás inmediata del deploy: `grupo_muscular` sigue
    poblado (la columna vieja no se borra en este release a propósito), así
    que soltar las FK y borrar el catálogo no pierde nada.

    NO es una vuelta atrás segura semanas después: las categorías que el
    staff haya creado desde el importador o el panel, y los ejercicios nuevos
    que solo tengan `categoria`, sí se perderían -- esos nunca tuvieron un
    `grupo_muscular` de dónde reconstruirse.
    """
    CategoriaEjercicio = apps.get_model("ejercicios", "CategoriaEjercicio")
    Ejercicio = apps.get_model("ejercicios", "Ejercicio")

    Ejercicio.objects.filter(categoria__isnull=False).update(categoria=None)
    CategoriaEjercicio.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ejercicios", "0002_alter_ejercicio_grupo_muscular_categoriaejercicio_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_categorias, revertir),
    ]
