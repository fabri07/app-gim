"""Refresca los tokens OAuth de las cuentas de Mercado Pago de las tiendas.

Lo corre `reconciliar-cobros.yml` cada 6 h. El borrado de los tokens de las
cuentas desconectadas hace más de 7 días va ANTES del chequeo de la bandera:
no necesita la clave de cifrado. Con la bandera apagada sale 0 («apagado»),
salvo que exista alguna cuenta CONECTADA: eso es un secret olvidado en el
workflow y los tokens vencerían sin que nadie lo note, así que sale 1."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tienda import cuenta_mp
from tienda.models import CuentaMercadoPago


class Command(BaseCommand):
    help = "Refresca los tokens de las cuentas de Mercado Pago de la tienda."

    def handle(self, *args, **options):
        borradas = cuenta_mp.borrar_tokens_vencidos()
        if borradas:
            self.stdout.write(f"Tokens borrados de cuentas desconectadas: {borradas}.")

        if not settings.TIENDA_MP_ENABLED:
            if CuentaMercadoPago.objects.filter(
                estado=CuentaMercadoPago.Estado.CONECTADA
            ).exists():
                raise CommandError(
                    "Mercado Pago de la tienda está apagado (faltan variables de "
                    "entorno o secrets del workflow) pero hay cuentas conectadas: "
                    "sus tokens vencerían sin refrescarse."
                )
            self.stdout.write("Mercado Pago de la tienda: apagado.")
            return

        r = cuenta_mp.refrescar_cuentas()
        self.stdout.write(self.style.SUCCESS(
            f"Cuentas refrescadas: {r['refrescadas']}. A reconectar: {r['reconectar']}. "
            f"Errores: {r['errores']}."
        ))
