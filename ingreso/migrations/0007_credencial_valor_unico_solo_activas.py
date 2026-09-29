# La unicidad de `(gimnasio, tipo, valor)` pasa a ser PARCIAL sobre las
# credenciales activas: dar de baja una credencial libera su valor (el id
# facial 42 de un ex-alumno se le puede reasignar a otro). Relajar una
# unicidad no puede fallar sobre datos existentes; la vuelta atrás sí puede,
# si ya hay dos filas con el mismo valor (una inactiva y otra activa).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ingreso', '0006_dispositivo_nombre_sin_mayusculas'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='credencial',
            name='credencial_valor_unico_por_gim',
        ),
        migrations.AddConstraint(
            model_name='credencial',
            constraint=models.UniqueConstraint(condition=models.Q(('activa', True)), fields=('gimnasio', 'tipo', 'valor'), name='credencial_valor_activo_unico_por_gim'),
        ),
    ]
