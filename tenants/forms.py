"""Forms de `tenants`: personalización white-label (Fase 4) de un gimnasio ya
existente.

El form de registro se borró junto con `RegisterView`: el alta de gimnasios
dejó de ser self-serve y se hace con `manage.py crear_gimnasio`.
"""

from django import forms

from tenants.models import Gimnasio


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
            "paleta",
            "tipografia",
            "texto_bienvenida",
            "contacto",
            "link_instagram",
            "link_whatsapp",
        ]
