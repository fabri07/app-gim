from django.core.management.base import BaseCommand

from solicitudes.services import expirar_vencidas


class Command(BaseCommand):
    help = "Marca EXPIRADA cada solicitud NO_VERIFICADA cuyo link de verificación ya venció."

    def handle(self, *args, **options):
        n = expirar_vencidas()
        self.stdout.write(self.style.SUCCESS(f"Solicitudes expiradas: {n}"))
