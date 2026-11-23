"""
Command de management que dispara la autogeneración mensual de pagos.

Lo programa `.github/workflows/generar-pagos.yml` (GitHub Actions, no Render:
no hay plan free para cron jobs ahí). Emite las cuotas del ciclo vigente de
cada alumno (y la del siguiente, si ya está a la vista) y vence las que se
pasaron de plazo. Es idempotente: correrlo dos veces no duplica nada.

Después aplica los descuentos de premios ya otorgados a las cuotas que
correspondan (`reconocimientos.premios.aplicar_pendientes`). Va con import
tardío (`pagos` no depende de `reconocimientos` a nivel módulo) y dentro de un
`try`: si los premios fallan, la emisión de cuotas ya está hecha y el cron no
se cae (queda en el log).
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import generar_pagos_pendientes, marcar_vencidos

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Emite las cuotas pendientes del ciclo vigente de cada alumno activo "
        "y marca como vencidas las que se pasaron del plazo de pago."
    )

    def handle(self, *args, **options):
        # `localdate()` y NO `now().date()`: `now()` es UTC y `TIME_ZONE` es
        # `America/Argentina/Buenos_Aires`, así que entre las 21:00 y las
        # 23:59 la fecha UTC ya es la de mañana. Corrido a mano el último día
        # del mes por la noche, esto emitía las cuotas del mes SIGUIENTE con
        # `dia=1`. La corrida agendada (06:30 UTC = 03:30 local) cae fuera de
        # esa ventana, pero la corrida manual es justo la que se hace cuando
        # algo ya salió mal.
        hoy = timezone.localdate()

        creados = generar_pagos_pendientes(hoy)
        vencidos = marcar_vencidos(hoy)

        premiadas = 0
        try:
            from reconocimientos.premios import aplicar_pendientes

            premiadas = aplicar_pendientes(hoy)
        except Exception:
            logger.exception("Falló la aplicación de descuentos de premios.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Cuotas emitidas al {hoy:%d/%m/%Y}: {creados}. "
                f"Cuotas marcadas como vencidas: {vencidos}."
            )
        )
        if premiadas:
            self.stdout.write(
                self.style.SUCCESS(f"Descuentos de premios aplicados: {premiadas}.")
            )
