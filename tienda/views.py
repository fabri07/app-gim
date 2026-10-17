"""Vistas de la tienda: staff (gestión), alumno (compra) y la foto pública.

Los errores de dominio (`ErrorDeTienda`) se muestran con `messages.error` y
redirect: bajo `hx-boost` un 4xx es un click mudo.
"""

import mimetypes

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from pagos.models import MedioCobro
from tenants import suplantacion
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from tienda import analitica, services
from tienda.forms import (
    AjusteStockForm,
    CategoriaProductoForm,
    ComprobanteForm,
    ConfiguracionTiendaForm,
    MotivoForm,
    ProductoForm,
    VarianteFormSet,
)
from tienda.models import (
    CategoriaProducto,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
)


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


FILTROS_PEDIDOS = {
    "revisar": Q(estado=Pedido.Estado.EN_REVISION),
    "entregar": Q(estado=Pedido.Estado.PAGADO),
    "cancelaciones": services.Q_CANCELACION_PENDIENTE,
    "todos": Q(),
}


class PedidoListView(TiendaStaffMixin, ListView):
    requiere_add_on = False
    model = Pedido
    template_name = "tienda/pedido_list.html"
    paginate_by = 50

    def get(self, request, *args, **kwargs):
        services.vencer_pedidos(gimnasio=self.gimnasio)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        self.filtro = self.request.GET.get("filtro", "revisar")
        if self.filtro not in FILTROS_PEDIDOS:
            self.filtro = "revisar"
        return (
            super().get_queryset()
            .filter(FILTROS_PEDIDOS[self.filtro])
            .select_related("alumno")
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filtro"] = self.filtro
        context["contadores"] = services.contadores_panel(self.gimnasio)
        context["vendido_mes"] = analitica.resumen_del_mes(self.gimnasio)
        return context


class PedidoDetailView(TiendaStaffMixin, DetailView):
    requiere_add_on = False
    model = Pedido
    template_name = "tienda/pedido_detalle.html"

    def get_queryset(self):
        return super().get_queryset().select_related("alumno").prefetch_related("items")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_motivo"] = MotivoForm()
        pedido = self.object
        context["cancelacion_pendiente"] = (
            pedido.estado in (Pedido.Estado.PAGADO, Pedido.Estado.ENTREGADO)
            and pedido.cancelacion_pedida_en is not None
            and pedido.cancelacion_resuelta_en is None
        )
        return context


class PedidoAccionView(TiendaStaffMixin, View):
    requiere_add_on = False

    def post(self, request, pk):
        pedido = get_object_or_404(Pedido.objects.for_gimnasio(self.gimnasio), pk=pk)
        form = MotivoForm(request.POST)
        motivo = form.cleaned_data["motivo"] if form.is_valid() else request.POST.get("motivo", "")
        accion = request.POST.get("accion")
        avisos = {
            "pagado": ("Pago confirmado. Avisamos al alumno que puede retirar.", "confirmado"),
            "rechazar": (
                "Comprobante rechazado: el alumno puede subir otro y el stock sigue reservado.",
                "rechazado",
            ),
            "entregar": ("Pedido entregado.", None),
            "cancelar": ("Pedido cancelado; el stock volvió a la tienda.", "cancelado"),
            "aceptar_cancelacion": ("Cancelación aceptada.", "cancelacion_resuelta"),
            "rechazar_cancelacion": ("Cancelación rechazada.", "cancelacion_resuelta"),
        }
        try:
            if accion == "pagado":
                services.marcar_pagado(pedido)
            elif accion == "rechazar":
                services.rechazar_comprobante(pedido, motivo=motivo)
            elif accion == "entregar":
                services.entregar(pedido)
            elif accion == "cancelar":
                services.cancelar_por_staff(pedido, motivo=motivo)
            elif accion == "aceptar_cancelacion":
                services.resolver_cancelacion(pedido, aceptar=True, motivo=motivo)
            elif accion == "rechazar_cancelacion":
                services.resolver_cancelacion(pedido, aceptar=False, motivo=motivo)
            else:
                raise services.ErrorDeTienda("estado_invalido")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            texto, aviso = avisos[accion]
            messages.success(request, texto)
            if aviso:
                from notificaciones import services as avisos_push

                transaction.on_commit(lambda: avisos_push.notificar_pedido_al_alumno(pedido, aviso))
        return redirect("tienda:pedido_detalle", pk=pedido.pk)


class ConfiguracionView(TiendaStaffMixin, UpdateView):
    form_class = ConfiguracionTiendaForm
    template_name = "tienda/configuracion_form.html"
    success_url = reverse_lazy("tienda:configuracion")

    def get_object(self, queryset=None):
        return obtener_configuracion(self.gimnasio)

    def get_context_data(self, **kwargs):
        from pagos.models import MedioCobro

        context = super().get_context_data(**kwargs)
        context["medios_cobro"] = MedioCobro.objects.for_gimnasio(self.gimnasio).filter(activo=True)
        return context

    def form_valid(self, form):
        messages.success(self.request, "Configuración guardada.")
        return super().form_valid(form)


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


# --- Alumno ------------------------------------------------------------


class TiendaAlumnoMixin(AlumnoRequiredMixin):
    """Alumno + tienda visible. `requiere_tienda_visible = False` en «Mis
    pedidos» y su detalle: con la tienda apagada el alumno sigue viendo lo que
    compró. Sin ficha de `Alumno` vinculada, cartel con 200 (bajo boost un 403
    es un click mudo)."""

    requiere_tienda_visible = True

    def get(self, request, *args, **kwargs):
        if not hasattr(super(), "get"):  # vistas sólo-POST (checkout, acciones)
            return self.http_method_not_allowed(request, *args, **kwargs)
        return self._cartel() or super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not hasattr(self, "procesar"):  # vistas sólo-GET
            return self.http_method_not_allowed(request, *args, **kwargs)
        return self._cartel() or self.procesar(request, *args, **kwargs)

    def _cartel(self):
        if self.alumno is None:
            return render(self.request, "tienda/sin_ficha.html", status=200)
        if self.requiere_tienda_visible and not services.tienda_visible_para_alumnos(self.gimnasio):
            return render(self.request, "tienda/cerrada.html", status=200)
        services.vencer_pedidos(gimnasio=self.gimnasio)
        return None


class CatalogoView(TiendaAlumnoMixin, TemplateView):
    template_name = "tienda/catalogo.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        productos = (
            Producto.objects.for_gimnasio(self.gimnasio)
            .filter(publicado=True)
            .select_related("categoria")
            .prefetch_related(models.Prefetch("variantes", queryset=Variante.objects.filter(activa=True)))
            .order_by("categoria__orden", "categoria__nombre", "orden", "nombre")
        )
        grupos = {}
        for producto in productos:
            variantes = producto.variantes.all()  # prefetcheado: sin query
            if variantes:
                producto.hay_stock = any(v.stock > 0 for v in variantes)
                nombre = producto.categoria.nombre if producto.categoria else "Otros"
                grupos.setdefault(nombre, []).append(producto)
        context["grupos"] = list(grupos.items())
        context["unidades_en_carrito"] = sum(services.leer_carrito(self.request.session).values())
        context["umbral_pocas"] = services.UMBRAL_POCAS_UNIDADES
        return context


