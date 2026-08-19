"""
Form de confirmación de pago (Fase 2 §6).

El staff NUNCA crea un `PagoMensual` a mano (ver docstring de
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
from pagos.models import MedioCobro, PagoMensual


class ConfirmarPagoForm(TenantScopedModelForm):
    class Meta:
        model = PagoMensual
        fields = ["monto", "fecha_pago", "medio_pago_texto", "comprobante"]
        widgets = {
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
        }


class AlumnoComprobanteForm(forms.ModelForm):
    """Solo `comprobante`: el alumno nunca toca monto/fecha_pago/estado --
    eso lo sigue definiendo el staff en `ConfirmarPagoView`. No hereda de
    `TenantScopedModelForm` porque no hay ningún FK tenant-owned en este
    form que necesite acotarse (a diferencia de `ConfirmarPagoForm`, que sí
    lo hereda por consistencia con el resto de Fase 2)."""

    class Meta:
        model = PagoMensual
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
