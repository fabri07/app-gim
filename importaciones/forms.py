"""Forms del importador (Proyecto 2). Los de subida son `forms.Form`
planos (no `ModelForm`, no hay modelo destino directo) que aceptan
`gimnasio` en `__init__` solo porque `TenantScopedMixin.get_form_kwargs()`
siempre lo inyecta -- mismo patrón que `AsignarRutinaForm` en
`rutinas/forms.py`. Los formsets de preview usan `forms.formset_factory`
(mecanismo idiomático de Django para N repeticiones de un sub-form)."""

from django import forms
from django.core.validators import FileExtensionValidator

from ejercicios.models import Ejercicio
from rutinas.models import RutinaPlantilla


class SubirArchivoForm(forms.Form):
    archivo = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])]
    )

    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.gimnasio = gimnasio


class SubirPlantillasForm(SubirArchivoForm):
    pass


class SubirBibliotecaForm(SubirArchivoForm):
    pass


class HojaMetadataForm(forms.Form):
    nombre_hoja = forms.CharField(widget=forms.HiddenInput)
    incluir = forms.BooleanField(required=False, initial=True)
    objetivo = forms.CharField(max_length=120)
    nivel = forms.ChoiceField(choices=RutinaPlantilla.Nivel.choices)


HojaMetadataFormSet = forms.formset_factory(HojaMetadataForm, extra=0)


class ResolucionEjercicioForm(forms.Form):
    nombre_normalizado = forms.CharField(widget=forms.HiddenInput)
    accion = forms.ChoiceField(choices=[
        ("usar_existente", "Usar existente"),
        ("crear_nuevo", "Crear como nuevo"),
    ])
    ejercicio_existente_id = forms.IntegerField(required=False)
    grupo_muscular = forms.ChoiceField(
        choices=Ejercicio.GrupoMuscular.choices, required=False,
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("accion") == "crear_nuevo" and not cleaned.get("grupo_muscular"):
            self.add_error(
                "grupo_muscular", "Elegí un grupo muscular para el ejercicio nuevo."
            )
        return cleaned


ResolucionEjercicioFormSet = forms.formset_factory(ResolucionEjercicioForm, extra=0)


class ResolucionGrupoMuscularForm(forms.Form):
    valor_original = forms.CharField(widget=forms.HiddenInput)
    grupo_muscular = forms.ChoiceField(choices=Ejercicio.GrupoMuscular.choices)


ResolucionGrupoMuscularFormSet = forms.formset_factory(ResolucionGrupoMuscularForm, extra=0)
