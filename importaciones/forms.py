"""Forms del importador (Proyecto 2). Los de subida son `forms.Form`
planos (no `ModelForm`, no hay modelo destino directo) que aceptan
`gimnasio` en `__init__` solo porque `TenantScopedMixin.get_form_kwargs()`
siempre lo inyecta -- mismo patrón que `AsignarRutinaForm` en
`rutinas/forms.py`. Los formsets de preview usan `forms.formset_factory`
(mecanismo idiomático de Django para N repeticiones de un sub-form)."""

import json

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


class ResolucionesJSONForm(forms.Form):
    """Reemplaza a `ResolucionGrupoMuscularFormSet` (un form por ejercicio
    pendiente) por un único campo con las resoluciones serializadas en
    JSON -- así el conteo de campos del POST de confirmación de biblioteca
    no escala con la cantidad de ejercicios sin match (una biblioteca real
    puede traer miles; ver ISSUES.md [2026-07-28] y su seguimiento)."""
    resoluciones = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self):
        # Validado en `clean()` (no en `clean_resoluciones`) para poder usar
        # `add_error(None, ...)`: un error de campo en `resoluciones` no se
        # ve en ningún lado (el campo es un `HiddenInput` y el template solo
        # renderiza `form.non_field_errors`) -- sin esto, un POST manipulado
        # o un bug del JS de serialización re-renderiza 200 sin ningún
        # mensaje visible para el staff (fix post-review, Tarea 13).
        cleaned = super().clean()
        crudo = cleaned.get("resoluciones") or "{}"
        try:
            datos = json.loads(crudo)
        except (json.JSONDecodeError, TypeError):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        if not isinstance(datos, dict):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        for clave, valor in datos.items():
            if not isinstance(clave, str) or valor not in Ejercicio.GrupoMuscular.values:
                self.add_error(None, "Grupo muscular inválido.")
                return cleaned
        cleaned["resoluciones"] = datos
        return cleaned
