from django.core.management.base import BaseCommand, CommandError

from suscripcion import avisos, services


class Command(BaseCommand):
    help = (
        "Avisa por mail al staff de cada gimnasio cuya suscripción está por "
        "vencer, vence hoy o ya venció (un aviso por período y tipo)."
    )

    def handle(self, *args, **options):
        # El mail invita a pagar online: sin integración no hay dónde. Salir
        # con error (workflow en rojo, sin ping) y no en silencio.
        if not services.integracion_activa():
            raise CommandError(
                "El cobro online no está configurado (faltan MERCADOPAGO_* en el entorno)."
            )
        n = avisos.avisar()
        self.stdout.write(self.style.SUCCESS(f"Avisos de cobro enviados: {n}"))
