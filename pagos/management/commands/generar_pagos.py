"""
Command de management que dispara la autogeneración mensual de pagos.

Lo programa `.github/workflows/generar-pagos.yml` (GitHub Actions, no Render:
no hay plan free para cron jobs ahí). Corre para el mes/año ACTUAL: genera los
`Cuota` pendientes del mes en curso y, de paso, vence los pendientes de
meses anteriores que quedaron sin confirmar, además de los del mes en curso
que ya pasaron el `dia_vencimiento_pago` de su gimnasio. Es idempotente:
correrlo dos veces no duplica nada.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import generar_pagos_pendientes, marcar_vencidos


class Command(BaseCommand):
    help = (
        "Autogenera los Cuota pendientes del mes actual para cada "
        "alumno activo, y marca como vencidos los pendientes de meses "
        "anteriores o del mes actual ya pasado su día de vencimiento."
    )

    def handle(self, *args, **options):
        # `localtime()` y NO `now()`: `now()` es UTC y `TIME_ZONE` es
        # `America/Argentina/Buenos_Aires`, así que entre las 21:00 y las
        # 23:59 la fecha UTC ya es la de mañana. Corrido a mano el último día
        # del mes por la noche, esto emitía las cuotas del mes SIGUIENTE con
        # `dia=1`. La corrida agendada (06:30 UTC = 03:30 local) cae fuera de
        # esa ventana, pero la corrida manual es justo la que se hace cuando
        # algo ya salió mal.
        ahora = timezone.localtime()
        mes, anio, dia = ahora.month, ahora.year, ahora.day

        creados = generar_pagos_pendientes(mes, anio)
        vencidos = marcar_vencidos(mes, anio, dia)

        self.stdout.write(
            self.style.SUCCESS(
                f"Pagos generados para {mes:02d}/{anio}: {creados}. "
                f"Pagos marcados como vencidos: {vencidos}."
            )
        )
