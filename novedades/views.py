"""Vistas de gestión (Fase 2) de novedades: publicar, editar y ocultar avisos
para los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El listado muestra TODAS las novedades del gimnasio,
no solo las `visibles()`: el staff necesita ver y gestionar también las
ocultas o vencidas, a diferencia de la pantalla del alumno (Fase 3).
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from novedades.forms import NovedadForm
from novedades.models import Novedad


class NovedadListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Novedad
    template_name = "novedades/novedad_list.html"
    context_object_name = "novedades"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Se calcula una sola vez el set de pks "visibles ahora" reusando
        # `NovedadQuerySet.visibles()` (no se duplica la condición acá) y se
        # lo expone para que la plantilla decida el badge por fila.
        context["ids_visibles"] = set(
            Novedad.objects.for_gimnasio(self.gimnasio)
            .visibles()
            .values_list("pk", flat=True)
        )
        return context


class NovedadCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Novedad
    form_class = NovedadForm
    template_name = "novedades/novedad_form.html"
    success_url = reverse_lazy("novedades:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Novedad publicada correctamente.")
        return response


class NovedadUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Novedad
    form_class = NovedadForm
    template_name = "novedades/novedad_form.html"
    success_url = reverse_lazy("novedades:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Novedad actualizada correctamente.")
        return response


class NovedadOcultarView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Atajo de un clic desde el listado: pone `activa=False` sin abrir el
    form completo -- la acción "ocultar" literal del ROADMAP Fase 2 §7.

    Solo POST: ocultar es una escritura y no debe poder dispararse desde un
    GET (link, prefetch, crawler). `SingleObjectMixin.get_object()` resuelve
    contra `TenantScopedMixin.get_queryset()`, así que una novedad de otro
    gimnasio da 404, igual que en las demás vistas.
    """

    model = Novedad
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        novedad = self.get_object()
        novedad.activa = False
        novedad.save(update_fields=["activa"])
        messages.success(request, "Novedad ocultada.")
        return self._redirect_a_listado()

    def _redirect_a_listado(self):
        from django.shortcuts import redirect

        return redirect("novedades:listado")
