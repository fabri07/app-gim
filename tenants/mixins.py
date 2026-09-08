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


class BloqueadoEnCuentaDemoMixin:
    """403 si el gimnasio del usuario es la cuenta de demostración compartida.

    La cuenta demo se le pasa a varios dueños de gimnasio a la vez. Si uno
    cambia la contraseña, `update_session_auth_hash` salva SU sesión y deja
    afuera a todos los demás, que ya no tienen la clave nueva: un click de un
    desconocido rompe la demo para el resto.

    Va como mixin y no como un `dispatch` copiado en cada vista porque son dos
    las vistas a proteger (el form y su pantalla de confirmación), y duplicar
    un guard es cómo se termina con una de las dos sin él -- el mismo criterio
    que llevó `sincronizar_acceso_con_estado` a una señal en vez de a las tres
    vistas que escriben `Alumno.estado`.

    NO hereda de `LoginRequiredMixin`: se combina con `StaffRequiredMixin`, que
    ya lo trae, y va DESPUÉS de él en la lista de bases para que el anónimo sea
    redirigido y el rol validado antes de que acá se toque `perfil.gimnasio`.
    Igual se chequea `is_authenticated` y se atrapa `ObjectDoesNotExist`, para
    que el mixin sea correcto por sí solo si alguien lo usa en otro orden.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                gimnasio = request.user.perfil.gimnasio
            except ObjectDoesNotExist:
                gimnasio = None
            if gimnasio is not None and gimnasio.es_demo:
                raise PermissionDenied(
                    "La cuenta de demostración no puede cambiar su contraseña: "
                    "la comparten varias personas a la vez."
                )
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
