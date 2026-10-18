import uuid

from django.db import migrations


def llenar_referencias(apps, schema_editor):
    """Cada pedido que ya existe recibe su propio uuid. Se hace de a uno (no
    hay un UPDATE con un uuid distinto por fila que sea portable entre SQLite
    y Postgres) y sólo sobre los NULL, así que se puede repetir."""
    Pedido = apps.get_model("tienda", "Pedido")
    pks = list(Pedido.objects.filter(referencia_externa__isnull=True).values_list("pk", flat=True))
    for pk in pks:
        Pedido.objects.filter(pk=pk).update(referencia_externa=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0003_mercadopago_modelos'),
    ]

    operations = [
        # `reverse_code=noop`: la columna se borra al revertir 0003.
        migrations.RunPython(llenar_referencias, reverse_code=migrations.RunPython.noop),
    ]
