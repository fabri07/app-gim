"""
Alta de un gimnasio nuevo y de la cuenta staff de su dueño.

Reemplaza al registro público (`/accounts/register/`), que se cerró: permitía
que cualquiera en internet creara User + Gimnasio + Perfil STAFF y quedara
logueado, sin email, sin verificación y sin límite. Con 1-3 gimnasios, darlos
de alta a mano es proporcionado y elimina el abuso de raíz.

    python manage.py crear_gimnasio --nombre "Gimnasio Central" \\
        --email dueno@gmail.com [--slug central] [--password ...] [--sin-password]

El email es además el usuario con el que el dueño va a entrar por Google, así
que tiene que ser su cuenta de Google real.

Mientras el login con Google no exista (Frente C), el comando genera una
contraseña provisoria y la imprime: sin ella un gimnasio recién creado no
podría entrar de ninguna forma. `--sin-password` es el modo definitivo y hoy
solo sirve para probarlo.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tenants.services import crear_gimnasio


class Command(BaseCommand):
    help = "Da de alta un gimnasio y la cuenta staff de su dueño."

    def add_arguments(self, parser):
        parser.add_argument("--nombre", required=True, help="Nombre del gimnasio.")
        parser.add_argument(
            "--email",
            required=True,
            help="Cuenta de Google del dueño. Es su usuario para entrar.",
        )
        parser.add_argument(
            "--slug",
            default=None,
            help="Slug de la landing pública. Si se omite, se deriva del nombre.",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Contraseña provisoria. Si se omite, se genera una al azar.",
        )
        parser.add_argument(
            "--sin-password",
            action="store_true",
            help=(
                "Crea la cuenta sin contraseña usable (solo login con Google). "
                "No usar hasta que el login con Google esté funcionando."
            ),
        )

    def handle(self, *args, **options):
        try:
            gimnasio, usuario, password = crear_gimnasio(
                nombre=options["nombre"],
                email=options["email"],
                slug=options["slug"],
                password=options["password"],
                sin_password=options["sin_password"],
            )
        except ValidationError as exc:
            # `CommandError` sale por stderr con exit code 1, sin traceback:
            # es un error de uso, no un bug.
            raise CommandError("; ".join(exc.messages)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Gimnasio «{gimnasio.nombre}» creado (slug: {gimnasio.slug}). "
                f"Staff: {usuario.username}."
            )
        )
        if password is None:
            self.stdout.write(
                "Sin contraseña usable: solo puede entrar con Google."
            )
        else:
            self.stdout.write(
                f"Contraseña provisoria: {password}\n"
                "Pasásela al dueño por un canal seguro. Es provisoria: cuando "
                "el login con Google esté activo, esta cuenta deja de usar "
                "contraseña."
            )
