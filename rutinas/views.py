"""
Vistas de gestión (Fase 2) de rutinas: CRUD de plantillas, alta/edición/borrado
de sus items, duplicar plantilla, y asignación de una plantilla a un alumno.

Todas son solo para staff (`StaffRequiredMixin`) y siempre acotadas al
gimnasio del usuario. Para `RutinaPlantilla` y `RutinaAsignada` (ambas
`TenantOwnedModel`) eso lo da `TenantScopedMixin.get_queryset()` de siempre.

Los items (`RutinaPlantillaItem`) NO son `TenantOwnedModel` -- no tienen
`for_gimnasio()` propio (ver docstring de `rutinas.models`). El aislamiento de
tenant para sus vistas se logra resolviendo PRIMERO la `RutinaPlantilla`
padre con `RutinaPlantilla.objects.for_gimnasio(gimnasio)`: si la plantilla es
de otro gimnasio, eso ya devuelve 404 antes de tocar ningún item.
`ItemPlantillaMixin` centraliza esa resolución.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from rutinas.forms import AsignarRutinaForm, RutinaPlantillaForm, RutinaPlantillaItemForm
from rutinas.models import RutinaAsignada, RutinaPlantilla, RutinaPlantillaItem
from tenants.mixins import StaffRequiredMixin


# ---------------------------------------------------------------------------
# Plantillas
# ---------------------------------------------------------------------------


class RutinaPlantillaListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = RutinaPlantilla
    template_name = "rutinas/plantilla_list.html"
    context_object_name = "plantillas"


class RutinaPlantillaCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = RutinaPlantilla
    form_class = RutinaPlantillaForm
    template_name = "rutinas/plantilla_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Plantilla creada correctamente.")
        return response

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.object.pk])


class RutinaPlantillaUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = RutinaPlantilla
    form_class = RutinaPlantillaForm
    template_name = "rutinas/plantilla_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Plantilla actualizada correctamente.")
        return response

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.object.pk])


class RutinaPlantillaDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    model = RutinaPlantilla
    template_name = "rutinas/plantilla_detail.html"
    context_object_name = "plantilla"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Ya vienen ordenados por dia/orden (Meta.ordering del modelo).
        context["items"] = self.object.items.select_related("ejercicio").all()
        return context


class RutinaPlantillaDuplicarView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """POST-only: crea una copia independiente de la plantilla (y sus items)
    vía `RutinaPlantilla.duplicar()`. GET no está permitido -- duplicar es una
    acción que muta datos, no una vista de solo lectura (ver ROADMAP Fase 2
    §4, "duplicar rutina existente")."""

    model = RutinaPlantilla

    def post(self, request, *args, **kwargs):
        plantilla = self.get_object()
        copia = plantilla.duplicar()
        messages.success(request, f'Se duplicó "{plantilla.nombre}".')
        return redirect("rutinas:plantilla_detalle", pk=copia.pk)


# ---------------------------------------------------------------------------
# Items de una plantilla
# ---------------------------------------------------------------------------


class ItemPlantillaMixin(StaffRequiredMixin, TenantScopedMixin):
    """Mixin común a los views de `RutinaPlantillaItem`.

    Resuelve la `RutinaPlantilla` padre (URL kwarg `plantilla_pk`) acotada al
    gimnasio del staff logueado -- ESE lookup es lo que aísla por tenant,
    porque 404ea antes de que se llegue a consultar ningún item. A partir de
    ahí se opera siempre sobre `self.plantilla.items`, nunca sobre
    `RutinaPlantillaItem.objects` directo (que no tiene scoping propio).
    """

    def get_plantilla(self):
        if not hasattr(self, "_plantilla"):
            self._plantilla = get_object_or_404(
                RutinaPlantilla.objects.for_gimnasio(self.gimnasio),
                pk=self.kwargs["plantilla_pk"],
            )
        return self._plantilla

    @property
    def plantilla(self):
        return self.get_plantilla()

    def get_queryset(self):
        # Reemplaza a TenantScopedMixin.get_queryset(): RutinaPlantillaItem no
        # es TenantOwnedModel y no tiene `for_gimnasio()`.
        return self.plantilla.items.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["plantilla"] = self.plantilla
        return context

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.plantilla.pk])


class RutinaPlantillaItemCreateView(ItemPlantillaMixin, CreateView):
    model = RutinaPlantillaItem
    form_class = RutinaPlantillaItemForm
    template_name = "rutinas/item_form.html"

    def form_valid(self, form):
        form.instance.rutina = self.plantilla
        self.object = form.save()
        messages.success(self.request, "Ejercicio agregado a la plantilla.")
        return redirect(self.get_success_url())


class RutinaPlantillaItemUpdateView(ItemPlantillaMixin, UpdateView):
    model = RutinaPlantillaItem
    form_class = RutinaPlantillaItemForm
    template_name = "rutinas/item_form.html"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Ejercicio actualizado.")
        return redirect(self.get_success_url())


class RutinaPlantillaItemDeleteView(ItemPlantillaMixin, View):
    """POST-only: no hay página de confirmación por GET, el botón de borrar
    ya es la confirmación (ver template `plantilla_detail.html`)."""

    def post(self, request, *args, **kwargs):
        item = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        item.delete()
        messages.success(request, "Ejercicio eliminado de la plantilla.")
        return redirect(self.get_success_url())


# ---------------------------------------------------------------------------
# Asignación de rutina
# ---------------------------------------------------------------------------


class AsignarRutinaView(StaffRequiredMixin, TenantScopedMixin, FormView):
    """`RutinaAsignada` no se crea vía `form.save()` (no es un `ModelForm`):
    la crea `RutinaAsignada.crear_desde_plantilla`. Igual se mezcla con
    `TenantScopedMixin` porque su `get_form_kwargs()` inyecta `gimnasio` sin
    importar qué tipo de form sea -- solo que no se usa su `get_queryset()`
    (no aplica a un `FormView`). `form_valid()` está completamente reescrito
    y no llama a `super()`, que asume un `ModelForm` con `form.instance`."""

    form_class = AsignarRutinaForm
    template_name = "rutinas/asignar_form.html"

    def get_initial(self):
        # Prefill opcional desde el link "Asignar a un alumno" del detalle de
        # una plantilla (?plantilla=<pk>) -- pura comodidad de UX, el form
        # sigue validando que la plantilla exista dentro del queryset scopeado.
        initial = super().get_initial()
        plantilla_pk = self.request.GET.get("plantilla")
        if plantilla_pk:
            initial["plantilla"] = plantilla_pk
        return initial

    def form_valid(self, form):
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=form.cleaned_data["alumno"],
            plantilla=form.cleaned_data["plantilla"],
            fecha_inicio=form.cleaned_data["fecha_inicio"],
        )
        messages.success(self.request, "Rutina asignada correctamente.")
        return redirect("rutinas:asignada_detalle", pk=asignada.pk)


class RutinaAsignadaDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    model = RutinaAsignada
    template_name = "rutinas/asignada_detail.html"
    context_object_name = "asignada"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["items"] = self.object.items.all()
        return context
