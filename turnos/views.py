"""Vistas de gestión de turnos (Task 4): configuración general (duración de
clase y cupo default), horarios de atención y excepciones de cupo. Las tres
se editan desde una sola pantalla de staff (`ConfiguracionTurnosView`);
horarios y excepciones tienen su propio alta/baja que redirige de vuelta a
esa pantalla.

Alcance de esta tarea: SOLO configuración. La grilla y las reservas del
alumno (`MisTurnosView`/`ReservarView`/`CancelarReservaView`) y la agenda de
staff (`AgendaView`) son las Tasks 5/6.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from turnos.forms import (
    ConfiguracionTurnosForm,
    CupoExcepcionForm,
    HorarioAtencionForm,
)
from turnos.models import (
    ConfiguracionTurnos,
    CupoExcepcion,
    HorarioAtencion,
    obtener_configuracion,
)
from turnos.services import eliminar_reservas_desencajadas


class ReconciliaReservasMixin:
    """Borra las reservas futuras que quedaron "desencajadas" tras un cambio
    de horarios/duración (Task 3, `eliminar_reservas_desencajadas`) y avisa
    al staff cuántas se cancelaron. El mensaje SOLO aparece si de verdad se
    borró alguna (`n > 0`) -- no ensuciar la pantalla con un aviso vacío en
    el caso común de que la grilla nueva siga cubriendo todas las reservas
    existentes.

    Requiere que la vista que lo use exponga `self.gimnasio` (lo da
    `TenantScopedMixin`, o -- para `ConfiguracionTurnosView`, que no lleva
    ese mixin -- una property propia)."""

    def _reconciliar(self):
        n = eliminar_reservas_desencajadas(self.gimnasio)
        if n > 0:
            messages.warning(
                self.request,
                f"Se cancelaron {n} reserva(s) futura(s) que ya no encajan en la nueva grilla.",
            )


class ConfiguracionTurnosView(StaffRequiredMixin, ReconciliaReservasMixin, UpdateView):
    """Pantalla única de configuración: form de duración/cupo default (este
    `UpdateView`) + tablas de horarios y excepciones, cada una con su propio
    form de alta inline (ver `turnos/configuracion_form.html`).

    Sin pk en la URL a propósito -- "mi configuración", no "configuración
    <pk>" -- mismo patrón que `tenants/views.py::GimnasioUpdateView`. No
    lleva `TenantScopedMixin`: no hay otro registro que se pueda alcanzar
    por esta vista (`get_object` siempre resuelve el propio gimnasio del
    staff logueado)."""

    model = ConfiguracionTurnos
    form_class = ConfiguracionTurnosForm
    template_name = "turnos/configuracion_form.html"
    success_url = reverse_lazy("turnos:configuracion")

    def get_object(self, queryset=None):
        return obtener_configuracion(self.gimnasio)

    @property
    def gimnasio(self):
        # `StaffRequiredMixin.dispatch` ya garantizó que el Perfil existe.
        return self.request.user.perfil.gimnasio

    def form_valid(self, form):
        response = super().form_valid(form)
        self._reconciliar()
        messages.success(self.request, "Configuración de turnos actualizada.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["horarios"] = HorarioAtencion.objects.for_gimnasio(self.gimnasio)
        context["excepciones"] = CupoExcepcion.objects.for_gimnasio(self.gimnasio)
        context["horario_form"] = HorarioAtencionForm(gimnasio=self.gimnasio)
        context["cupo_form"] = CupoExcepcionForm(gimnasio=self.gimnasio)
        return context


class HorarioAtencionCreateView(
    StaffRequiredMixin, TenantScopedMixin, ReconciliaReservasMixin, CreateView
):
    model = HorarioAtencion
    form_class = HorarioAtencionForm
    template_name = "turnos/horario_form.html"
    success_url = reverse_lazy("turnos:configuracion")

    def form_valid(self, form):
        response = super().form_valid(form)
        self._reconciliar()
        messages.success(self.request, "Horario agregado.")
        return response


class HorarioAtencionEliminarView(
    StaffRequiredMixin,
    TenantScopedMixin,
    ReconciliaReservasMixin,
    SingleObjectMixin,
    View,
):
    """Solo POST: borrar es una escritura, no debe dispararse desde un GET
    (link, prefetch, crawler) -- mismo patrón que `NovedadOcultarView`.
    `TenantScopedMixin.get_queryset()` acota el `get_object()` heredado de
    `SingleObjectMixin`: un horario de otro gimnasio da 404, nunca 403."""

    model = HorarioAtencion
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        self.get_object().delete()
        self._reconciliar()
        messages.success(request, "Horario eliminado.")
        return redirect("turnos:configuracion")


class CupoExcepcionCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    """Sin `ReconciliaReservasMixin`: los cupos no desencajan reservas, solo
    la vigencia de la franja (horarios/duración) lo hace."""

    model = CupoExcepcion
    form_class = CupoExcepcionForm
    template_name = "turnos/cupo_form.html"
    success_url = reverse_lazy("turnos:configuracion")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Excepción de cupo agregada.")
        return response


class CupoExcepcionEliminarView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    model = CupoExcepcion
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        self.get_object().delete()
        messages.success(request, "Excepción de cupo eliminada.")
        return redirect("turnos:configuracion")
