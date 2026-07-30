"""Form de alta/edición de alumnos de un gimnasio.

`fecha_activacion` queda afuera a propósito: es system-set en el primer login
del alumno (Fase 3, ver `Alumno.fecha_activacion` y `alumnos/signals.py`), no
un campo que el staff edite a mano.
"""

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from core.forms import TenantScopedModelForm
from alumnos import identidad
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


class CrearAccesoForm(forms.Form):
    """Alta del login de un alumno que todavía no tiene uno.

    NO tiene campo de contraseña a propósito: la genera la app
    (`alumnos/services.py::crear_acceso`). El staff solo elige con qué dato
    entra el alumno — su email o su teléfono.

    Tampoco valida acá que el identificador esté libre: eso es una carrera
    (entre el `clean` y el `create_user` puede pasar cualquier cosa) y además
    el servicio ya lo chequea. La vista traduce `IdentificadorEnUso` a un error
    de campo.
    """

    tipo = forms.ChoiceField(
        choices=identidad.TIPOS,
        label="El alumno va a entrar con su",
        initial=identidad.TIPO_EMAIL,
    )
    identificador = forms.CharField(
        max_length=150,
        label="Email o teléfono",
        help_text="Es el usuario con el que va a iniciar sesión.",
    )

    def clean(self):
        datos = super().clean()
        tipo, valor = datos.get("tipo"), datos.get("identificador")
        if not tipo or not valor:
            return datos
        try:
            datos["identificador"] = identidad.normalizar_identificador(tipo, valor)
        except DjangoValidationError as exc:
            self.add_error("identificador", exc.messages)
        return datos
