"""Formulario público de solicitud de acceso.

`ModelForm` sobre `SolicitudAcceso` con dos campos EXTRA que no son del modelo y
que maneja la vista (nunca se guardan): `website` (honeypot) y `tiempo` (token
firmado para medir cuánto tardó el envío, ver `antispam`).
"""

from django import forms

from solicitudes.antispam import token_de_tiempo
from solicitudes.models import SolicitudAcceso

#: Campos del modelo que expone el form (en orden de aparición en la pantalla).
CAMPOS_MODELO = [
    "nombre_gimnasio",
    "nombre_contacto",
    "email",
    "telefono",
    "cantidad_alumnos",
    "anios_operando",
    "tamano_staff",
    "banda_ingresos",
    "formato_registros",
    "profundidad_historia",
    "puede_compartir_archivos",
    "comentario",
]


class SolicitarAccesoForm(forms.ModelForm):
    # Honeypot: un humano no ve este campo (se oculta por CSS) ni lo completa.
    # Se llama `website` a secas para tentar el autofill de los bots.
    website = forms.CharField(
        required=False,
        label="No completar este campo",
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )
    # Token firmado con la hora de render: la vista rechaza envíos demasiado
    # rápidos o con el token adulterado/vencido.
    tiempo = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = SolicitudAcceso
        fields = CAMPOS_MODELO
        widgets = {"comentario": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["tiempo"].initial = token_de_tiempo()
        # nombre_gimnasio, nombre_contacto y email ya son obligatorios por el
        # modelo; el resto queda opcional (screening blank=True).

    def datos_de_modelo(self):
        """Dict con SOLO los campos del modelo, para pasarle al service (que
        hace `SolicitudAcceso.objects.create(**datos)`). Deja afuera honeypot y
        tiempo."""
        return {campo: self.cleaned_data[campo] for campo in CAMPOS_MODELO}
