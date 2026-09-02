"""Llena un gimnasio de PRUEBA con datos verosímiles.

    python manage.py sembrar_demo --gimnasio verificacion-r2
    python manage.py sembrar_demo --gimnasio verificacion-r2 --borrar

Para qué: una cuenta vacía no muestra nada de la app. Los gráficos del panel,
la tarjeta de planes por vencer y los botones de eliminar necesitan datos para
existir en pantalla; sin ellos, una captura para promocionar el producto se ve
como un formulario en blanco.

**Guarda contra el desastre:** se niega a correr sobre un gimnasio que ya
tiene alumnos que NO son de demo, salvo `--confirmar`. Es lo único que separa
"lleno la cuenta de prueba" de "le meto 24 alumnos falsos al gimnasio de un
cliente que paga".
"""

from django.core.management.base import BaseCommand, CommandError

from notificaciones import services as notificaciones
from tenants.demo import MARCA, borrar_demo, sembrar_demo
from tenants.models import Gimnasio


class Command(BaseCommand):
    help = "Siembra datos de demostración en un gimnasio de prueba."

    def add_arguments(self, parser):
        parser.add_argument(
            "--gimnasio", required=True,
            help="Slug del gimnasio destino. Obligatorio: no hay default para "
                 "que nunca se ejecute sobre el gimnasio equivocado.",
        )
        parser.add_argument("--alumnos", type=int, default=24)
        parser.add_argument(
            "--meses", type=int, default=6,
            help="Cuántos meses de historial de cuotas generar.",
        )
        parser.add_argument(
            "--borrar", action="store_true",
            help="Saca los datos de demo (y solo esos) en vez de sembrar.",
        )
        parser.add_argument(
            "--confirmar", action="store_true",
            help="Sembrar aunque el gimnasio ya tenga alumnos reales.",
        )

    def handle(self, *args, **opciones):
        from alumnos.models import Alumno

        try:
            gimnasio = Gimnasio.objects.get(slug=opciones["gimnasio"])
        except Gimnasio.DoesNotExist:
            existentes = ", ".join(
                Gimnasio.objects.values_list("slug", flat=True).order_by("slug")
            )
            raise CommandError(
                f"No existe un gimnasio con slug «{opciones['gimnasio']}». "
                f"Los que hay: {existentes or 'ninguno'}."
            )

        if opciones["borrar"]:
            borrados = borrar_demo(gimnasio=gimnasio)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Borrados {borrados} alumnos de demo (y sus pagos, rutinas "
                    f"y reservas) de «{gimnasio.nombre}»."
                )
            )
            return

        reales = (
            Alumno.objects.for_gimnasio(gimnasio).exclude(observaciones=MARCA).count()
        )
        if reales and not opciones["confirmar"]:
            raise CommandError(
                f"«{gimnasio.nombre}» ya tiene {reales} alumnos que NO son de "
                f"demo. Esto parece un gimnasio real, no una cuenta de prueba.\n"
                f"Si de verdad querés sembrar datos falsos acá, repetí el "
                f"comando con --confirmar."
            )

        self.stdout.write(f"Sembrando «{gimnasio.nombre}» ({gimnasio.slug})...")
        # `silenciado()` envuelve la transacción ENTERA a propósito: los
        # signals de `notificaciones` mandan el push desde un
        # `transaction.on_commit`, o sea al cerrarse el `atomic()` de
        # `sembrar_demo`, que pasa acá adentro. Sin esto, sembrar 300+
        # reservas le dispara 300+ notificaciones al celular del staff.
        with notificaciones.silenciado():
            resumen = sembrar_demo(
                gimnasio=gimnasio,
                cantidad_alumnos=opciones["alumnos"],
                meses=opciones["meses"],
            )
        for clave, valor in resumen.items():
            self.stdout.write(f"  {clave}: {valor}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. Entrá a «{gimnasio.nombre}» y mirá el panel de inicio.\n"
                f"Para deshacerlo: manage.py sembrar_demo --gimnasio "
                f"{gimnasio.slug} --borrar"
            )
        )
