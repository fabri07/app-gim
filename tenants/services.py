"""Lógica de negocio de `tenants`: alta de un gimnasio y su staff.

Vive acá y no en una vista porque el alta dejó de ser self-serve. El registro
público (`/accounts/register/`) se cerró: cualquiera en internet podía crear
User + Gimnasio + Perfil STAFF y quedaba logueado, sin email, sin verificación
y sin límite. Hoy el único camino es `manage.py crear_gimnasio`, que llama a
estas funciones.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from tenants.models import Gimnasio, Perfil


def slug_disponible(nombre):
    """Primer slug libre derivado de `nombre` (`central`, `central-2`, ...).

    Venía de `RegisterView._slug_disponible`. Hay una race condition teórica
    (check-then-create sin lock), pero `Gimnasio.slug` es `unique=True`, así
    que el peor caso es un `IntegrityError`, no dos gimnasios con el mismo
    slug. Con altas manuales y de a una, no amerita un lock.
    """
    base = slugify(nombre) or "gimnasio"
    slug = base
    sufijo = 1
    while Gimnasio.objects.filter(slug=slug).exists():
        sufijo += 1
        slug = f"{base}-{sufijo}"
    return slug


def normalizar_email(email):
    """Devuelve el email en minúsculas y sin espacios, o levanta
    `ValidationError`.

    El lowercase NO es cosmético: `User.objects.get(username=...)` es
    case-sensitive en Postgres, así que sin normalizar `Dueno@X.com` y
    `dueno@x.com` serían dos cuentas distintas y el dueño no podría entrar.
    """
    email = (email or "").strip().lower()
    validate_email(email)
    return email


def generar_password():
    """Contraseña provisoria legible para dictar por teléfono o WhatsApp.

    Alfabeto sin caracteres ambiguos (sin `0/O`, `1/l/I`): quien la recibe la
    va a tipear a mano una sola vez.
    """
    alfabeto = "abcdefghijkmnopqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
    return get_random_string(14, alfabeto)


@transaction.atomic
def crear_gimnasio(nombre, email, slug=None, password=None, sin_password=False):
    """Da de alta un gimnasio y la cuenta staff de su dueño.

    Devuelve `(gimnasio, usuario, password)`. `password` es `None` cuando la
    cuenta quedó sin contraseña usable.

    **Login con Google (Frente C) ya está verificado contra producción
    (2026-08-19/20)** — `sin_password=True` es seguro para un gimnasio real,
    no solo para pruebas: el dueño entra directo con su cuenta de Google
    (tiene que ser el email real). `set_unusable_password()` además la deja
    automáticamente fuera del reset por mail, porque
    `PasswordResetForm.get_users()` filtra por `has_usable_password()`. El
    default sigue siendo generar una contraseña provisoria (`sin_password`
    es opt-in, no default) — decisión explícita del dueño del producto, no
    una limitación técnica pendiente.

    Atómico: si algo falla no queda ni un gimnasio huérfano ni un usuario sin
    perfil.
    """
    email = normalizar_email(email)

    User = get_user_model()
    if User.objects.filter(username=email).exists():
        raise ValidationError(f"Ya existe un usuario con el email {email}.")

    usuario = User(username=email, email=email)
    if sin_password:
        password = None
        usuario.set_unusable_password()
    else:
        password = password or generar_password()
        # Las contraseñas provisorias generadas son largas y aleatorias, pero
        # una pasada con `--password` puede ser cualquier cosa: se validan las
        # dos con los mismos validadores que el resto del proyecto.
        validate_password(password, user=usuario)
        usuario.set_password(password)

    gimnasio = Gimnasio.objects.create(
        nombre=nombre,
        slug=slug or slug_disponible(nombre),
    )
    usuario.save()
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=Perfil.Rol.STAFF)

    # Import tardío: `tenants` no tiene por qué conocer el modelo de
    # categorías, y así se evita el ciclo con `ejercicios` (que sí importa
    # `tenants.Gimnasio` vía `TenantOwnedModel`). Mismo criterio que
    # `HomeView._metricas_dashboard`.
    from ejercicios.services import sembrar_categorias_iniciales

    sembrar_categorias_iniciales(gimnasio)

    return gimnasio, usuario, password
