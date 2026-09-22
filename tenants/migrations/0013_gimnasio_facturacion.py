"""
Los dos campos con los que la plataforma le factura a cada gimnasio, más el
estampado que hace que prender la facturación NO sea retroactivo.
"""

from django.db import migrations, models
from django.utils import timezone


def estampar_inicio_de_facturacion(Gimnasio):
    """Le pone a cada gimnasio ya existente el día de HOY como arranque de
    facturación.

    Sin esto, `plataforma.facturacion.inicio_efectivo` cae en la fecha de alta
    y la primera carga del panel muestra a TODOS los clientes como vencidos,
    con meses de atraso por un período que nunca se les cobró ni se les va a
    cobrar. Es exactamente el criterio de `Gimnasio.fecha_activacion_bloqueo`:
    una regla de cobro nueva no se aplica para atrás.

    Los gimnasios creados DESPUÉS quedan en `NULL`, que significa "desde la
    fecha de alta" — para ellos la prueba de 30 días arranca cuando de verdad
    arrancaron.

    `timezone.localdate()` y no `now().date()`: con UTC-3, entre las 21:00 y
    las 23:59 la fecha UTC ya es la de mañana, y correr el deploy de noche le
    regalaría un día de prueba a todo el padrón.

    El filtro por `isnull` la deja idempotente: correrla dos veces (o
    aplicarla sobre una base que ya la tiene) no pisa una fecha elegida a
    mano. Recibe el modelo por parámetro para poder llamarla desde un test con
    el modelo real.
    """
    return Gimnasio.objects.filter(facturacion_inicio__isnull=True).update(
        facturacion_inicio=timezone.localdate()
    )


def _estampar(apps, schema_editor):
    estampar_inicio_de_facturacion(apps.get_model("tenants", "Gimnasio"))


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0012_gimnasio_exportacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="gimnasio",
            name="facturacion_inicio",
            field=models.DateField(
                blank=True,
                help_text=(
                    "Desde qué día se le factura el uso de la app. Vacío = "
                    "desde la fecha de alta."
                ),
                null=True,
                verbose_name="inicio de facturación",
            ),
        ),
        migrations.AddField(
            model_name="gimnasio",
            name="facturacion_exenta",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "No se le cobra el uso de la app ni suma al ingreso "
                    "esperado."
                ),
                verbose_name="exenta de facturación",
            ),
        ),
        # `noop` al revés: volver atrás borra la columna entera, así que no
        # hay nada que deshacer fila por fila.
        migrations.RunPython(_estampar, migrations.RunPython.noop),
    ]
