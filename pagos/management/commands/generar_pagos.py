"""
Command de management que dispara la autogeneración mensual de pagos.

Este es el comando que Fase 5 programa como Render Cron Job (acá solo se
define y se prueba que funciona; la programación del cron en sí queda para
esa fase). Corre para el mes/año ACTUAL: genera los `PagoMensual` pendientes
del mes en curso y, de paso, vence los pendientes de meses anteriores que
quedaron sin confirmar.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import generar_pagos_pendientes, marcar_vencidos


class Command(BaseCommand):
    help = (
        "Autogenera los PagoMensual pendientes del mes actual para cada "
        "alumno activo, y marca como vencidos los pendientes de meses "
        "anteriores."
    )

    def handle(self, *args, **options):
        ahora = timezone.now()
        mes, anio = ahora.month, ahora.year

        creados = generar_pagos_pendientes(mes, anio)
        vencidos = marcar_vencidos(mes, anio)

        self.stdout.write(
            self.style.SUCCESS(
                f"Pagos generados para {mes:02d}/{anio}: {creados}. "
                f"Pagos marcados como vencidos: {vencidos}."
            )
        )
