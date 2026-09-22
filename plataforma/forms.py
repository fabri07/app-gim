"""
Los dos formularios del panel de plataforma: registrar un pago y ajustar cómo
se le factura a un gimnasio.

Los dos se renderizan campo por campo con `partials/campo_form.html`, nunca
con `{{ form.as_p }}`: la `errorlist` de Django no tiene estilo en este
proyecto y sale en negro arriba de la etiqueta, indistinguible de una ayuda.
Un formulario que rechaza sin que se note es igual a uno que no guarda.
"""

from django import forms

from plataforma.models import PagoPlataforma
from tenants.models import Gimnasio


class PagoPlataformaForm(forms.ModelForm):
    """Alta de un pago ya cobrado.

    **`gimnasio` y `registrado_por` no están en el form a propósito.** El
    gimnasio sale de la URL y el usuario de la sesión, los dos estampados del
    lado del servidor por la vista -- mismo criterio que `TenantScopedMixin`
    en el resto del proyecto. Exponerlos dejaría que un POST armado a mano le
    acredite el pago de un cliente a otro.
    """

    class Meta:
        model = PagoPlataforma
        fields = [
            "fecha_pago",
            "periodo_desde",
            "periodo_hasta",
            "alumnos_activos",
            "monto_usd",
            "tipo_cambio",
            "monto_ars",
            "notas",
        ]
        widgets = {
            "fecha_pago": forms.DateInput(attrs={"type": "date"}),
            "periodo_desde": forms.DateInput(attrs={"type": "date"}),
            "periodo_hasta": forms.DateInput(attrs={"type": "date"}),
            "notas": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        """Un período que termina antes de empezar no se guarda.

        El error se cuelga de `periodo_hasta` y no del formulario entero
        porque es ahí donde está lo que hay que corregir: un error general
        arriba de todo obliga a adivinar cuál de los dos campos mover.

        Duplica la `CheckConstraint` del modelo a propósito: sin esta
        validación el rechazo llega como un `IntegrityError` (un 500 mudo) en
        vez de un mensaje al lado del campo.
        """
        limpio = super().clean()
        desde = limpio.get("periodo_desde")
        hasta = limpio.get("periodo_hasta")
        if desde and hasta and hasta < desde:
            self.add_error(
                "periodo_hasta",
                "El período no puede terminar antes de empezar.",
            )
        return limpio


class FacturacionForm(forms.ModelForm):
    """Desde cuándo y si se le factura a un gimnasio.

    Es el único camino de UI para estos dos campos (fuera de `/admin/`): el
    dueño del gimnasio no los ve, `GimnasioForm.Meta.fields` los deja afuera.
    """

    class Meta:
        model = Gimnasio
        fields = ["facturacion_inicio", "facturacion_exenta"]
        widgets = {
            "facturacion_inicio": forms.DateInput(attrs={"type": "date"}),
        }
