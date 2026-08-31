"""
Forms de gestión de rutinas (Fase 2).

`RutinaPlantillaForm` y `RutinaPlantillaItemForm` heredan de
`TenantScopedModelForm`: cierran automáticamente el hueco de FK-injection en
cualquier campo `ModelChoice*Field` cuyo modelo sea `TenantOwnedModel` (en
`RutinaPlantillaItemForm`, el campo `ejercicio` -- ver docstring de
`core.forms.TenantScopedModelForm`).

`AsignarRutinaForm` es un `forms.Form` plano (no `ModelForm`): `RutinaAsignada`
se crea vía `RutinaAsignada.crear_desde_plantilla`, no vía `form.save()` (ver
ROADMAP Fase 1). Por eso el scoping de sus dos `ModelChoiceField` se hace a
mano acá, replicando lo que `TenantScopedModelForm` hace automáticamente para
un `ModelForm`.
"""

from django import forms

from alumnos.models import Alumno
from core.forms import TenantScopedModelForm
from rutinas.models import RutinaPlantilla, RutinaPlantillaItem


class RutinaPlantillaForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantilla
        fields = ["nombre", "objetivo", "nivel", "dias_por_semana", "activa"]


class RutinaPlantillaItemForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantillaItem
        fields = [
            "ejercicio",
            "semana",
            "dia",
            "dia_nombre",
            "orden",
            "bloque",
            "series",
            "repeticiones",
            "kilos",
            "descanso",
            "notas",
        ]


class AsignarRutinaForm(forms.Form):
    """Elegir alumno + plantilla + fecha de inicio para generar el snapshot
    (ROADMAP Fase 2 §5). Los querysets de ambos campos se acotan al gimnasio
    del staff que asigna, igual que haría `TenantScopedModelForm` en un
    `ModelForm` -- acá se hace a mano porque este form no lo es."""

    alumno = forms.ModelChoiceField(queryset=Alumno.objects.none())
    plantilla = forms.ModelChoiceField(queryset=RutinaPlantilla.objects.none())
    fecha_inicio = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["alumno"].queryset = Alumno.objects.for_gimnasio(
            gimnasio
        ).filter(estado=Alumno.Estado.ACTIVO)
        self.fields["plantilla"].queryset = RutinaPlantilla.objects.for_gimnasio(
            gimnasio
        ).filter(activa=True)
