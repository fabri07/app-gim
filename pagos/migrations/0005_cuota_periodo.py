"""La cuota deja de ser (mes, año) calendario y pasa a ser un período con fechas.

Escrita a mano y no por `makemigrations` porque el orden de las operaciones
importa y porque `periodo_inicio`/`periodo_fin` son NOT NULL en el modelo
final: hay que agregarlas nullable, rellenarlas y recién ahí apretarlas.

Tres cosas que NO se pueden diferir a otra migración, aunque tiente:

1. `mes`/`anio` pasan a `null=True`. Siguen existiendo (son la red de la
   vuelta atrás, ver el modelo) pero el generador nuevo ya no las trata como
   parte de la identidad de la fila.
2. El `unique_together` pasa a `(gimnasio, alumno, periodo_inicio)`. Con
   ciclos de 28 días hay 13 arranques por año contra 12 meses, así que un
   alumno produce DOS `periodo_inicio` en el mismo mes calendario cada vez que
   el primero cae entre el día 1 y el 3. Con el unique viejo vivo, esa segunda
   cuota lo viola y —al emitirse por `bulk_create`, que es una sola sentencia—
   aborta la emisión del gimnasio entero, todos los días.
3. `estado` suma ANULADO.

El backfill convierte el histórico a MES CALENDARIO (día 1 → último día del
mes), no a ventanas de 28 días: esas cuotas *fueron* mensuales y esa es la
verdad contable. Además hace que `ingresos_por_mes`, que pasa a agrupar por
`TruncMonth("periodo_inicio")`, devuelva exactamente los mismos números que
antes para todo el pasado.
"""

import calendar
from datetime import date

import django.core.validators
from django.db import migrations, models

LOTE = 500


def _guardar(Cuota, lote, campos):
    if lote:
        Cuota.objects.bulk_update(lote, campos, batch_size=LOTE)
        lote.clear()


def backfill_periodos(apps, schema_editor):
    """(mes, anio) -> [primer día del mes, último día del mes]."""
    Cuota = apps.get_model("pagos", "Cuota")
    lote = []
    for cuota in Cuota.objects.filter(periodo_inicio__isnull=True).only(
        "id", "mes", "anio", "periodo_inicio", "periodo_fin"
    ):
        ultimo_dia = calendar.monthrange(cuota.anio, cuota.mes)[1]
        cuota.periodo_inicio = date(cuota.anio, cuota.mes, 1)
        cuota.periodo_fin = date(cuota.anio, cuota.mes, ultimo_dia)
        lote.append(cuota)
        if len(lote) >= LOTE:
            _guardar(Cuota, lote, ["periodo_inicio", "periodo_fin"])
    _guardar(Cuota, lote, ["periodo_inicio", "periodo_fin"])


def revertir_periodos(apps, schema_editor):
    """Reverse REAL, no un `noop`.

    La conversión `(mes, anio) -> date(anio, mes, 1)` es inyectiva, así que el
    mes y el año se recuperan exactos. Importa para las cuotas creadas DESPUÉS
    de esta migración: al desaplicarla se les vuelven a derivar `mes`/`anio`
    de su período, y el código viejo (que filtra por esas dos columnas) las
    encuentra igual.
    """
    Cuota = apps.get_model("pagos", "Cuota")
    lote = []
    for cuota in Cuota.objects.filter(periodo_inicio__isnull=False).only(
        "id", "mes", "anio", "periodo_inicio"
    ):
        cuota.mes = cuota.periodo_inicio.month
        cuota.anio = cuota.periodo_inicio.year
        lote.append(cuota)
        if len(lote) >= LOTE:
            _guardar(Cuota, lote, ["mes", "anio"])
    _guardar(Cuota, lote, ["mes", "anio"])


class Migration(migrations.Migration):

    dependencies = [
        ("pagos", "0004_rename_pagomensual_cuota_alter_cuota_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="cuota",
            name="periodo_inicio",
            field=models.DateField(null=True, verbose_name="Inicio del período"),
        ),
        migrations.AddField(
            model_name="cuota",
            name="periodo_fin",
            field=models.DateField(null=True, verbose_name="Fin del período"),
        ),
        migrations.RunPython(backfill_periodos, revertir_periodos),
        migrations.AlterField(
            model_name="cuota",
            name="periodo_inicio",
            field=models.DateField(verbose_name="Inicio del período"),
        ),
        migrations.AlterField(
            model_name="cuota",
            name="periodo_fin",
            field=models.DateField(verbose_name="Fin del período"),
        ),
        migrations.AlterField(
            model_name="cuota",
            name="mes",
            field=models.PositiveSmallIntegerField(
                blank=True,
                editable=False,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(12),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="cuota",
            name="anio",
            field=models.PositiveSmallIntegerField(
                blank=True, editable=False, null=True
            ),
        ),
        migrations.AlterField(
            model_name="cuota",
            name="estado",
            field=models.CharField(
                choices=[
                    ("pendiente", "Pendiente"),
                    ("pagado", "Pagado"),
                    ("vencido", "Vencido"),
                    ("anulado", "Anulada"),
                ],
                default="pendiente",
                max_length=10,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="cuota",
            unique_together={("gimnasio", "alumno", "periodo_inicio")},
        ),
        migrations.AlterModelOptions(
            name="cuota",
            options={
                "ordering": ["-periodo_inicio"],
                "verbose_name": "cuota",
                "verbose_name_plural": "cuotas",
            },
        ),
    ]
