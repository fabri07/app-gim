"""Form de alta/edición de ejercicios de la biblioteca de un gimnasio.

Hereda de `TenantScopedModelForm` (aunque `Ejercicio` no tiene FKs
tenant-owned que necesiten acotarse) para mantener el mismo contrato en
todos los forms de Fase 2 -- recibe `gimnasio` por `get_form_kwargs` del
mixin de vista y no rompe si en el futuro se agrega un campo FK.
"""

from core.forms import TenantScopedModelForm
from ejercicios.models import Ejercicio


class EjercicioForm(TenantScopedModelForm):
    class Meta:
        model = Ejercicio
        fields = ["nombre", "grupo_muscular", "descripcion", "url_video", "activo"]
