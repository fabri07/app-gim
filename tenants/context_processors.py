from django.conf import settings
from django.utils import timezone

from tenants.models import Perfil


def google_staff_login_disponible(request):
    return {"GOOGLE_STAFF_LOGIN_DISPONIBLE": settings.GOOGLE_STAFF_LOGIN_ENABLED}


def password_reset_disponible(request):
    return {"PASSWORD_RESET_DISPONIBLE": settings.PASSWORD_RESET_ENABLED}


def tour_onboarding_disponible(request):
    """Habilita el tour de bienvenida solo para staff cuyo Perfil se creó
    después de TOUR_ONBOARDING_DESDE -- ver settings.py. Durante una
    suplantación `request.user` es el alumno suplantado (rol ALUMNO), así
    que ya queda excluido por el chequeo de rol sin necesidad de un guard
    aparte.
    """
    # /admin/ usa el mismo motor de templates (y por lo tanto corre este
    # context processor en cada request), pero nunca renderiza el tour --
    # `app_name` es "admin" siempre para `admin.site.urls`, así que evita la
    # query de `perfil` de más contra Neon (scale-to-zero) sin acoplarse al
    # path fijo "/admin/".
    if request.resolver_match and request.resolver_match.app_name == "admin":
        return {"tour_onboarding_habilitado": False}
    perfil = getattr(request.user, "perfil", None)
    habilitado = (
        perfil is not None
        and perfil.rol == Perfil.Rol.STAFF
        # .date() sin convertir daría la fecha en UTC, no la local (project
        # TIME_ZONE = America/Argentina/Buenos_Aires): un Perfil creado
        # entre las 21:00 y 23:59 locales cae en el día siguiente en UTC.
        and timezone.localtime(perfil.creado).date() >= settings.TOUR_ONBOARDING_DESDE
    )
    return {"tour_onboarding_habilitado": habilitado}
