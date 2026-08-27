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
from ejercicios.models import CategoriaEjercicio, Ejercicio


class EjercicioListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Ejercicio
    template_name = "ejercicios/ejercicio_list.html"
    context_object_name = "ejercicios"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("categoria")
        # El filtro viaja por id, no por texto: las categorías son por
        # gimnasio, así que un slug global ya no identifica nada.
        self.categoria_actual = None
        categoria_id = self.request.GET.get("categoria", "").strip()
        if categoria_id.isdigit():
            self.categoria_actual = (
                CategoriaEjercicio.objects.for_gimnasio(self.gimnasio)
                .filter(pk=categoria_id)
                .first()
            )
            if self.categoria_actual is not None:
                queryset = queryset.filter(categoria=self.categoria_actual)
        self.q = self.request.GET.get("q", "").strip()
        if self.q:
            queryset = queryset.filter(nombre__icontains=self.q)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Todas, no solo las activas: el staff tiene que poder filtrar por una
        # categoría que desactivó para encontrar los ejercicios que quedaron
        # colgados de ella (mismo criterio que `MedioCobroListView`).
        context["categorias"] = CategoriaEjercicio.objects.for_gimnasio(
            self.gimnasio
        )
        context["categoria_actual"] = self.categoria_actual
        context["q_actual"] = self.q
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
