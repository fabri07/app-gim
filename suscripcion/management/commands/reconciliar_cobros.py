from django.core.management.base import BaseCommand, CommandError

from suscripcion import services


class Command(BaseCommand):
    help = (
        "Busca en Mercado Pago los pagos de suscripción recientes que todavía "
        "no se acreditaron (webhook perdido, pestaña cerrada) y los acredita."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Máximo de intentos a consultar en esta corrida (default 50).",
        )

    def handle(self, *args, **options):
        # Sin la integración, salir con error a propósito: un cron que no hace
        # nada en silencio porque faltan los secrets es indistinguible del
        # estado sano, y así el workflow queda en rojo y no pinguea.
        if not services.integracion_activa():
            raise CommandError(
                "El cobro online no está configurado (faltan MERCADOPAGO_* en el entorno)."
            )
        try:
            n = services.reconciliar_pendientes(limite=options["limite"])
        except services.CobroNoDisponible as exc:
            raise CommandError(f"El cobro online no está disponible: {exc.motivo}") from exc
        self.stdout.write(self.style.SUCCESS(f"Cobros acreditados: {n}"))
