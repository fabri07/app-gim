"""Form de alta/edición de novedades (comunicados) de un gimnasio.

Incluye `activa` a propósito: la misma pantalla de edición sirve para
"ocultar" o "revelar" una novedad además de corregir su texto. El atajo de
un clic (`NovedadOcultarView`, ver `novedades/views.py`) es un camino más
corto para el caso puntual de "ocultar sin editar nada más", no un
reemplazo de este form.
"""

from django import forms

from core.forms import TenantScopedModelForm
from novedades.models import Novedad


class NovedadForm(TenantScopedModelForm):
    class Meta:
        model = Novedad
        fields = ["titulo", "mensaje", "fecha_publicacion", "visible_hasta", "activa"]
        widgets = {
            "fecha_publicacion": forms.DateInput(attrs={"type": "date"}),
            "visible_hasta": forms.DateInput(attrs={"type": "date"}),
        }
