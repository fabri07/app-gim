"""Congelar una cuenta: `estado_cuenta` + desde cuándo.

**No lleva migración de datos.** El `default=NORMAL` es justamente lo que
garantiza que el deploy no bloquee a nadie: todos los gimnasios que ya
existían siguen exactamente como estaban, y congelar una cuenta es siempre una
decisión manual del dueño del producto desde el panel.

El `AlterField` sobre `activo` es solo su `help_text` nuevo (no bloquea el
acceso; para eso está `estado_cuenta`): no toca la columna ni los datos.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0013_gimnasio_facturacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='gimnasio',
            name='estado_cuenta',
            field=models.CharField(choices=[('normal', 'Normal'), ('alumnos_bloqueados', 'Alumnos bloqueados'), ('suspendida', 'Suspendida')], default='normal', help_text='Congelar el acceso por falta de pago. Los datos del gimnasio no se tocan en ningún caso.', max_length=20, verbose_name='estado de la cuenta'),
        ),
        migrations.AddField(
            model_name='gimnasio',
            name='estado_cuenta_desde',
            field=models.DateTimeField(blank=True, null=True, verbose_name='en ese estado desde'),
        ),
        migrations.AlterField(
            model_name='gimnasio',
            name='activo',
            field=models.BooleanField(default=True, help_text='Gimnasio en uso. Destildado se oculta de la landing pública y deja de facturarse, pero no bloquea el acceso de nadie: para eso está «estado de la cuenta».'),
        ),
    ]
