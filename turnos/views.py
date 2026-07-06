"""Vistas de turnos.

Task 4: configuración general (duración de clase y cupo default), horarios de
atención y excepciones de cupo. Las tres se editan desde una sola pantalla de
staff (`ConfiguracionTurnosView`); horarios y excepciones tienen su propio
alta/baja que redirige de vuelta a esa pantalla.

Task 5: grilla y reservas del alumno (`MisTurnosView`/`ReservarView`/
`CancelarReservaView`).

Task 6: agenda de staff (`AgendaView`) -- cierra la Feature A (turnos).
"""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, TemplateView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from turnos.forms import (
    ConfiguracionTurnosForm,
    CupoExcepcionForm,
    HorarioAtencionForm,
    ReservaForm,
)
from turnos.models import (
    ConfiguracionTurnos,
    CupoExcepcion,
    HorarioAtencion,
    Reserva,
    obtener_configuracion,
)
from turnos.services import (
    ErrorDeReserva,
    TurnoCerrado,
    cancelar_reserva,
    crear_reserva,
    grilla_semanal,
    reconciliar_reservas_desencajadas,
    reservas_por_franja,
    url_google_calendar,
)


class ReconciliaReservasMixin:
    """Reubica o cancela las reservas futuras que quedaron "desencajadas"
    tras un cambio de horarios/duración (`reconciliar_reservas_desencajadas`)
    y avisa al staff. Cada mensaje SOLO aparece si de verdad hubo alguna
    migración/cancelación -- no ensuciar la pantalla con un aviso vacío en
    el caso común de que la grilla nueva siga cubriendo todas las reservas
    existentes.

    Requiere que la vista que lo use exponga `self.gimnasio` (lo da
    `TenantScopedMixin`, o -- para `ConfiguracionTurnosView`, que no lleva
    ese mixin -- una property propia)."""

    def _reconciliar(self):
        resultado = reconciliar_reservas_desencajadas(self.gimnasio)
        if resultado.migradas > 0:
            messages.info(
                self.request,
                f"Se reprogramaron {resultado.migradas} reserva(s) futura(s) a un nuevo horario.",
            )
        if resultado.canceladas > 0:
            messages.warning(
                self.request,
                f"Se cancelaron {resultado.canceladas} reserva(s) futura(s) que ya no encajan en la nueva grilla.",
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


class MisTurnosView(AlumnoRequiredMixin, TemplateView):
    """Grilla de 14 días (desde el lunes de la semana actual) + "Mis
    reservas" futuras del alumno logueado.

    `self.alumno` puede ser `None` (Perfil de rol alumno todavía sin ficha
    de `Alumno` vinculada, ver `AlumnoRequiredMixin`) -- en ese caso la
    grilla se muestra igual (sin `reservada_por_mi`, porque
    `grilla_semanal` recibe `alumno=None`) y "Mis reservas" queda vacío, no
    un 500.
    """

    template_name = "turnos/mis_turnos.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        hoy = timezone.localdate()
        lunes_actual = hoy - timedelta(days=hoy.weekday())
        context["grilla"] = grilla_semanal(
            self.gimnasio, desde=lunes_actual, dias=14, alumno=self.alumno
        )

        if self.alumno is None:
            mis_reservas = []
        else:
            reservas = Reserva.objects.for_gimnasio(self.gimnasio).filter(
                alumno=self.alumno, fecha__gte=timezone.localdate()
            )
            # Se precomputa acá (no en el template) porque
            # `url_google_calendar` toma 2 argumentos -- el lenguaje de
            # templates de Django solo permite invocar métodos sin
            # argumentos.
            mis_reservas = [
                (reserva, url_google_calendar(reserva, self.gimnasio))
                for reserva in reservas
            ]
        context["mis_reservas"] = mis_reservas

        context["sin_horarios"] = not HorarioAtencion.objects.for_gimnasio(
            self.gimnasio
        ).exists()
        return context


class ReservarView(AlumnoRequiredMixin, View):
    """Solo POST: crea una reserva a partir de una franja de la grilla.
    Nunca deja pasar un `ErrorDeReserva` como 500 -- siempre redirige a
    `mis_turnos` con un mensaje (de éxito o de error)."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied(
                "Todavía no tenés una ficha de alumno vinculada."
            )

        form = ReservaForm(request.POST)
        if form.is_valid():
            try:
                crear_reserva(
                    self.gimnasio,
                    self.alumno,
                    form.cleaned_data["fecha"],
                    form.cleaned_data["hora_inicio"],
                )
            except ErrorDeReserva as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "¡Turno reservado!")
        else:
            messages.error(request, "Datos de turno inválidos.")
        return redirect("turnos:mis_turnos")


class CancelarReservaView(AlumnoRequiredMixin, SingleObjectMixin, View):
    """Solo POST. `get_queryset()` acota SIEMPRE por `(gimnasio, alumno)` --
    una reserva de otro alumno (mismo gimnasio o de otro) da 404, nunca un
    403 ni, peor, un borrado indebido. Si `self.alumno` es `None`, el
    queryset queda vacío a propósito: sigue dando 404 en vez de reventar en
    el filtro."""

    model = Reserva
    http_method_names = ["post"]

    def get_queryset(self):
        if self.alumno is None:
            return Reserva.objects.none()
        return Reserva.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno)

    def post(self, request, *args, **kwargs):
        reserva = self.get_object()
        try:
            cancelar_reserva(reserva)
        except TurnoCerrado as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Reserva cancelada.")
        return redirect("turnos:mis_turnos")


class AgendaView(StaffRequiredMixin, TenantScopedMixin, TemplateView):
    """Agenda de staff: misma grilla de 14 días que `MisTurnosView`, pero de
    solo lectura y con el detalle de quién reservó cada franja (Task 6,
    cierra la Feature A).

    `reservas_por_franja` trae TODAS las reservas del rango en una sola query
    (con `select_related("alumno")`) y las agrupa en un dict en Python -- acá
    se combina ese dict con la grilla, también en Python, en una lista de
    `(franja, reservas_de_esa_franja)` por día. Hace falta este paso porque
    el lenguaje de templates de Django no permite indexar un dict por una
    clave tupla armada a partir de dos variables de contexto (`reservas.franja.hora_inicio`
    no funciona) -- mismo motivo por el que `MisTurnosView` arma `mis_reservas`
    como lista de tuplas en vez de resolverlo en el template. El template
    nunca dispara una query por celda de la grilla.
    """

    template_name = "turnos/agenda.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        hoy = timezone.localdate()
        lunes_actual = hoy - timedelta(days=hoy.weekday())
        grilla = grilla_semanal(self.gimnasio, desde=lunes_actual, dias=14)
        reservas = reservas_por_franja(
            self.gimnasio, lunes_actual, lunes_actual + timedelta(days=13)
        )

        context["grilla"] = {
            fecha: [
                (franja, reservas.get((fecha, franja.hora_inicio), []))
                for franja in franjas
            ]
            for fecha, franjas in grilla.items()
        }
        return context
