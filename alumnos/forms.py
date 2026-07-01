"""Form de alta/edición de alumnos de un gimnasio.

`fecha_activacion` queda afuera a propósito: es system-set por el magic-link
de Fase 3 (ver `Alumno.fecha_activacion`), no un campo que el staff edite a
mano.
"""

from django import forms

from core.forms import TenantScopedModelForm
from alumnos.models import Alumno


class AlumnoForm(TenantScopedModelForm):
    class Meta:
        model = Alumno
        fields = [
            "nombre",
            "apellido",
            "email",
            "telefono",
            "fecha_nacimiento",
            "estado",
            "observaciones",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
        }
