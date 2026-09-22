"""Lo que `base.html` necesita saber del estado de la cuenta.

Va como context processor y no como mixin por el mismo motivo que el
middleware: el aviso vive en `base.html`, o sea en TODA página del sitio, y
una vista que se olvide de agregarlo al contexto dejaría al dueño sin enterarse
de que sus alumnos están bloqueados.
"""

from django.conf import settings

from plataforma.middleware import perfil_de
from tenants.models import Gimnasio


def estado_cuenta(request):
    """`cuenta_alumnos_bloqueados`, `cuenta_suspendida` y `SOPORTE_CONTACTO`.

    Los dos primeros describen la CUENTA, no a quién se le muestra qué:
    `base.html` decide que el banner ámbar lo vea el staff (es el que puede
    pagar) y que la nav desaparezca con la cuenta suspendida.

    Corta temprano en `/admin/` -- mismo criterio que
    `tenants.context_processors.tour_onboarding_disponible`: el admin usa el
    mismo motor de templates pero no extiende `base.html`, así que la consulta
    del `Perfil` ahí sería una query de más contra Neon en cada pantalla.
    """
    if request.resolver_match and request.resolver_match.app_name == "admin":
        return {}

    # El superadmin no se bloquea nunca (ver el middleware), así que tampoco
    # se le esconde la nav ni se le muestra un banner sobre una cuenta que
    # para él no está congelada: las dos pantallas tienen que decir lo mismo.
    usuario = getattr(request, "user", None)
    perfil = None if usuario is not None and usuario.is_superuser else perfil_de(request)
    estado = perfil.gimnasio.estado_cuenta if perfil is not None else None
    return {
        "cuenta_alumnos_bloqueados": (
            estado == Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS
        ),
        "cuenta_suspendida": estado == Gimnasio.EstadoCuenta.SUSPENDIDA,
        "SOPORTE_CONTACTO": settings.SOPORTE_CONTACTO,
    }
