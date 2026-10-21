"""Acredita los pagos de cuotas por Mercado Pago cuyo webhook se perdió.

Lo corre `reconciliar-cobros.yml` cada 6 h (`pagos.cobro_mp.reconciliar_cuotas`).
Misma regla de salida que `reconciliar_pedidos`: con la bandera apagada sale 0
(«apagado»), salvo que exista alguna cuenta de Mercado Pago CONECTADA: eso es
un secret olvidado en el workflow (los pagos perdidos de las cuotas no se
acreditarían nunca), así que sale 1 (workflow en rojo, sin ping)."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pagos import cobro_mp


class Command(BaseCommand):
    help = "Acredita los pagos de cuotas por Mercado Pago que el webhook no trajo."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)

    def handle(self, *args, **options):
        # `tienda` carga después que `pagos`: import tardío.
        from tienda.models import CuentaMercadoPago

        if not settings.TIENDA_MP_ENABLED:
            if CuentaMercadoPago.objects.filter(
                estado=CuentaMercadoPago.Estado.CONECTADA
            ).exists():
                raise CommandError(
                    "Mercado Pago está apagado (faltan variables de entorno o "
                    "secrets del workflow) pero hay cuentas conectadas: los pagos "
                    "perdidos de las cuotas no se acreditarían."
                )
            self.stdout.write("Cuotas por Mercado Pago: apagado.")
            return

        r = cobro_mp.reconciliar_cuotas(limite=options["limite"])
        self.stdout.write(self.style.SUCCESS(
            f"Cobros de cuotas revisados: {r['revisados']}. Acreditados: {r['acreditados']}. "
            f"Vencidos: {r['vencidos']}. Salteados: {r['salteados']}. Errores: {r['errores']}."
        ))
