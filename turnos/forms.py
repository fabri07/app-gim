"""Forms de gestión de turnos (Task 4): configuración general (duración de
clase y cupo default), horarios de atención y excepciones de cupo. Los tres
se editan desde una sola pantalla de staff
(`turnos/views.py::ConfiguracionTurnosView`).
"""

from django import forms

from core.forms import TenantScopedModelForm
from turnos.models import ConfiguracionTurnos, CupoExcepcion, HorarioAtencion
from turnos.services import franjas_del_dia


class ConfiguracionTurnosForm(forms.ModelForm):
    """`ConfiguracionTurnos` no tiene FK a otro `TenantOwnedModel` (solo el
    `gimnasio` propio) -- ModelForm plano, patrón
    `tenants/forms.py::GimnasioForm`, NO `TenantScopedModelForm`."""

    class Meta:
        model = ConfiguracionTurnos
        fields = ["duracion_minutos", "vacantes_default"]


class HorarioAtencionForm(TenantScopedModelForm):
    class Meta:
        model = HorarioAtencion
        fields = ["dia_semana", "hora_desde", "hora_hasta"]
        widgets = {
            "hora_desde": forms.TimeInput(attrs={"type": "time"}),
            "hora_hasta": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()
        dia_semana = cleaned.get("dia_semana")
        hora_desde = cleaned.get("hora_desde")
        hora_hasta = cleaned.get("hora_hasta")
        if dia_semana is None or hora_desde is None or hora_hasta is None:
            # Algún campo ya falló su propia validación -- nada más que
            # chequear acá.
            return cleaned

        if hora_desde >= hora_hasta:
            raise forms.ValidationError(
                "El horario de inicio debe ser anterior al de cierre."
            )

        # Solapamiento: dos rangos [a, b) y [c, d) se cruzan si a < d y c < b.
        # `exclude(pk=self.instance.pk)` es un no-op al crear (pk=None no
        # matchea ninguna fila) y descarta la propia fila al editar --
        # aunque esta tarea no expone una vista de edición todavía, queda
        # preparado (ver brief de Task 4).
        solapados = (
            HorarioAtencion.objects.for_gimnasio(self.gimnasio)
            .filter(dia_semana=dia_semana)
            .exclude(pk=self.instance.pk)
            .filter(hora_desde__lt=hora_hasta, hora_hasta__gt=hora_desde)
        )
        if solapados.exists():
            raise forms.ValidationError(
                "Ya existe un horario que se superpone ese día."
            )
        return cleaned


class CupoExcepcionForm(TenantScopedModelForm):
    class Meta:
        model = CupoExcepcion
        fields = ["dia_semana", "hora_inicio", "vacantes"]
        widgets = {
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()
        dia_semana = cleaned.get("dia_semana")
        hora_inicio = cleaned.get("hora_inicio")
        if dia_semana is None or hora_inicio is None:
            return cleaned

        horas_validas = {
            inicio for inicio, _ in franjas_del_dia(self.gimnasio, dia_semana)
        }
        if hora_inicio not in horas_validas:
            raise forms.ValidationError(
                "Ese horario no coincide con ninguna franja de turnos de ese día."
            )

        # `gimnasio` no es un campo del form -> Django NO corre solo la
        # validación automática de `unique_together` del ModelForm (la
        # excluye porque referencia un campo excluido). Sin este chequeo
        # manual, una segunda excepción para el mismo (gimnasio, dia_semana,
        # hora_inicio) pasa `clean()` y explota en el INSERT con
        # `IntegrityError` (500 crudo) -- no hay vista de edición todavía
        # (el flujo es "borrar y recrear"), así que este es el único lugar
        # donde se puede avisar de forma amigable. `exclude(pk=self.instance.pk)`
        # es un no-op al crear y descarta la propia fila si en el futuro se
        # agrega una vista de edición.
        duplicada = (
            CupoExcepcion.objects.for_gimnasio(self.gimnasio)
            .filter(dia_semana=dia_semana, hora_inicio=hora_inicio)
            .exclude(pk=self.instance.pk)
        )
        if duplicada.exists():
            raise forms.ValidationError(
                "Ya existe una excepción de cupo para ese día y horario. "
                "Eliminala primero si querés cambiar el valor."
            )
        return cleaned
