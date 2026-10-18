"""Acredita los pagos de Mercado Pago de la tienda cuyo webhook se perdió.

Lo corre `reconciliar-cobros.yml` cada 6 h (`tienda.pagos_mp.reconciliar_pedidos`).
Misma regla de salida que `refrescar_cuentas_mp`: con la bandera apagada sale
0 («apagado»), salvo que exista alguna cuenta CONECTADA: eso es un secret
olvidado en el workflow (los pagos perdidos no se acreditarían nunca), así que
sale 1 (workflow en rojo, sin ping)."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tienda import pagos_mp
from tienda.models import CuentaMercadoPago


class Command(BaseCommand):
    help = "Acredita los pagos de Mercado Pago de la tienda que el webhook no trajo."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)

    def handle(self, *args, **options):
        if not settings.TIENDA_MP_ENABLED:
            if CuentaMercadoPago.objects.filter(
                estado=CuentaMercadoPago.Estado.CONECTADA
            ).exists():
                raise CommandError(
                    "Mercado Pago de la tienda está apagado (faltan variables de "
                    "entorno o secrets del workflow) pero hay cuentas conectadas: "
                    "sus pagos perdidos no se acreditarían."
                )
            self.stdout.write("Mercado Pago de la tienda: apagado.")
            return

        r = pagos_mp.reconciliar_pedidos(limite=options["limite"])
        self.stdout.write(self.style.SUCCESS(
            f"Pedidos revisados: {r['revisados']}. Errores: {r['errores']}."
        ))