class CarritoView(TiendaAlumnoMixin, TemplateView):
    template_name = "tienda/carrito.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lineas"], context["total"] = services.lineas_del_carrito(
            self.request.session, gimnasio=self.gimnasio
        )
        context["config"] = obtener_configuracion(self.gimnasio)
        return context

    def procesar(self, request, *args, **kwargs):
        try:
            variante_id = int(request.POST.get("variante", ""))
            cantidad = int(request.POST.get("cantidad", "1"))
        except ValueError:
            messages.error(request, services.MENSAJES["cantidad_invalida"])
            return redirect("tienda:carrito")
        if request.POST.get("accion") == "quitar":
            services.quitar_del_carrito(request.session, variante_id)
            return redirect("tienda:carrito")
        try:
            services.agregar_al_carrito(
                request.session, gimnasio=self.gimnasio, variante_id=variante_id, cantidad=cantidad
            )
            messages.success(request, "Agregado al carrito.")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        destino = "tienda:catalogo" if request.POST.get("volver") == "catalogo" else "tienda:carrito"
        return redirect(destino)


class CheckoutView(TiendaAlumnoMixin, View):
    def procesar(self, request, *args, **kwargs):
        try:
            # Se compra lo que el alumno VE en el carrito (líneas saneadas), no
            # la sesión cruda: un producto archivado después de agregarlo no
            # puede trabar la compra del resto.
            lineas, _ = services.lineas_del_carrito(request.session, gimnasio=self.gimnasio)
            pedido = services.crear_pedido(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                carrito={l.variante.pk: l.cantidad for l in lineas},
                suplantando=suplantacion.esta_activa(request),
            )
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
            return redirect("tienda:carrito")
        services.vaciar_carrito(request.session)
        from notificaciones import services as avisos_push

        transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "nuevo"))
        messages.success(request, "¡Pedido hecho! Transferí y subí el comprobante antes del plazo.")
        return redirect("tienda:mi_pedido", pk=pedido.pk)


