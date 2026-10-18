import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tienda', '0004_pedido_referencia_externa_datos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedido',
            name='referencia_externa',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True, unique=True),
        ),
    ]
