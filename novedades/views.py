"""Vistas de gestión (Fase 2) de novedades: publicar, editar y ocultar avisos
para los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El listado muestra TODAS las novedades del gimnasio,
no solo las `visibles()`: el staff necesita ver y gestionar también las
ocultas o vencidas, a diferencia de la pantalla del alumno (Fase 3).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from novedades.forms import NovedadForm
from novedades.models import Novedad, NovedadLeida


class NovedadListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Novedad
    template_name = "novedades/novedad_list.html"
    context_object_name = "novedades"

    def get_queryset(self):
        # `distinct=True` evita que el join con `lecturas` infle el conteo
        # si en el futuro se agrega otra tabla relacionada por `Novedad`.
        return super().get_queryset().annotate(
            lecturas_count=Count("lecturas", distinct=True)
        )

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
        # Denominador del "X/Y leído" del listado (Feature B, Task 9): import
        # tardío, mismo patrón que `tenants/views.py::HomeView`. Solo cuenta
        # alumnos ACTIVO -- el numerador (lecturas_count, arriba) no filtra
        # por estado del alumno, así que en casos raros (alumno pasado a
        # INACTIVO después de leer una novedad) X puede superar a Y. Es
        # aceptado (ver brief de Task 9).
        from alumnos.models import Alumno

        context["alumnos_activos_count"] = Alumno.objects.for_gimnasio(
            self.gimnasio
        ).filter(estado=Alumno.Estado.ACTIVO).count()
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


class NovedadMarcarLeidaView(AlumnoRequiredMixin, View):
    """El alumno marca como leída una novedad visible de su gimnasio (Fase 5,
    Feature B: read-receipts).

    Solo POST -- es una escritura, igual que `NovedadOcultarView`. Idempotente
    vía `get_or_create` (no `create`): un segundo click no duplica la fila ni
    rompe contra el `unique_together` de `NovedadLeida`. La novedad se busca
    filtrando por `.visibles()` (no solo `for_gimnasio()`): una novedad oculta,
    vencida o de otro gimnasio da 404 -- no es un tema de permisos (403), es
    que desde la perspectiva del alumno esa novedad "no existe" para marcar.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        novedad = get_object_or_404(
            Novedad.objects.for_gimnasio(self.gimnasio).visibles(), pk=kwargs["pk"]
        )
        NovedadLeida.objects.get_or_create(novedad=novedad, alumno=self.alumno)
        return redirect("home")


class NovedadLecturasView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """Detalle de qué alumnos leyeron una novedad puntual (Feature B,
    Task 9) -- a lo que enlaza el "X/Y" del listado (`NovedadListView`).

    `get_object()` resuelve contra `TenantScopedMixin.get_queryset()`, así
    que una novedad de otro gimnasio da 404, igual que en el resto de las
    vistas de gestión.
    """

    model = Novedad
    template_name = "novedades/novedad_lecturas.html"
    context_object_name = "novedad"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lecturas"] = self.object.lecturas.select_related("alumno")
        return context
