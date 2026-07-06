"""Vistas de gestión (Fase 2 §6) de pagos mensuales de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). No hay vista de creación: los `PagoMensual` PENDIENTE
ya existen, autogenerados por el cron de Fase 1 (`generar_pagos_pendientes`).
La única acción de escritura del staff es confirmar un pago existente
(`ConfirmarPagoView`), operando siempre sobre un pk ya acotado al tenant por
`TenantScopedMixin.get_queryset` -- por eso un pk de otro gimnasio da 404, no
403 (Django resuelve `SingleObjectMixin.get_object` sobre ese queryset).
"""

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from pagos.forms import ConfirmarPagoForm, MedioCobroForm
from pagos.models import MedioCobro, PagoMensual


class PagoMensualListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = PagoMensual
    template_name = "pagos/pago_list.html"
    context_object_name = "pagos"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("alumno")
        self.mes = self.request.GET.get("mes", "")
        self.anio = self.request.GET.get("anio", "")
        self.estado = self.request.GET.get("estado", "")

        if self.mes:
            queryset = queryset.filter(mes=self.mes)
        if self.anio:
            queryset = queryset.filter(anio=self.anio)
        if self.estado == "deudores":
            queryset = queryset.filter(
                estado__in=[PagoMensual.Estado.PENDIENTE, PagoMensual.Estado.VENCIDO]
            )
        elif self.estado:
            queryset = queryset.filter(estado=self.estado)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mes_actual"] = self.mes
        context["anio_actual"] = self.anio
        context["estado_actual"] = self.estado
        context["estados"] = PagoMensual.Estado.choices
        return context


class ConfirmarPagoView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = PagoMensual
    form_class = ConfirmarPagoForm
    template_name = "pagos/pago_confirmar.html"
    success_url = reverse_lazy("pagos:listado")

    def form_valid(self, form):
        form.instance.estado = PagoMensual.Estado.PAGADO
        response = super().form_valid(form)
        messages.success(self.request, "Pago confirmado correctamente.")
        return response


class MedioCobroListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Lista TODOS los medios de cobro del gimnasio (activos e inactivos):
    el staff necesita ver los inactivos para poder reactivarlos, a
    diferencia de lo que ve el alumno en el portal (Task 12), que solo
    debe ver los `activo=True`."""

    model = MedioCobro
    template_name = "pagos/medio_list.html"
    context_object_name = "medios"


class MedioCobroCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = MedioCobro
    form_class = MedioCobroForm
    template_name = "pagos/medio_form.html"
    success_url = reverse_lazy("pagos:medios_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Medio de cobro creado correctamente.")
        return response


class MedioCobroUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """No hay `MedioCobroDeleteView`: "eliminar" un medio de cobro es editarlo
    acá y destildar `activo` (mismo patrón que `Novedad.activa` en
    `NovedadUpdateView`) -- se conserva el historial en vez de borrar filas
    a las que pueden referirse pagos históricos."""

    model = MedioCobro
    form_class = MedioCobroForm
    template_name = "pagos/medio_form.html"
    success_url = reverse_lazy("pagos:medios_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Medio de cobro actualizado correctamente.")
        return response
