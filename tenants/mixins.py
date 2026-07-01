"""
Autorización por rol en la capa de vista.

Separado de `core.mixins.TenantScopedMixin` a propósito: ese mixin resuelve
AISLAMIENTO de tenant (qué gimnasio), esto resuelve AUTORIZACIÓN por rol
(quién puede entrar). Son responsabilidades distintas (SOLID) y Fase 3 va a
necesitar la primera para vistas de alumno SIN esta segunda (un alumno también
está scopeado a su gimnasio, pero no debe pasar por `StaffRequiredMixin`).

Vive en `tenants` (no en `core`) porque necesita conocer `Perfil.Rol`, y
`core` no importa `tenants` (ver `core/mixins.py`).
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from tenants.models import Perfil


class StaffRequiredMixin(LoginRequiredMixin):
    """Todas las vistas de gestión de Fase 2 son solo para `staff`. Un
    usuario logueado sin Perfil, o con Perfil de `alumno`, recibe 403 — el
    portal del alumno (Fase 3) son vistas completamente distintas, no una
    variante con permisos reducidos de estas."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                perfil = request.user.perfil
            except ObjectDoesNotExist:
                raise PermissionDenied(
                    "Tu usuario no tiene un Perfil asociado a un Gimnasio."
                )
            if perfil.rol != Perfil.Rol.STAFF:
                raise PermissionDenied("Esta sección es solo para staff.")
        return super().dispatch(request, *args, **kwargs)
