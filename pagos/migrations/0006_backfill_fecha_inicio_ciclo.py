"""Ancla el ciclo de cobro de cada alumno que ya existe.

**La regla acá es el punto más delicado de toda la migración.** La versión
intuitiva —anclar en la fecha de la primera rutina del alumno, que es la regla
de producto para los alumnos NUEVOS— está mal para los que ya existen, y no en
un caso de borde sino para todos:

    ciclo vigente = ancla + 28k, que por construcción cae en [hoy-27, hoy]

o sea que su período SIEMPRE se solapa con la cuota calendario del mes en
curso, que el cron viejo ya emitió y el gimnasio probablemente ya cobró. El
alumno terminaría con dos cuotas cubriendo los mismos días.

La regla correcta para el histórico es **el primer día que su última cuota no
cubre**: `max(periodo_fin) + 1 día`. Con eso el régimen de 28 días arranca
exactamente donde termina el último mes ya facturado — sin solape, sin cuota
duplicada en el mes de transición, y sin que nada nazca vencido (mientras
`hoy < ancla` no se emite nada, ver `pagos.models.ciclo_vigente`).

Para el alumno sin ninguna cuota el ancla es hoy.

Una sola query agregada: anclar esto con un bucle por alumno es exactamente el
N+1 que este proyecto ya pagó con un 502 en producción.
"""

from datetime import timedelta

from django.db import migrations
from django.db.models import Max
from django.utils import timezone


def backfill_ancla(apps, schema_editor):
    Alumno = apps.get_model("alumnos", "Alumno")
    hoy = timezone.localdate()

    pendientes = list(
        Alumno.objects.filter(fecha_inicio_ciclo__isnull=True).annotate(
            _ultimo_periodo=Max("pagos__periodo_fin")
        )
    )
    for alumno in pendientes:
        ultimo = alumno._ultimo_periodo
        alumno.fecha_inicio_ciclo = (ultimo + timedelta(days=1)) if ultimo else hoy

    if pendientes:
        Alumno.objects.bulk_update(pendientes, ["fecha_inicio_ciclo"], batch_size=500)


def limpiar_ancla(apps, schema_editor):
    Alumno = apps.get_model("alumnos", "Alumno")
    Alumno.objects.update(fecha_inicio_ciclo=None)


class Migration(migrations.Migration):

    dependencies = [
        ("pagos", "0005_cuota_periodo"),
        ("alumnos", "0005_alumno_fecha_inicio_ciclo"),
    ]

    operations = [
        migrations.RunPython(backfill_ancla, limpiar_ancla),
    ]
