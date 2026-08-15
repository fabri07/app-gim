"""Forms de `tenants`: personalización white-label (Fase 4) de un gimnasio ya
existente.

El form de registro se borró junto con `RegisterView`: el alta de gimnasios
dejó de ser self-serve y se hace con `manage.py crear_gimnasio`.
"""

from django import forms
from django.core.files.uploadedfile import UploadedFile
from PIL import Image

from tenants.models import Gimnasio

_FONDO_IMAGEN_TAMANIO_MAXIMO = 5 * 1024 * 1024
_FONDO_IMAGEN_ANCHO_MINIMO = 1280
_FONDO_IMAGEN_ALTO_MINIMO = 720
_FONDO_IMAGEN_FORMATOS_VALIDOS = {"JPEG", "PNG"}


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
            "fondo_tipo",
            "fondo_imagen",
            "fondo_doodle",
            "texto_bienvenida",
            "contacto",
            "link_instagram",
            "link_whatsapp",
        ]
        widgets = {
            "fondo_tipo": forms.RadioSelect,
            "fondo_doodle": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # fondo_doodle es blank=True en el modelo -> Django agrega un choice
        # vacío ("---------") al formfield por defecto. Para una grilla de
        # exactamente 4 miniaturas no queremos un 5to radio "ninguno" -- la
        # ausencia de selección ya se resuelve con required=False (heredado
        # de blank=True) más la validación cruzada en clean().
        self.fields["fondo_doodle"].choices = Gimnasio.Doodle.choices

    def clean_fondo_imagen(self):
        archivo = self.cleaned_data.get("fondo_imagen")
        if not archivo or not isinstance(archivo, UploadedFile):
            # No hay archivo nuevo en este request: es el valor ya guardado
            # (o ninguno) -- ya pasó esta validación cuando se subió.
            return archivo
        if archivo.size > _FONDO_IMAGEN_TAMANIO_MAXIMO:
            raise forms.ValidationError("La imagen no puede pesar más de 5 MB.")
        try:
            imagen = Image.open(archivo)
            ancho, alto = imagen.size
            formato = imagen.format
        except Exception:
            raise forms.ValidationError("El archivo no es una imagen válida.")
        if formato not in _FONDO_IMAGEN_FORMATOS_VALIDOS:
            raise forms.ValidationError("Solo se aceptan imágenes JPEG o PNG.")
        if ancho < _FONDO_IMAGEN_ANCHO_MINIMO or alto < _FONDO_IMAGEN_ALTO_MINIMO:
            raise forms.ValidationError(
                f"La imagen debe medir al menos "
                f"{_FONDO_IMAGEN_ANCHO_MINIMO}×{_FONDO_IMAGEN_ALTO_MINIMO}px."
            )
        # PIL consumió el puntero al leer -- el form todavía necesita el
        # archivo completo para guardarlo en el storage.
        archivo.seek(0)
        return archivo

    def clean(self):
        cleaned_data = super().clean()
        fondo_tipo = cleaned_data.get("fondo_tipo")
        if fondo_tipo == Gimnasio.FondoTipo.DOODLE and not cleaned_data.get(
            "fondo_doodle"
        ):
            self.add_error("fondo_doodle", "Elegí un doodle para este modo de fondo.")
        # "fondo_imagen" not in self.errors: si clean_fondo_imagen() ya rechazó
        # el archivo (pesa de más, formato, resolución), Django lo sacó de
        # cleaned_data -- sin este guard sumaríamos un segundo error que dice
        # "subí una imagen" a alguien que SÍ subió una. La plantilla muestra
        # errors.0 y taparía el engaño, pero form.errors completo (lector de
        # pantalla, un {{ form.errors }} futuro) vería el mensaje contradictorio.
        if (
            fondo_tipo == Gimnasio.FondoTipo.IMAGEN
            and not cleaned_data.get("fondo_imagen")
            and "fondo_imagen" not in self.errors
        ):
            self.add_error(
                "fondo_imagen",
                "Subí una imagen para este modo de fondo (o elegí otro tipo).",
            )
        return cleaned_data
