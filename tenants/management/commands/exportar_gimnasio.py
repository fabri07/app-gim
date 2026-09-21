"""Exporta todos los datos de un gimnasio a un ZIP de CSV, desde la Shell.

    python manage.py exportar_gimnasio --gimnasio vida-plena --salida datos.zip

Es el mismo archivo que baja el botón «Exportar mis datos» de "Mi gimnasio"
(`tenants/exportacion.py`). Existe para el gimnasio cuyo historial sea tan
grande que la descarga web roce los 30 s de timeout de gunicorn: acá no hay
timeout, y no ocupa el único worker que atiende a todos los demás.

No mira `Gimnasio.puede_exportar` a propósito: esa casilla decide si el STAFF
del gimnasio puede bajarse los datos solo. Quien tiene Shell ya tiene la base.
"""

from django.core.management.base import BaseCommand, CommandError

from tenants.exportacion import exportar_gimnasio
from tenants.models import Gimnasio


class Command(BaseCommand):
    help = "Exporta todos los datos de un gimnasio a un ZIP de CSV."

    def add_arguments(self, parser):
        parser.add_argument("--gimnasio", required=True, help="Slug del gimnasio.")
        parser.add_argument(
            "--salida", required=True, help="Ruta del .zip a escribir."
        )

    def handle(self, *args, **options):
        try:
            gimnasio = Gimnasio.objects.get(slug=options["gimnasio"])
        except Gimnasio.DoesNotExist:
            raise CommandError(f"No existe un gimnasio con slug «{options['gimnasio']}».")

        with open(options["salida"], "wb") as destino:
            conteos = exportar_gimnasio(gimnasio=gimnasio, destino=destino)

        for nombre, cantidad in conteos.items():
            self.stdout.write(f"  {nombre}: {cantidad}")
        self.stdout.write(
            self.style.SUCCESS(f"{gimnasio.nombre} exportado a {options['salida']}")
        )
