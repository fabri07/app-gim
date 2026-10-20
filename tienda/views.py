"""Vistas de la tienda: staff (gestión), alumno (compra) y la foto pública.

Los errores de dominio (`ErrorDeTienda`) se muestran con `messages.error` y
redirect: bajo `hx-boost` un 4xx es un click mudo.
"""

import logging
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import urlencode
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from pagos.models import MedioCobro
from plataforma.middleware import sin_acceso
from tenants import suplantacion
from tenants.models import Perfil
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from tienda import analitica, pagos_mp, services
from tienda.codigos import normalizar
from tienda.variantes import SUGERENCIAS
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
    CuentaMercadoPago,
    FotoProducto,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
    q_vendible,
)

logger = logging.getLogger(__name__)


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


LARGO_MAXIMO_BUSQUEDA = 60
MINIMO_BUSQUEDA_EN_CODIGO = 4


def _entero_positivo(valor):
    """`?ajustada=` viene de la URL: cualquier cosa rara se ignora, nunca un
    500. `int()` con `try` y no `isdigit()`: `"²".isdigit()` es `True`."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if 0 < numero < 2**63 else None


class ProductoListView(TiendaStaffMixin, ListView):
    """Lista con buscador. El buscador sirve también para el lector de código
    de barras (USB = teclado + Enter): un código exacto deja sólo su producto,
    marca la fila y pone el foco en su «Sumar o restar».

    Escaneo en serie: el ajuste hecho desde una búsqueda vuelve con
    `?ajustada=<pk>` (fila marcada, buscador vacío con el foco), así el
    próximo escaneo cae en el buscador y nunca en el input del ajuste, donde
    se sumaría el código como cantidad."""

    model = Producto
    template_name = "tienda/producto_list.html"

    def get_queryset(self):
        queryset = (
            super().get_queryset().select_related("categoria").prefetch_related("variantes", "fotos")
        )
        self.q = (self.request.GET.get("q") or "").strip()[:LARGO_MAXIMO_BUSQUEDA]
        self.destacadas = set()
        self.ajustada = _entero_positivo(self.request.GET.get("ajustada"))
        if not self.q:
            return queryset
        # `filter` y no `get`: sin constraint en la base (ver ISSUES.md), dos
        # variantes podrían compartir el código; se muestran todas.
        # Contra el código normalizado (mayúsculas, sin espacios): así se
        # guardan todos, y un código pegado con espacios también es exacto.
        exactas = list(
            Variante.objects.filter(producto__gimnasio=self.gimnasio, codigo=normalizar(self.q))
            .values_list("pk", "producto_id")
        )
        if exactas:
            self.destacadas = {pk for pk, _producto in exactas}
            return queryset.filter(pk__in={producto for _pk, producto in exactas})
        filtro = Q(nombre__icontains=self.q) | Q(variantes__nombre__icontains=self.q)
        # Dentro de los códigos sólo desde 4 caracteres (el mínimo de un
        # código): «20» o «0» están en todos los internos y traerían todo.
        if len(self.q) >= MINIMO_BUSQUEDA_EN_CODIGO:
            filtro |= Q(variantes__codigo__icontains=self.q)
        return queryset.filter(filtro).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reservado = services.reservado_por_variante(self.gimnasio)
        # Sin filtro de template: cada variante viaja con su reservado. El
        # `prefetch_related` evita una query por producto.
        context["filas"] = [
            (producto, [(v, reservado.get(v.pk, 0)) for v in producto.variantes.all()])
            for producto in context["object_list"]
        ]
        # El foco va a UN solo `[autofocus]` (htmx y el navegador enfocan el
        # primero): el ajuste de la primera variante del código escaneado, o
        # si no el buscador.
        context["foco_ajuste"] = next(
            (v.pk for _p, variantes in context["filas"] for v, _r in variantes if v.pk in self.destacadas),
            None,
        )
        context["q"] = self.q
        context["destacadas"] = self.destacadas
        context["marcadas"] = self.destacadas | ({self.ajustada} if self.ajustada else set())
        context["en_serie"] = bool(self.q or self.ajustada)
        context["umbral_pocas"] = services.UMBRAL_POCAS_UNIDADES
        return context


class _ProductoFormMixin:
    """Form del producto + formset de variantes, validados JUNTOS: los errores
    de los dos se ven en el primer intento (antes el formset sólo se validaba
    si el form pasaba), y el formset necesita las características ya
    validadas del form para saber qué reglas aplicar."""

    model = Producto
    form_class = ProductoForm
    template_name = "tienda/producto_form.html"
    success_url = reverse_lazy("tienda:productos")

    def _con_ventas(self):
        # Variantes que ya se vendieron: no se pueden borrar. Una query, no
        # una por fila del formset.
        if not self.object:
            return set()
        return set(
            ItemPedido.objects.filter(variante__producto=self.object)
            .values_list("variante_id", flat=True)
        )

    def _formset(self, data=None, form=None, con_ventas=None):
        """En el POST (`form` validado) las características salen de
        `form.cleaned_data`, y si alguna tiene error el formset no confía en
        ellas; las etiquetas, de lo que el staff tipeó. En el GET, de la
        instancia. `gimnasio` a cada fila: la unicidad del código de barras
        es por gimnasio."""
        if form is not None and form.is_bound:
            instance = form.instance
            validas = "caracteristica_1" not in form.errors and "caracteristica_2" not in form.errors
            caracteristicas = (
                (form.cleaned_data.get("caracteristica_1", ""), form.cleaned_data.get("caracteristica_2", ""))
                if validas else ("", "")
            )
            etiquetas = tuple(
                (form[nombre].value() or "").strip() for nombre in ("caracteristica_1", "caracteristica_2")
            )
        else:
            instance = self.object or Producto()
            validas = True
            # Del `initial` del form (si lo hay) y no de la instancia: el form
            # puede proponer «Opción» para un producto creado por la versión
            # vieja (ver `ProductoForm.__init__`).
            iniciales = form.initial if form is not None else {}
            caracteristicas = etiquetas = (
                iniciales.get("caracteristica_1", instance.caracteristica_1),
                iniciales.get("caracteristica_2", instance.caracteristica_2),
            )
        return VarianteFormSet(
            data, instance=instance,
            form_kwargs={"gimnasio": self.gimnasio},
            caracteristicas=caracteristicas, caracteristicas_validas=validas,
            etiquetas=etiquetas, con_ventas=con_ventas,
        )

    def post(self, request, *args, **kwargs):
        # Propio y explícito: el mixin va antes que `CreateView`/`UpdateView`
        # en el MRO y reemplaza su `post`. `get_object` ya filtra por
        # gimnasio (`TenantScopedMixin.get_queryset`): lo ajeno es 404.
        self.object = self.get_object() if "pk" in self.kwargs else None
        if "caracteristica_1" not in request.POST:
            # Una pestaña abierta antes del deploy postea el form viejo (sin
            # características ni valores): guardarlo dejaría el producto «sin
            # características». No se escribe nada; se redibuja lo guardado.
            messages.error(request, "La página quedó vieja: recargala y volvé a guardar.")
            kwargs = self.get_form_kwargs()
            kwargs.pop("data", None)
            kwargs.pop("files", None)
            form = self.get_form_class()(**kwargs)
            return self.render_to_response(self.get_context_data(form=form))
        form = self.get_form()
        form.instance.gimnasio = self.gimnasio
        form_ok = form.is_valid()
        con_ventas = self._con_ventas()
        formset = self._formset(request.POST, form=form, con_ventas=con_ventas)
        formset_ok = formset.is_valid()
        if form_ok and formset_ok:
            self._formset_valido = formset
            # Con la firma de siempre: en el MRO va primero
            # `TenantScopedMixin.form_valid` (estampa el gimnasio).
            return self.form_valid(form)
        return self.render_to_response(
            self.get_context_data(form=form, formset=formset, con_ventas=con_ventas)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "con_ventas" not in context:
            context["con_ventas"] = self._con_ventas()
        if "formset" not in context:
            context["formset"] = self._formset(form=context.get("form"), con_ventas=context["con_ventas"])
        context["sugerencias"] = SUGERENCIAS
        # El navegador nunca re-envía un `<input type=file>`: si la pantalla
        # se redibuja tras un POST con fotos elegidas (falló el form o las
        # variantes), hay que avisar que no se guardaron.
        context["fotos_no_guardadas"] = self.request.method == "POST" and bool(
            self.request.FILES.getlist("fotos_nuevas")
        )
        return context

    def form_valid(self, form):
        formset = self._formset_valido
        with transaction.atomic():
            self.object = form.save()
            form.guardar_fotos(self.object)
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
        crudo = normalizar(request.POST.get("delta"))
        if crudo and Variante.objects.filter(producto__gimnasio=self.gimnasio, codigo=crudo).exists():
            # El lector escaneó con el foco en «Sumar o restar»: un código corto
            # y numérico pasaría como cantidad. Los largos (EAN-13) ya los frena
            # el `max_value` del form.
            messages.error(request, "Eso parece un código de barras, no una cantidad: buscalo arriba.")
        elif not form.is_valid():
            messages.error(request, "Escribí un número entero, por ejemplo 10 o -1.")
        else:
            try:
                services.ajustar_stock(variante, form.cleaned_data["delta"])
                messages.success(request, f"{variante}: quedan {variante.stock} disponibles.")
            except services.ErrorDeTienda as error:
                messages.error(request, error.mensaje)
        if request.POST.get("desde_busqueda") == "1":
            # Escaneo en serie: ver `ProductoListView`.
            return redirect(reverse("tienda:productos") + "?" + urlencode({"ajustada": variante.pk}))
        return redirect("tienda:productos")


FILTROS_PEDIDOS = {
    "revisar": Q(estado=Pedido.Estado.EN_REVISION),
    "entregar": Q(estado=Pedido.Estado.PAGADO),
    "cancelaciones": services.Q_CANCELACION_PENDIENTE,
    "revisar_mp": services.Q_REVISAR_MP,
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
        # Con la bandera apagada la pestaña no existe (la tienda, idéntica a la
        # de transferencia), salvo que haya algo pendiente de cuando estaba
        # prendida.
        context["ver_revisar_mp"] = settings.TIENDA_MP_ENABLED or bool(
            context["contadores"]["a_revisar_mp"]
        )
        return context


class PedidoDetailView(TiendaStaffMixin, DetailView):
    requiere_add_on = False
    model = Pedido
    template_name = "tienda/pedido_detalle.html"

    def get(self, request, *args, **kwargs):
        # Perezoso, igual que el listado: sin esto el detalle mostraría
        # «Esperando el pago» sobre un pedido que ya venció.
        services.vencer_pedidos(gimnasio=self.gimnasio)
        return super().get(request, *args, **kwargs)

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
        # Con el link de MP pagable, ni «Marcar pagado» ni «Cancelar»: el pago
        # puede entrar en cualquier momento. Pasado `vence_preferencia`, sí.
        context["mp_en_curso"] = services.preferencia_vigente(pedido)
        # Cobrado por Mercado Pago: cancelarlo o aceptar el arrepentimiento NO
        # devuelve la plata sola (la devolución automática queda fuera de
        # esta versión): se le dice al staff que la devuelva desde MP.
        context["cobrado_por_mp"] = _cobrado_por_mp(pedido)
        context["pagos_adicionales"] = pagos_mp.adicionales_sin_resolver(pedido.respuesta_mp)
        return context


def _cobrado_por_mp(pedido):
    """Mercado Pago movió plata en este pedido (un pago rechazado anotado no
    cuenta: `marcar_pagado` lo conserva)."""
    return pedido.medio == Pedido.Medio.MERCADOPAGO and pedido.estado_mp in (
        pagos_mp.ESTADOS_MP_APROBADO | pagos_mp.ESTADOS_MP_DEVUELTO
    )


DEVOLVER_DESDE_MP = "Devolvé la plata desde tu cuenta de Mercado Pago."


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
            "entregar_igual": ("Pedido entregado.", None),
            "ya_devuelto": ("Pedido marcado como devuelto.", "devuelto_mp"),
            "duplicado_devuelto": ("Listo: el pago duplicado quedó marcado como devuelto.", None),
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
            elif accion == "entregar_igual":
                services.entregar_igual(pedido)
            elif accion == "ya_devuelto":
                services.marcar_devuelto(pedido)
            elif accion == "duplicado_devuelto":
                services.resolver_pago_duplicado(
                    pedido, payment_id=request.POST.get("payment_id", "")
                )
            else:
                raise services.ErrorDeTienda("estado_invalido")
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
        else:
            texto, aviso = avisos[accion]
            if (
                accion in ("cancelar", "aceptar_cancelacion")
                and pedido.estado == Pedido.Estado.DEVUELTO
                and _cobrado_por_mp(pedido)
            ):
                texto = f"{texto} {DEVOLVER_DESDE_MP}"
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
        # Con la bandera apagada la tienda no carga NI UNA instancia de la
        # cuenta: la tarjeta de Mercado Pago no existe.
        context["mp_habilitado"] = settings.TIENDA_MP_ENABLED
        context["mp_demo"] = self.gimnasio.es_demo
        if settings.TIENDA_MP_ENABLED and not self.gimnasio.es_demo:
            cuenta = (
                CuentaMercadoPago.objects.for_gimnasio(self.gimnasio)
                .select_related("conectada_por")
                .first()
            )
            context["cuenta_mp"] = cuenta
            conectada = cuenta is not None and cuenta.estado == CuentaMercadoPago.Estado.CONECTADA
            context["mp_conectada"] = conectada
            context["mp_reconectar"] = (
                cuenta is not None and cuenta.estado == CuentaMercadoPago.Estado.RECONECTAR
            )
            # Leído de la base, no del form (que en un POST inválido ya trae lo tipeado).
            context["mp_no_disponible"] = (
                not conectada and obtener_configuracion(self.gimnasio).acepta_mercadopago
            )
            if cuenta is not None and cuenta.conectada_por is not None:
                context["mp_conectada_por"] = (
                    cuenta.conectada_por.get_full_name() or cuenta.conectada_por.get_username()
                )
        return context

    def form_valid(self, form):
        messages.success(self.request, "Configuración guardada.")
        return super().form_valid(form)


class FotoProductoView(View):
    """Dos modos, según la moderación (ver `Producto.Moderacion`).

    **Público**: foto aprobada de un producto APROBADO. Es el de siempre: la
    ruta está en `URLS_SIN_BLOQUEO` (sin cookie en una respuesta que se cachea
    un año) y la vista no toca la sesión. No se exige `publicado`, para que el
    staff vea los borradores de lo ya aprobado. A diferencia del logo
    (`ArchivoDeGimnasioView`, que aparece en todas las páginas), no se cachea
    el contenido en LocMem: son muchas fotos de hasta 1200 px por proceso, y
    con `immutable` el navegador ya no las vuelve a pedir.

    **Privado**: cualquier otra (sin aprobar, o de un producto pendiente o
    bajado). La ven sólo el staff de ese gimnasio, si su cuenta tiene acceso, y
    el superadmin, con `private, no-store`: ninguna caché puede guardarla. Es
    el único camino que lee `request.user`. Para todos los demás es un 404 con
    `no-store`, así una caché intermedia no guarda el «no existe» y la foto
    sigue oculta después de aprobarla. Es `HttpResponseNotFound` y no `Http404`
    justamente para poder ponerle la cabecera."""

    def get(self, request, pk, foto_pk):
        foto = (
            FotoProducto.objects.filter(
                Q(producto__gimnasio__es_demo=True) | Q(producto__gimnasio__tienda_habilitada=True),
                producto__gimnasio__activo=True,
                pk=foto_pk,
                producto_id=pk,
            )
            .select_related("producto")
            .first()
        )
        if foto is None:
            return self._no_existe()
        if foto.aprobada and foto.producto.moderacion == Producto.Moderacion.APROBADO:
            cache = "public, max-age=31536000, immutable"
        elif self._puede_ver_sin_aprobar(request, foto):
            cache = "private, no-store"
        else:
            return self._no_existe()
        with foto.imagen.open("rb") as archivo:
            contenido = archivo.read()
        tipo = mimetypes.guess_type(foto.imagen.name)[0] or "image/jpeg"
        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Cache-Control"] = cache
        return respuesta

    @staticmethod
    def _no_existe():
        respuesta = HttpResponseNotFound()
        respuesta["Cache-Control"] = "no-store"
        return respuesta

    @staticmethod
    def _puede_ver_sin_aprobar(request, foto):
        usuario = request.user
        if not usuario.is_authenticated:
            return False
        if usuario.is_superuser:
            return usuario.is_active
        perfil = getattr(usuario, "perfil", None)
        return (
            perfil is not None
            and perfil.rol == Perfil.Rol.STAFF
            and perfil.gimnasio_id == foto.producto.gimnasio_id
            and not sin_acceso(perfil)
        )


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
            .filter(q_vendible())
            .select_related("categoria")
            .prefetch_related(
                models.Prefetch("variantes", queryset=Variante.objects.filter(activa=True)),
                # Sólo las fotos aprobadas: una sin aprobar no se ve en el catálogo.
                models.Prefetch("fotos", queryset=FotoProducto.objects.filter(aprobada=True)),
            )
            .order_by("categoria__orden", "categoria__nombre", "orden", "nombre")
        )
        grupos = {}
        for producto in productos:
            variantes = producto.variantes.all()  # prefetcheado: sin query
            if variantes:
                producto.hay_stock = any(v.stock > 0 for v in variantes)
                # Una sola variante activa y sin nombre: no hay nada que elegir.
                producto.variante_unica = (
                    variantes[0] if len(variantes) == 1 and not variantes[0].nombre else None
                )
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
        config = obtener_configuracion(self.gimnasio)
        context["config"] = config
        medios = services.medios_disponibles(self.gimnasio, config)
        # Con un solo medio el carrito es el de siempre (sin elección).
        context["elegir_medio"] = len(medios) > 1
        context["con_mercadopago"] = Pedido.Medio.MERCADOPAGO in medios
        context["con_transferencia"] = Pedido.Medio.TRANSFERENCIA in medios
        context["error_medio"] = self.request.GET.get("medio") == "invalido"
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
        medio = request.POST.get("medio", "")
        if not medio:
            # Con un solo medio el carrito no muestra elección (el de siempre).
            medios = services.medios_disponibles(self.gimnasio, obtener_configuracion(self.gimnasio))
            medio = medios[0] if len(medios) == 1 else ""
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
                medio=medio,
            )
        except services.ErrorDeTienda as error:
            if error.motivo == "medio_no_disponible":
                # El error va debajo de los radios (fieldset a mano).
                return redirect(reverse("tienda:carrito") + "?medio=invalido")
            messages.error(request, error.mensaje)
            return redirect("tienda:carrito")
        if pedido.medio == Pedido.Medio.MERCADOPAGO:
            return self._pagar_con_mercadopago(request, pedido)
        services.vaciar_carrito(request.session)
        from notificaciones import services as avisos_push

        transaction.on_commit(lambda: avisos_push.notificar_pedido_al_staff(pedido, "nuevo"))
        messages.success(request, "¡Pedido hecho! Transferí y subí el comprobante antes del plazo.")
        return redirect("tienda:mi_pedido", pk=pedido.pk)

    def _pagar_con_mercadopago(self, request, pedido):
        """Fuera del `atomic` de `crear_pedido`: la red nunca va con locks
        tomados. Si MP falla, el pedido ya está borrado y el carrito intacto.
        SIN push «Nuevo pedido»: llegaría por cada checkout abandonado; al
        staff le avisa el pago acreditado."""
        try:
            link = pagos_mp.crear_link_de_pago(
                pedido,
                url_retorno_para=lambda ref: request.build_absolute_uri(
                    reverse("tienda:mp_retorno", args=[ref])
                ),
                url_notificacion_para=lambda uuid_webhook: request.build_absolute_uri(
                    reverse("tienda:mp_webhook", args=[uuid_webhook])
                ),
            )
        except services.ErrorDeTienda as error:
            messages.error(request, error.mensaje)
            return redirect("tienda:carrito")
        services.vaciar_carrito(request.session)
        hora = timezone.localtime(pedido.vence_preferencia).strftime("%H:%M")
        # Se lee al volver de Mercado Pago (o si el alumno cierra la pestaña y
        # entra de nuevo a la tienda).
        messages.info(request, f"Pedido #{pedido.pk} reservado: pagalo con Mercado Pago antes de las {hora}.")
        return HttpResponseRedirect(link)


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
            es_mp=pedido.medio == Pedido.Medio.MERCADOPAGO,
            mp_en_curso=services.preferencia_vigente(pedido),
            mp_devuelto=pedido.estado_mp in ("refunded", "charged_back"),
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


@method_decorator(never_cache, name="dispatch")
class RetornoMercadoPagoView(TiendaAlumnoMixin, View):
    """Adonde vuelve el navegador desde Mercado Pago. Mismo acceso que «Mi
    pedido»: alumno dueño (404 si no), aunque la tienda esté apagada.

    El querystring es dato del cliente: el `status` se IGNORA, la única
    autoridad es leer el pago en la API (`acreditar_pedido`). Un error se
    loguea y se muestra «Estamos confirmando tu pago»: el webhook y
    `reconciliar_pedidos` lo acreditan igual. En la app instalada (iOS) la
    vuelta puede caer en el login: también acredita el webhook."""

    requiere_tienda_visible = False

    def get(self, request, *args, **kwargs):
        return self._cartel() or self._volver(request)

    def _volver(self, request):
        pedido = get_object_or_404(
            Pedido.objects.for_gimnasio(self.gimnasio).filter(
                alumno=self.alumno, medio=Pedido.Medio.MERCADOPAGO
            ),
            referencia_externa=self.kwargs["referencia"],
        )
        payment_id = next(
            (
                valor
                for valor in (request.GET.get("payment_id", ""), request.GET.get("collection_id", ""))
                # Mercado Pago manda el texto «null» cuando no hubo pago.
                if valor.strip() and valor.strip().lower() != "null"
            ),
            "",
        ).strip()
        if payment_id and settings.TIENDA_MP_ENABLED:
            cuenta = CuentaMercadoPago.objects.for_gimnasio(self.gimnasio).first()
            if cuenta is not None:
                try:
                    pagos_mp.acreditar_pedido(cuenta, payment_id)
                except Exception as exc:  # noqa: BLE001 - lo acredita el webhook
                    logger.warning(
                        "No se pudo acreditar al volver de Mercado Pago el pedido %s: %s",
                        pedido.pk, type(exc).__name__,
                    )
                    messages.info(request, "Estamos confirmando tu pago. Te avisamos cuando se acredite.")
        return redirect("tienda:mi_pedido", pk=pedido.pk)
