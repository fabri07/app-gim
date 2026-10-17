"""Vistas de la tienda: staff (gestión), alumno (compra) y la foto pública.

Los errores de dominio (`ErrorDeTienda`) se muestran con `messages.error` y
redirect: bajo `hx-boost` un 4xx es un click mudo.
"""

import mimetypes

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from tenants.mixins import StaffRequiredMixin
from tienda import services
from tienda.forms import (
    AjusteStockForm,
    CategoriaProductoForm,
    ProductoForm,
    VarianteFormSet,
)
from tienda.models import CategoriaProducto, ItemPedido, Producto, Variante


class TiendaHabilitadaMixin:
    """Cartel con 200 si el gimnasio no tiene el add-on (no 403: bajo boost
    sería un click mudo). Mismo molde que `tenants.mixins.BloqueadoEnCuentaDemoMixin`:
    va DESPUÉS de `StaffRequiredMixin` en las bases (el rol ya está validado
    cuando llega acá) y se cuida solo del anónimo y del usuario sin Perfil.
    `requiere_add_on = False` en las vistas de pedidos: con la tienda apagada
    el staff tiene que poder cerrar lo que quedó vivo."""

    requiere_add_on = True

    def dispatch(self, request, *args, **kwargs):
        if self.requiere_add_on and request.user.is_authenticated:
            try:
                gimnasio = request.user.perfil.gimnasio
            except ObjectDoesNotExist:
                gimnasio = None
            if gimnasio is not None and not gimnasio.puede_usar_tienda:
                return render(request, "tienda/no_habilitada.html")
        return super().dispatch(request, *args, **kwargs)


class TiendaStaffMixin(StaffRequiredMixin, TiendaHabilitadaMixin, TenantScopedMixin):
    pass


class CategoriaListView(TiendaStaffMixin, ListView):
    model = CategoriaProducto
    template_name = "tienda/categoria_list.html"
    context_object_name = "categorias"

    def get_queryset(self):
        return super().get_queryset().annotate(total_productos=Count("productos"))


class _CategoriaFormMixin:
    model = CategoriaProducto
    form_class = CategoriaProductoForm
    template_name = "tienda/categoria_form.html"
    success_url = reverse_lazy("tienda:categorias")

    def form_valid(self, form):
        messages.success(self.request, "Categoría guardada.")
        return super().form_valid(form)


# Alta y edición como hermanas sobre un mixin común, NO `Update(Create,
# UpdateView)`: con esa herencia `BaseCreateView.get/post` quedan antes en el
# MRO, ponen `self.object = None` y la «edición» crea un registro nuevo (y no
# da 404 para lo ajeno).
class CategoriaCreateView(TiendaStaffMixin, _CategoriaFormMixin, CreateView):
    pass


class CategoriaUpdateView(TiendaStaffMixin, _CategoriaFormMixin, UpdateView):
    pass


class CategoriaBorrarView(TiendaStaffMixin, BorrarConExplicacionView):
    model = CategoriaProducto

    def get_url_exito(self):
        return reverse("tienda:categorias")

    def get_mensaje_exito(self):
        return "Categoría eliminada. Sus productos quedaron sin categoría."


class ProductoListView(TiendaStaffMixin, ListView):
    model = Producto
    template_name = "tienda/producto_list.html"

    def get_queryset(self):
        return super().get_queryset().select_related("categoria").prefetch_related("variantes")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reservado = services.reservado_por_variante(self.gimnasio)
        # Sin filtro de template: cada variante viaja con su reservado. El
        # `prefetch_related` evita una query por producto.
        context["filas"] = [
            (producto, [(v, reservado.get(v.pk, 0)) for v in producto.variantes.all()])
            for producto in context["object_list"]
        ]
        context["umbral_pocas"] = services.UMBRAL_POCAS_UNIDADES
        return context


class _ProductoFormMixin:
    model = Producto
    form_class = ProductoForm
    template_name = "tienda/producto_form.html"
    success_url = reverse_lazy("tienda:productos")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "formset" not in context:
            context["formset"] = VarianteFormSet(
                self.request.POST or None, instance=self.object or Producto()
            )
        # Variantes que ya se vendieron: no se pueden borrar. Una query, no
        # una por fila del formset.
        context["con_ventas"] = (
            set(
                ItemPedido.objects.filter(variante__producto=self.object)
                .values_list("variante_id", flat=True)
            )
            if self.object
            else set()
        )
        return context

    def form_valid(self, form):
        form.instance.gimnasio = self.gimnasio
        formset = VarianteFormSet(self.request.POST, instance=form.instance)
        if not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        with transaction.atomic():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            # Las desactivadas en `_should_delete_form` (tenían ventas).
            for f in formset.forms:
                if f.instance.pk and f.instance.activa is False and f.cleaned_data.get("DELETE"):
                    f.instance.save(update_fields=["activa"])
        messages.success(self.request, "Producto guardado.")
        return redirect(self.success_url)


class ProductoCreateView(TiendaStaffMixin, _ProductoFormMixin, CreateView):
    pass


class ProductoUpdateView(TiendaStaffMixin, _ProductoFormMixin, UpdateView):
    pass


class ProductoBorrarView(TiendaStaffMixin, BorrarConExplicacionView):
    """`core/borrado.py` mira un solo nivel y prometería un borrado que la
    PROTECT de `ItemPedido→Variante` (dos niveles abajo) hace imposible."""

    model = Producto
    alternativa = "Destildá «Publicado» para sacarlo de la tienda sin perder su historial."

    def get_url_exito(self):
        return reverse("tienda:productos")

    def get_mensaje_exito(self):
        return "Producto eliminado."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if services.tiene_ventas(self.object):
            context["bloqueos"] = "pedidos de alumnos"
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if services.tiene_ventas(self.object):
            messages.error(request, "No se puede eliminar: tiene pedidos de alumnos. " + self.alternativa)
            return redirect(request.path)
        return super().post(request, *args, **kwargs)


class VarianteAjustarView(TiendaStaffMixin, View):
    def post(self, request, pk):
        variante = get_object_or_404(Variante, pk=pk, producto__gimnasio=self.gimnasio)
        form = AjusteStockForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Escribí un número entero, por ejemplo 10 o -1.")
        else:
            try:
                services.ajustar_stock(variante, form.cleaned_data["delta"])
                messages.success(request, f"{variante}: quedan {variante.stock} disponibles.")
            except services.ErrorDeTienda as error:
                messages.error(request, error.mensaje)
        return redirect("tienda:productos")


class FotoProductoView(View):
    """Pública y en `URLS_SIN_BLOQUEO` (sin cookie en una respuesta que se
    cachea un año). La foto de un suplemento no es dato sensible; no se exige
    `publicado` para que el staff vea sus borradores. A diferencia del logo
    (`ArchivoDeGimnasioView`, que aparece en todas las páginas), no se cachea
    el contenido en LocMem: son muchas fotos de hasta 1200 px por proceso, y
    con `immutable` el navegador ya no las vuelve a pedir."""

    def get(self, request, pk):
        producto = get_object_or_404(
            Producto.objects.filter(
                Q(gimnasio__es_demo=True) | Q(gimnasio__tienda_habilitada=True),
                gimnasio__activo=True,
            ),
            pk=pk,
        )
        if not producto.foto:
            raise Http404
        with producto.foto.open("rb") as archivo:
            contenido = archivo.read()
        tipo = mimetypes.guess_type(producto.foto.name)[0] or "image/jpeg"
        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Cache-Control"] = "public, max-age=31536000, immutable"
        return respuesta
