"""
Command de management que dispara la autogeneración mensual de pagos.

Lo programa `.github/workflows/generar-pagos.yml` (GitHub Actions, no Render:
no hay plan free para cron jobs ahí). Corre para el mes/año ACTUAL: genera los
`PagoMensual` pendientes del mes en curso y, de paso, vence los pendientes de
meses anteriores que quedaron sin confirmar, además de los del mes en curso
que ya pasaron el `dia_vencimiento_pago` de su gimnasio. Es idempotente:
correrlo dos veces no duplica nada.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import generar_pagos_pendientes, marcar_vencidos


class Command(BaseCommand):
    help = (
        "Autogenera los PagoMensual pendientes del mes actual para cada "
        "alumno activo, y marca como vencidos los pendientes de meses "
        "anteriores o del mes actual ya pasado su día de vencimiento."
    )

    def handle(self, *args, **options):
        ahora = timezone.now()
        mes, anio, dia = ahora.month, ahora.year, ahora.day

        creados = generar_pagos_pendientes(mes, anio)
        vencidos = marcar_vencidos(mes, anio, dia)

        self.stdout.write(
            self.style.SUCCESS(
                f"Pagos generados para {mes:02d}/{anio}: {creados}. "
                f"Pagos marcados como vencidos: {vencidos}."
            )
        )
