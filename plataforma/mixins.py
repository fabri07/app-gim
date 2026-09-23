"""
Autorización del panel de la plataforma.

Simétrico a `tenants.mixins.StaffRequiredMixin`, pero un escalón más arriba:
`staff` es el rol más alto DENTRO de un gimnasio, y este panel es del dueño
del producto. Un dueño de gimnasio no tiene por qué ver la facturación ni los
datos de los otros gimnasios, así que recibe 403 igual que un alumno.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class SuperadminRequiredMixin(LoginRequiredMixin):
    """403 para todo el que no sea un superusuario activo.

    El chequeo de `is_active` es defensa en profundidad: `ModelBackend.
    get_user()` ya revalida `is_active` en cada request, así que un usuario
    desactivado no llega acá autenticado. Igual se chequea, porque el costo es
    cero y el día que aparezca otro backend de autenticación este panel no
    puede ser el que se entere tarde.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (
            request.user.is_superuser and request.user.is_active
        ):
            raise PermissionDenied("El panel de plataforma es solo para el superadmin.")
        return super().dispatch(request, *args, **kwargs)