class MisPedidosView(TiendaAlumnoMixin, TemplateView):
    requiere_tienda_visible = False
    template_name = "tienda/mis_pedidos.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedidos = Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno)
        # «Botón de arrepentimiento» (Res. 424/2020): la misma pantalla, sólo
        # con lo que todavía se puede cancelar y el botón en cada fila.
        context["arrepentimiento"] = self.request.GET.get("arrepentimiento") == "1"
        if context["arrepentimiento"]:
            # Filtrado en la base y sin cortar: un `[:50]` antes de filtrar
            # podía dejar afuera un pedido cancelable. Son pocos por naturaleza
            # (pagados sin retirar + retirados en los últimos 10 días).
            context["pedidos"] = pedidos.filter(services.q_puede_pedir_cancelacion()).prefetch_related("items")
        else:
            context["pedidos"] = pedidos.prefetch_related("items")[:50]
        return context


class MiPedidoView(TiendaAlumnoMixin, TemplateView):
    requiere_tienda_visible = False
    template_name = "tienda/mi_pedido.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedido = get_object_or_404(
            Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno).prefetch_related("items"),
            pk=self.kwargs["pk"],
        )
        context.update(
            pedido=pedido,
            form=ComprobanteForm(),
            medios_cobro=MedioCobro.objects.for_gimnasio(self.gimnasio).filter(activo=True),
            config=obtener_configuracion(self.gimnasio),
            puede_pedir_cancelacion=services.puede_pedir_cancelacion(pedido),
        )
        return context


class _AccionSobreMiPedido(TiendaAlumnoMixin, View):
    requiere_tienda_visible = False

    def mi_pedido(self):
        return get_object_or_404(
            Pedido.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno), pk=self.kwargs["pk"]
        )


class MiPedidoComprobanteView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        pedido = self.mi_pedido()
        form = ComprobanteForm(request.POST, request.FILES)
        if not form.is_valid():
            for error in form.errors.get("comprobante", []):
                messages.error(request, error)
            return redirect("tienda:mi_pedido", pk=pk)
        try:
            if pedido.estado == Pedido.Estado.VENCIDO:
                # `_cartel` ya venció lo atrasado: subir tarde siempre lo
                # encuentra VENCIDO, y eso es un plazo vencido, no «cambió».
                raise services.ErrorDeTienda("plazo_vencido")
            services.subir_comprobante(pedido, alumno=self.alumno, archivo=form.cleaned_data["comprobante"])
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            from notificaciones import services as avisos_push

            transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "comprobante"))
            messages.success(request, "Recibimos tu comprobante. Te avisamos cuando el gimnasio lo confirme.")
        return redirect("tienda:mi_pedido", pk=pk)


class MiPedidoCancelarView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        try:
            services.cancelar_por_alumno(self.mi_pedido(), alumno=self.alumno)
            messages.success(request, "Pedido cancelado.")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        return redirect("tienda:mi_pedido", pk=pk)


class MiPedidoPedirCancelacionView(_AccionSobreMiPedido):
    def procesar(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        pedido = self.mi_pedido()
        try:
            services.pedir_cancelacion(pedido, alumno=self.alumno)
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            from notificaciones import services as avisos_push

            transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "cancelacion"))
            # El número de pedido es el código de la solicitud que pide la
            # Res. 424/2020; se muestra en el acto.
            messages.success(
                request,
                f"Solicitud registrada — pedido #{pedido.pk}. Le avisamos al gimnasio: "
                "te van a contactar para devolverte la plata.",
            )
        return redirect("tienda:mi_pedido", pk=pk)
