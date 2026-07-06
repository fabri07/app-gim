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


class AlumnoRequiredMixin(LoginRequiredMixin):
    """Simétrico a StaffRequiredMixin: 403 si no hay Perfil o el rol no es ALUMNO.

    A diferencia de StaffRequiredMixin, expone `self.alumno` (puede ser None si el
    Perfil de rol alumno todavía no está vinculado a una ficha de Alumno) para que las
    vistas GET puedan renderizar un estado vacío en vez de un 403.

    Reglas de uso (documentarlas en vistas que la hereden):
    - Vistas GET pueden mostrar un estado vacío cuando `self.alumno is None`.
    - Vistas POST de escritura deben hacer `if self.alumno is None: raise PermissionDenied(...)`.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                self.perfil = request.user.perfil
            except ObjectDoesNotExist:
                raise PermissionDenied(
                    "Tu usuario no tiene un Perfil asociado a un Gimnasio."
                )
            if self.perfil.rol != Perfil.Rol.ALUMNO:
                raise PermissionDenied("Esta sección es solo para alumnos.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def gimnasio(self):
        return self.perfil.gimnasio

    @property
    def alumno(self):
        try:
            return self.perfil.alumno
        except ObjectDoesNotExist:
            return None
