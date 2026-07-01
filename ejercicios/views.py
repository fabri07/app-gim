"""Vistas de gestión (Fase 2) de la biblioteca de ejercicios de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). No hay vista de borrado: `activo=False` es la forma de
"retirar" un ejercicio de uso activo sin romper `RutinaAsignada` items que lo
referencian con `on_delete=PROTECT` (ver docstring de `Ejercicio`).
"""

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from ejercicios.forms import EjercicioForm
from ejercicios.models import Ejercicio


class EjercicioListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Ejercicio
    template_name = "ejercicios/ejercicio_list.html"
    context_object_name = "ejercicios"

    def get_queryset(self):
        queryset = super().get_queryset()
        self.grupo_muscular = self.request.GET.get("grupo_muscular", "")
        if self.grupo_muscular:
            queryset = queryset.filter(grupo_muscular=self.grupo_muscular)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["grupos_musculares"] = Ejercicio.GrupoMuscular.choices
        context["grupo_muscular_actual"] = self.grupo_muscular
        return context


class EjercicioCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Ejercicio
    form_class = EjercicioForm
    template_name = "ejercicios/ejercicio_form.html"
    success_url = reverse_lazy("ejercicios:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Ejercicio creado correctamente.")
        return response


class EjercicioUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Ejercicio
    form_class = EjercicioForm
    template_name = "ejercicios/ejercicio_form.html"
    success_url = reverse_lazy("ejercicios:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Ejercicio actualizado correctamente.")
        return response
