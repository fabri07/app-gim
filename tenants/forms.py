"""Forms de `tenants`: registro (alta de un gimnasio) y personalización
white-label (Fase 4) de un gimnasio ya existente."""

from django import forms
from django.contrib.auth.forms import UserCreationForm

from tenants.models import Gimnasio


class RegistroForm(UserCreationForm):
    nombre_gimnasio = forms.CharField(max_length=120, label="Nombre del gimnasio")

    class Meta(UserCreationForm.Meta):
        fields = ("username",)  # password1/2 los aporta UserCreationForm


class GimnasioForm(forms.ModelForm):
    """Personalización del gimnasio (Fase 4, "Personalización por
    gimnasio"). No es `TenantScopedModelForm`: `Gimnasio` ES el tenant, no
    tiene FK a otro `TenantOwnedModel` para acotar. `slug` y `activo` quedan
    afuera a propósito -- son de gestión de la plataforma, no algo que el
    dueño edite desde su propio panel; siguen disponibles en `/admin/`.
    """

    class Meta:
        model = Gimnasio
        fields = [
            "nombre",
            "logo",
            "color_primario",
            "color_secundario",
            "texto_bienvenida",
            "contacto",
            "link_instagram",
            "link_whatsapp",
        ]
        widgets = {
            "color_primario": forms.TextInput(attrs={"type": "color"}),
            "color_secundario": forms.TextInput(attrs={"type": "color"}),
        }
