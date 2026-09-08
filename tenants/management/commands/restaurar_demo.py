"""Deja la cuenta de demostración compartida como recién creada.

    python manage.py restaurar_demo --gimnasio demo

Para qué: esa cuenta se le pasa a varios dueños de gimnasio a la vez para que
prueben la app. Cualquiera de ellos puede borrar los alumnos, vaciar la
biblioteca de ejercicios o renombrar el gimnasio -- y el siguiente prospecto
entra a una pantalla en blanco, que es justo lo contrario de lo que una demo
tiene que mostrar. Lo corre un cron cada 6 horas
(`.github/workflows/restaurar-demo.yml`).

Comando aparte de `sembrar_demo`, y no un `--restaurar` suyo, porque el guard
es el opuesto: `sembrar_demo` se protege de escribir en un gimnasio que parece
real (y se puede forzar con `--confirmar`), mientras que esto BORRA todo y por
lo tanto exige `Gimnasio.es_demo`, sin escape posible. Mezclar las dos
políticas en un solo comando es cómo se termina con una bandera que saltea la
que no era.
"""

from django.core.management.base import BaseCommand, CommandError

from tenants.demo import PASSWORD_DEMO, restaurar_demo
from tenants.models import Gimnasio


class Command(BaseCommand):
    help = "Vacía y vuelve a sembrar la cuenta de demostración compartida."

    def add_arguments(self, parser):
        parser.add_argument(
            "--gimnasio",
            required=True,
            help="Slug de la cuenta de demostración. Obligatorio y sin default: "
            "un default acá es un gimnasio vaciado por tipear de menos.",
        )
        parser.add_argument("--alumnos", type=int, default=24)
        parser.add_argument("--meses", type=int, default=6)

    def handle(self, *args, **opciones):
        slug = opciones["gimnasio"]
        try:
            gimnasio = Gimnasio.objects.get(slug=slug)
        except Gimnasio.DoesNotExist:
            raise CommandError(
                f"No existe ningún gimnasio con slug «{slug}». Si es la cuenta "
                f"de demostración y todavía no la creaste:\n"
                f"  manage.py crear_gimnasio --nombre 'Gimnasio Demo' "
                f"--email <email> --slug {slug} --demo"
            )

        # El guard de verdad vive en `vaciar_gimnasio`, para que ningún
        # llamador lo pueda saltear. Se repite acá sólo para dar un error de
        # uso legible (stderr, exit 1, sin traceback) en vez de un ValueError.
        if not gimnasio.es_demo:
            raise CommandError(
                f"«{gimnasio.nombre}» ({gimnasio.slug}) NO está marcado como "
                f"cuenta de demostración. Este comando borra todos los alumnos, "
                f"rutinas, cobros y turnos del gimnasio: si de verdad es una "
                f"cuenta de prueba, marcá es_demo desde /admin/."
            )

        self.stdout.write(f"Restaurando «{gimnasio.nombre}» ({gimnasio.slug})...")
        # `restaurar_demo` ya envuelve todo en `notificaciones.silenciado()`:
        # sin eso, cada reserva sembrada le manda un push al celular de quien
        # tenga la cuenta abierta (medido: 71 con sólo 4 alumnos).
        resumen = restaurar_demo(
            gimnasio=gimnasio,
            cantidad_alumnos=opciones["alumnos"],
            meses=opciones["meses"],
        )
        for clave, valor in resumen.items():
            self.stdout.write(f"  {clave}: {valor}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. Contraseña de los alumnos de demo: {PASSWORD_DEMO}"
            )
        )
