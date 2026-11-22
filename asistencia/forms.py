"""Formulario del staff para cargar a mano un día de asistencia."""

from datetime import timedelta

from django import forms
from django.utils import timezone

from asistencia.services import MAX_DIAS_ATRAS


class RegistrarAsistenciaForm(forms.Form):
    fecha = forms.DateField(
        label="Día que vino",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={
            "required": "Elegí el día que vino.",
            "invalid": "Esa fecha no es válida: elegí un día del calendario.",
        },
    )

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        hoy = timezone.localdate()
        if fecha > hoy:
            raise forms.ValidationError(
                "No se puede cargar un día que todavía no pasó."
            )
        if fecha < hoy - timedelta(days=MAX_DIAS_ATRAS):
            raise forms.ValidationError(
                f"Sólo se puede cargar asistencia de los últimos {MAX_DIAS_ATRAS} días."
            )
        return fecha
