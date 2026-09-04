"""Form de alta/edición de alumnos de un gimnasio.

`fecha_activacion` queda afuera a propósito: es system-set en el primer login
del alumno (Fase 3, ver `Alumno.fecha_activacion` y `alumnos/signals.py`), no
un campo que el staff edite a mano.
"""

from django import forms
from django.utils import timezone
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
            "fecha_inicio_ciclo",
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
            "fecha_inicio_ciclo": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # OBLIGATORIO en el formulario aunque sea `blank=True` en el modelo.
        # El modelo tiene que poder nacer sin ancla (la estampan las señales
        # del alta y de la primera rutina), pero desde la ficha vaciarla
        # tendría un efecto invisible y grave: `generar_pagos_pendientes`
        # filtra por ancla no nula, así que el alumno dejaría de recibir
        # cuotas para siempre sin ningún síntoma.
        #
        # Se resuelve con `required` y no con un `clean_*` que lance
        # `ValidationError`: así el error sale por el camino normal de Django,
        # lo renderiza `partials/campo_form.html` debajo del campo, y un POST
        # que omita el campo falla de forma visible en vez de guardar a
        # medias.
        self.fields["fecha_inicio_ciclo"].required = True

    def clean_fecha_inicio_ciclo(self):
        """El ancla no puede ser futura.

        `pagos.ciclo_vigente` no emite nada mientras el ancla no llegue, así
        que una fecha futura tipeada por error apaga la facturación de ese
        alumno, en silencio y hasta esa fecha.
        """
        fecha = self.cleaned_data["fecha_inicio_ciclo"]
        # El valor que YA está guardado pasa siempre, aunque sea futuro: la
        # señal `anclar_ciclo_a_la_primera_rutina` escribe legítimamente un
        # ancla futura cuando el plan se carga con anticipación (caso
        # soportado a propósito). Sin esta excepción, hasta que llegara ese
        # día NINGÚN guardado de la ficha -- corregir un teléfono, cargar la
        # ficha de inscripción -- pasaba la validación, por un valor que el
        # staff no tipeó. Lo que se rechaza es que el staff la MUEVA a futuro.
        if fecha > timezone.localdate() and fecha != self.instance.fecha_inicio_ciclo:
            raise forms.ValidationError(
                "El inicio del ciclo de pago no puede ser una fecha futura: "
                "hasta ese día no se le emitiría ninguna cuota."
            )
        return fecha



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
