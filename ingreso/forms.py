"""Formularios de las pantallas de staff del control de ingreso.

Las dos unicidades que chocan desde un formulario (`Dispositivo(gimnasio,
nombre)` y `Credencial(gimnasio, tipo, valor)`) llevan `gimnasio`, que NO es un
campo del form: el `validate_constraints` de Django se saltea toda restricción
que nombre un campo excluido, así que sin estos `clean_*` el choque llega a la
base como un `IntegrityError` y la pantalla da un 500. La vista además atrapa
el `IntegrityError` para la carrera del doble submit, que ningún chequeo previo
cubre.
"""

from django import forms

from core.forms import TenantScopedModelForm
from ingreso.models import ConfiguracionIngreso, Credencial, Dispositivo

ERROR_NOMBRE_REPETIDO = "Ya tenés un lector con ese nombre."


class DispositivoForm(TenantScopedModelForm):
    """Alta y edición de un lector. `activo` solo existe al editar: un lector
    nace activo (para eso se lo da de alta), y desactivarlo es la única forma
    de «borrarlo» -- el `PROTECT` de los eventos lo respalda."""

    class Meta:
        model = Dispositivo
        fields = ["nombre", "tipo", "activo"]
        labels = {"nombre": "Nombre", "tipo": "Qué lee", "activo": "Activo"}
        help_texts = {
            "nombre": "Cómo lo vas a reconocer en el listado: «Molinete de la entrada».",
            "activo": (
                "Destildalo para que este lector deje de abrir la puerta. Su "
                "historial de ingresos se conserva."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            del self.fields["activo"]

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        repetido = (
            Dispositivo.objects.for_gimnasio(self.gimnasio)
            .filter(nombre__iexact=nombre)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if repetido:
            raise forms.ValidationError(ERROR_NOMBRE_REPETIDO)
        return nombre


class ConfiguracionIngresoForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionIngreso
        fields = ["bloquea_por_deuda", "exige_reserva", "minutos_antipassback"]


class CredencialFacialForm(forms.Form):
    """El id que el alumno tiene EN el lector facial. Nunca una imagen ni una
    plantilla biométrica: la cara se enrola en el equipo y el staff copia acá
    el número que el equipo le asignó."""

    valor = forms.CharField(
        label="Número de usuario en el lector",
        max_length=Credencial._meta.get_field("valor").max_length,
        help_text=(
            "Registrá la cara del alumno en el lector y copiá acá el número de "
            "usuario que te muestra el equipo."
        ),
    )

    def __init__(self, *args, alumno, **kwargs):
        super().__init__(*args, **kwargs)
        self.alumno = alumno

    def clean_valor(self):
        # `CharField` ya hace `strip()` (espacios, `\r\n`): el mismo criterio
        # que `Credencial.save()` y que la puerta al comparar.
        valor = self.cleaned_data["valor"]
        otro = self.otro_alumno_con(valor)
        if otro is not None:
            raise forms.ValidationError(error_valor_repetido(otro))
        return valor

    def otro_alumno_con(self, valor):
        """El alumno del mismo gimnasio que ya tiene este id en el lector, o
        `None`. La usa `clean_valor` y la vista, que la vuelve a correr ante un
        `IntegrityError` para distinguir un choque real de un doble submit."""
        otra = (
            Credencial.objects.for_gimnasio(self.alumno.gimnasio)
            .filter(tipo=Credencial.Tipo.FACIAL, valor=valor)
            .exclude(alumno=self.alumno)
            .select_related("alumno")
            .first()
        )
        return otra.alumno if otra is not None else None


def error_valor_repetido(alumno):
    return f"Ese número ya lo tiene {alumno} en el lector."
