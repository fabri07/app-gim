"""Form de alta/edición de alumnos de un gimnasio.

`fecha_activacion` queda afuera a propósito: es system-set en el primer login
del alumno (Fase 3, ver `Alumno.fecha_activacion` y `alumnos/signals.py`), no
un campo que el staff edite a mano.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

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
            "sexo",
            "actividad_fisica_previa",
            "frecuencia_actividad_previa",
            "deportes_practica",
            "tiene_discapacidad",
            "discapacidad_detalle",
            "tiene_enfermedad_cronica",
            "enfermedad_cronica_detalle",
            "observaciones",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
        }


_PASSWORD_HELP_TEXT = (
    "Se muestra en texto plano a propósito: el staff necesita poder leerla "
    "para comunicársela al alumno en persona o por WhatsApp. Django la "
    "guarda hasheada — nadie, ni el staff, va a poder verla de nuevo "
    "después de este paso."
)


class CrearAccesoForm(forms.Form):
    """Alta del login (usuario/contraseña) de un alumno que todavía no
    tiene uno. Ver `alumnos/views.py::CrearAccesoView` — Fase 3 reemplazó el
    magic-link original por este flujo (ver ISSUES.md 2026-07-01)."""

    username = forms.CharField(max_length=150, label="Usuario")
    password = forms.CharField(
        label="Contraseña",
        widget=forms.TextInput,
        help_text=_PASSWORD_HELP_TEXT,
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        User = get_user_model()
        # El username es GLOBAL a toda la plataforma (no hay namespacing por
        # gimnasio en `User.username`): dos gimnasios distintos no pueden
        # tener ambos un alumno "juan". No se renombra en silencio, se avisa.
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError(
                "Ese usuario ya está en uso (el nombre de usuario es único "
                "en toda la plataforma, no solo en este gimnasio). Elegí "
                "otro."
            )
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return password


class CambiarPasswordAlumnoForm(forms.Form):
    """Reseteo de la contraseña de un alumno que ya tiene login. Mismo
    criterio de validación/visibilidad que `CrearAccesoForm.password`."""

    password = forms.CharField(
        label="Contraseña",
        widget=forms.TextInput,
        help_text=_PASSWORD_HELP_TEXT,
    )

    def clean_password(self):
        password = self.cleaned_data["password"]
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return password
