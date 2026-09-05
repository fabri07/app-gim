"""
Form-base que cierra el hueco de FK-injection.

Stampar `gimnasio` en el objeto nuevo no alcanza: un form con una FK a otra
entidad tenant-owned (p.ej. `RutinaAsignada.alumno`, `Cuota.alumno`)
permitiría hacer POST con el id de un alumno de otro gimnasio. Este form-base
acota AUTOMÁTICAMENTE el queryset de todo campo FK tenant-owned al `gimnasio`
recibido. Se escribe una sola vez; toda la lógica de filtrado vive acá (DRY)
y es imposible olvidarla.

Adaptado de ~/gestor-pedidos/core/forms.py (negocio -> gimnasio).
"""

from django import forms

from core.models import TenantOwnedModel


class TenantScopedModelForm(forms.ModelForm):
    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.gimnasio = gimnasio
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)  # solo ModelChoice*Field
            model = getattr(qs, "model", None)     # modelo concreto del queryset
            if model is not None and issubclass(model, TenantOwnedModel):
                field.queryset = qs.for_gimnasio(gimnasio)
