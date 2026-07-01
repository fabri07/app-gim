"""
Scoping de tenant en la capa de vista (no en el modelo).

Decisión (ver CLAUDE.md): el aislamiento automático vive acá, en un mixin de
vista reutilizable, NO en un manager con thread-local + middleware (que sería
implícito y acoplaría el modelo al request). El filtro real sigue siendo
`TenantQuerySet.for_gimnasio`; este mixin lo aplica una sola vez para que
ningún desarrollador tenga que reescribirlo —ni pueda olvidarlo— por vista.

`core` no importa `tenants`: para detectar "usuario sin Perfil" se captura
`ObjectDoesNotExist` (base de la excepción del reverse accessor `user.perfil`),
preservando el orden de dependencias core -> tenants -> dominio.

Adaptado de ~/gestor-pedidos/core/mixins.py (negocio -> gimnasio).
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied


class TenantScopedMixin(LoginRequiredMixin):
    @property
    def gimnasio(self):
        """Gimnasio del usuario autenticado. 403 si no tiene Perfil.

        El panel operativo exige Perfil; un usuario sin Perfil (p.ej. un
        superuser creado por consola) usa el admin, no este panel.
        """
        try:
            return self.request.user.perfil.gimnasio
        except ObjectDoesNotExist:
            raise PermissionDenied(
                "Tu usuario no tiene un Perfil asociado a un Gimnasio."
            )

    def get_queryset(self):
        return super().get_queryset().for_gimnasio(self.gimnasio)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["gimnasio"] = self.gimnasio
        return kwargs

    def form_valid(self, form):
        # `gimnasio` se stampa del lado del servidor: jamás es un campo del
        # form controlado por el cliente.
        form.instance.gimnasio = self.gimnasio
        return super().form_valid(form)
