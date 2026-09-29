"""Borra los eventos de ingreso viejos de un gimnasio, desde la Shell.

    python manage.py purgar_ingresos --gimnasio vida-plena --meses 24 --dry-run
    python manage.py purgar_ingresos --gimnasio vida-plena --meses 24

Existe por el techo del exportador, no "por si alguna vez molesta":
`tenants/exportacion.py::MAX_FILAS_WEB` es 60.000 y un gimnasio de 300 alumnos
genera del orden de 72.000 eventos por año, así que sin purga la exportación
deja de generarse justo cuando el cliente se quiere ir. Es **retención
periódica**: mantiene al botón «Exportar mis datos» por debajo del techo en la
operación normal.

**Nunca se corre como paso previo a exportar los datos de un gimnasio que los
pide**: le borraría justo el historial de asistencia que se quiere llevar. Ese
caso se resuelve con `manage.py exportar_gimnasio`, que no tiene techo.

**El corte son meses de CALENDARIO, no bloques de 30 días**: se toma el «hoy»
del dominio con `timezone.localdate()`, se le restan `--meses` meses (si ese día
no existe en el mes de destino, cae en el último: 31/03 menos un mes es 28/02),
y el corte es la MEDIANOCHE LOCAL de ese día (`ingreso.presentacion.
inicio_del_dia`). Se borra lo que tiene `momento` ESTRICTAMENTE anterior al
corte; un evento justo en la medianoche del corte se conserva. Meses de
calendario porque es como lo lee una persona («los últimos dos años») y así el
corte cae siempre en el mismo día del mes, sin derivar. Con `timezone.now()
.date()` el corte se correría un día entre las 21:00 y las 23:59 (UTC-3).

**Solo toca `EventoIngreso`**, y solo los del gimnasio indicado (con o sin
alumno). Ningún modelo tiene FK hacia `EventoIngreso`, así que el `delete()`
no dispara cascadas: lectores (con sus contadores `eventos_hoy` y
`ultimo_contacto`), credenciales y alumnos quedan como estaban.

**Borra por tandas de pk**, cada una en su propia transacción corta, para no
tener la tabla bloqueada durante un borrado de decenas de miles de filas. Se
pagina con `pk__gt` y nunca con `.iterator()`: con
`disable_server_side_cursors` (obligatorio por PgBouncer) el driver trae el
resultado entero a memoria igual. Mismo criterio que `tenants/exportacion.py`.

`--meses` tiene un mínimo de `MESES_MINIMOS`: un `--meses 1` tipeado de apuro
se llevaría los ingresos recientes, que son justo los que explican un reclamo
de una cuota (ciclos de 28 días) o de un acceso rechazado.
"""

import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingreso.models import EventoIngreso
from ingreso.presentacion import inicio_del_dia
from tenants.models import Gimnasio

TAMANIO_TANDA = 2000
MESES_POR_DEFECTO = 24
MESES_MINIMOS = 3


def restar_meses(dia, meses):
    """`dia` menos `meses` meses de calendario; si el día no existe en el mes
    de destino, el último de ese mes. Pura y sin dependencias (`dateutil` no es
    dependencia del proyecto)."""
    indice = dia.year * 12 + (dia.month - 1) - meses
    anio, mes = divmod(indice, 12)
    mes += 1
    return date(anio, mes, min(dia.day, calendar.monthrange(anio, mes)[1]))


class Command(BaseCommand):
    help = "Borra los eventos de ingreso de un gimnasio anteriores a N meses."

    def add_arguments(self, parser):
        parser.add_argument("--gimnasio", required=True, help="Slug del gimnasio.")
        parser.add_argument(
            "--meses", type=int, default=MESES_POR_DEFECTO,
            help=f"Meses de historial a conservar (default {MESES_POR_DEFECTO}, "
            f"mínimo {MESES_MINIMOS}).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="No borra nada: informa cuántos eventos borraría.",
        )

    def handle(self, *args, **options):
        meses = options["meses"]
        if meses < MESES_MINIMOS:
            raise CommandError(f"--meses tiene que ser al menos {MESES_MINIMOS}.")
        try:
            gimnasio = Gimnasio.objects.get(slug=options["gimnasio"])
        except Gimnasio.DoesNotExist:
            raise CommandError(f"No existe un gimnasio con slug «{options['gimnasio']}».")

        dia_corte = restar_meses(timezone.localdate(), meses)
        viejos = EventoIngreso.objects.filter(
            gimnasio=gimnasio, momento__lt=inicio_del_dia(dia_corte)
        )

        if options["dry_run"]:
            total = viejos.count()
            verbo = "se borrarían"
        else:
            total = _borrar_por_tandas(viejos)
            verbo = "borrados"

        self.stdout.write(
            f"{gimnasio.slug}: {total} eventos de ingreso {verbo} "
            f"(anteriores al {dia_corte:%d/%m/%Y})"
        )


def _borrar_por_tandas(queryset):
    """Borra `queryset` en tandas de `TAMANIO_TANDA` pk, una transacción por
    tanda. Devuelve cuántas filas borró."""
    base = queryset.order_by("pk").values_list("pk", flat=True)
    total = 0
    ultimo_pk = 0
    while True:
        pks = list(base.filter(pk__gt=ultimo_pk)[:TAMANIO_TANDA])
        if not pks:
            return total
        with transaction.atomic():
            borrados, _ = queryset.filter(pk__in=pks).delete()
        total += borrados
        if len(pks) < TAMANIO_TANDA:
            return total
        ultimo_pk = pks[-1]
