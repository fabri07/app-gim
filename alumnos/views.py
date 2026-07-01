"""Vistas de gestión (Fase 2) de los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El "activar/inactivar" es una acción POST-only
separada (nunca una vista de borrado): un `Alumno` nunca se borra, solo
cambia de estado (queda como historial para pagos y rutinas ya emitidos).

No hay vista para "enviar invitación / magic-link": eso es Fase 3, donde se
diseña el mecanismo de acceso sin contraseña del alumno.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from alumnos.forms import AlumnoForm
from alumnos.models import Alumno


class AlumnoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Alumno
    template_name = "alumnos/alumno_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        queryset = super().get_queryset()
        self.estado_actual = self.request.GET.get("estado", "")
        if self.estado_actual in (Alumno.Estado.ACTIVO, Alumno.Estado.INACTIVO):
            queryset = queryset.filter(estado=self.estado_actual)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estado_actual"] = self.estado_actual
        return context


class AlumnoCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno creado correctamente.")
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


class AlumnoUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno actualizado correctamente.")
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


class AlumnoDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """La "ficha" del alumno: sus datos + secciones de solo lectura de sus
    pagos (app `pagos`) y su rutina activa (app `rutinas`)."""

    model = Alumno
    template_name = "alumnos/alumno_detail.html"
    context_object_name = "alumno"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pagos"] = self.object.pagos.all()
        context["rutina_actual"] = self.object.rutinas_asignadas.filter(
            activa=True
        ).first()
        return context


class AlumnoToggleEstadoView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Flip activo <-> inactivo. POST-only: muta estado, nunca debe
    dispararse con un GET (link, prefetch, etc)."""

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.estado == Alumno.Estado.ACTIVO:
            alumno.estado = Alumno.Estado.INACTIVO
        else:
            alumno.estado = Alumno.Estado.ACTIVO
        alumno.save(update_fields=["estado"])
        messages.success(
            request, f"{alumno} ahora está {alumno.get_estado_display().lower()}."
        )
        return redirect("alumnos:detalle", pk=alumno.pk)
