"""
Form de confirmación de pago (Fase 2 §6).

El staff NUNCA crea un `Cuota` a mano (ver docstring de
`generar_pagos_pendientes` en `pagos/models.py`): las filas pendientes ya
existen, autogeneradas por el cron de Fase 1. Este form solo cubre la única
acción de escritura que le queda al staff sobre un pago: confirmarlo (cargar
el monto real, la fecha de pago, el medio y el comprobante).

Deliberadamente NO incluye:
  - `estado`: confirmar un pago SIEMPRE significa marcarlo PAGADO. Exponerlo
    como dropdown le daría al staff la posibilidad (sin sentido de negocio)
    de "confirmar" un pago dejándolo pendiente/vencido. La vista lo fija.
  - `alumno`, `mes`, `anio`: se definen una sola vez, en la autogeneración;
    editarlos acá permitiría mover un pago a otro alumno/período por error.

Hereda de `TenantScopedModelForm` por el mismo motivo que `EjercicioForm`:
mantener el mismo contrato en todos los forms de Fase 2, aunque ninguno de
estos campos sea un FK tenant-owned que necesite acotarse.
"""

from django import forms

from core.forms import TenantScopedModelForm
from pagos.cobro import cobrada_por_mercadopago
from pagos.models import MedioCobro, Cuota


class ConfirmarPagoForm(TenantScopedModelForm):
    """`estado_al_abrir` (oculto) viaja con el estado que la cuota tenía cuando
    el staff abrió el form: así `ConfirmarPagoView` distingue «estoy
    confirmando una impaga» (y MP la cobró en el medio) de «estoy editando
    una pagada». No es confiable como permiso, sólo como intención: la vista
    relee la base con lock antes de escribir.

    Una cuota cobrada por Mercado Pago no deja cambiar `monto` ni
    `medio_pago_texto`: son lo que MP cobró. `disabled` hace que Django ignore
    lo que llegue en el POST para esos campos."""

    estado_al_abrir = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Cuota
        fields = ["monto", "fecha_pago", "medio_pago_texto", "comprobante"]
        widgets = {
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["estado_al_abrir"].initial = self.instance.estado
        self.cobrada_por_mercadopago = bool(
            self.instance.pk
            and self.instance.estado == Cuota.Estado.PAGADO
            and cobrada_por_mercadopago(self.instance.pk)
        )
        if self.cobrada_por_mercadopago:
            for nombre in ("monto", "medio_pago_texto"):
                self.fields[nombre].disabled = True
                self.fields[nombre].help_text = "Lo cobró Mercado Pago: no se puede cambiar."

    def clean_estado_al_abrir(self):
        valor = self.cleaned_data.get("estado_al_abrir", "")
        return valor if valor in Cuota.Estado.values else ""


class CuotaMontoForm(TenantScopedModelForm):
    """Sólo `monto`: corregir lo que se le cobra a una cuota impaga."""

    class Meta:
        model = Cuota
        fields = ["monto"]

    def clean_monto(self):
        monto = self.cleaned_data["monto"]
        if monto is not None and monto < 0:
            raise forms.ValidationError("No puede ser negativo.")
        return monto


class AlumnoComprobanteForm(forms.ModelForm):
    """Solo `comprobante`: el alumno nunca toca monto/fecha_pago/estado --
    eso lo sigue definiendo el staff en `ConfirmarPagoView`. No hereda de
    `TenantScopedModelForm` porque no hay ningún FK tenant-owned en este
    form que necesite acotarse (a diferencia de `ConfirmarPagoForm`, que sí
    lo hereda por consistencia con el resto de Fase 2)."""

    class Meta:
        model = Cuota
        fields = ["comprobante"]
        widgets = {
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
        }


class MedioCobroForm(TenantScopedModelForm):
    """Alta/edición de un medio de cobro. Incluye `activo` a propósito: no
    hay `DeleteView` para `MedioCobro` (mismo patrón que `Novedad.activa`) --
    "eliminar" un medio de cobro es editarlo y destildar `activo`, no
    borrarlo, así se conserva el historial de a qué alias transfirió cada
    alumno en el pasado."""

    class Meta:
        model = MedioCobro
        fields = ["alias", "titular", "entidad", "activo"]
