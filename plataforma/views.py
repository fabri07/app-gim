"""
Panel del dueño del producto: cómo viene la plataforma y a quién hay que
cobrarle.

Las vistas solo arman contexto y estampan lo que no puede venir del cliente
(el gimnasio de la URL, el usuario de la sesión): el precio, el estado de
cobro y los KPIs los calculan `plataforma.facturacion`, `plataforma.precios` y
`plataforma.cambio`, que se testean sin pasar por acá.

**La cotización se pide UNA vez por request** y se comparte entre los KPIs, la
ficha y el formulario de pago. Es una llamada HTTP a un tercero: pedirla por
fila sería el mismo error que un N+1, pero contra la red.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from plataforma import cambio, facturacion, precios
from plataforma.forms import FacturacionForm, PagoPlataformaForm
from plataforma.mixins import SuperadminRequiredMixin
from plataforma.models import PagoPlataforma
from tenants.models import Gimnasio


class InicioView(SuperadminRequiredMixin, TemplateView):
    """Monitor de todos los gimnasios: KPIs, qué cobrar esta semana y la
    tabla completa."""

    template_name = "plataforma/inicio.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = facturacion.filas_del_monitor()
        cotizacion = cambio.cotizacion_dolar()
        context["filas"] = filas
        context["cotizacion"] = cotizacion
        context["kpis"] = facturacion.kpis(filas, cotizacion=cotizacion)
        context["para_cobrar"] = facturacion.para_cobrar(filas)
        return context


class GimnasioDetalleView(SuperadminRequiredMixin, DetailView):
    """Ficha de plataforma de un gimnasio: su fila del monitor, la cotización
    del día y todo lo que ya pagó."""

    model = Gimnasio
    template_name = "plataforma/gimnasio_detalle.html"
    context_object_name = "gimnasio"

    def get_queryset(self):
        # El mismo queryset anotado del monitor: sin esto la ficha tendría que
        # recalcular los alumnos activos por su cuenta y podría no coincidir
        # con lo que muestra la tabla.
        return facturacion.gimnasios_anotados()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fila = facturacion.fila_de_gimnasio(self.object)
        cotizacion = cambio.cotizacion_dolar()
        context["fila"] = fila
        context["cotizacion"] = cotizacion
        context["precio_ars"] = cambio.pesos(fila.precio_usd, cotizacion)
        context["escalon"] = precios.escalon_de(fila.alumnos_activos)
        context["inicio_efectivo"] = facturacion.inicio_efectivo(self.object)
        context["fin_de_prueba"] = precios.fin_de_prueba(context["inicio_efectivo"])
        # Una sola query para toda la tabla: el orden ya lo pone
        # `PagoPlataforma.Meta.ordering` (del período más nuevo al más viejo).
        context["pagos"] = self.object.pagos_plataforma.all()
        return context


class GimnasioDePlataformaMixin(SuperadminRequiredMixin):
    """Resuelve el gimnasio de la URL (anotado) y sabe volver a su ficha.

    Lo comparten las dos vistas de escritura de la fase para que el gimnasio
    salga SIEMPRE de la URL y nunca del POST.
    """

    def get_gimnasio(self):
        if not hasattr(self, "_gimnasio"):
            self._gimnasio = get_object_or_404(
                facturacion.gimnasios_anotados(), pk=self.kwargs["pk"]
            )
        return self._gimnasio

    def get_success_url(self):
        return reverse(
            "plataforma:gimnasio_detalle", args=[self.get_gimnasio().pk]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gimnasio"] = self.get_gimnasio()
        return context


class PagoPlataformaCreateView(GimnasioDePlataformaMixin, CreateView):
    """Registrar un pago que ya se cobró.

    El formulario llega con TODO propuesto (el período que toca, los alumnos
    activos de hoy, el precio del escalón y su equivalente en pesos): el
    superadmin confirma o corrige, no calcula. Es el mismo criterio que el
    alta de cuotas dentro del gimnasio -- lo que el sistema puede deducir no
    se le pide a la persona.
    """

    model = PagoPlataforma
    form_class = PagoPlataformaForm
    template_name = "plataforma/pago_form.html"

    def get_initial(self):
        gimnasio = self.get_gimnasio()
        alumnos_activos = gimnasio.alumnos_activos
        monto_usd = precios.precio_usd(alumnos_activos)
        desde, hasta = facturacion.periodo_a_cobrar(gimnasio)
        cotizacion = cambio.cotizacion_dolar()
        inicial = {
            "fecha_pago": timezone.localdate(),
            "periodo_desde": desde,
            "periodo_hasta": hasta,
            "alumnos_activos": alumnos_activos,
            "monto_usd": monto_usd,
        }
        # Sin cotización los dos campos quedan vacíos en vez de en cero: un
        # pago con tipo de cambio 0 es un dato falso, y el superadmin puede
        # completarlos a mano mirando su homebanking.
        if cotizacion:
            inicial["tipo_cambio"] = cotizacion["venta"]
            inicial["monto_ars"] = cambio.pesos(monto_usd, cotizacion)
        return inicial

    def form_valid(self, form):
        # Estampado del lado del servidor, nunca del cliente: `gimnasio` sale
        # de la URL y `registrado_por` de la sesión.
        form.instance.gimnasio = self.get_gimnasio()
        form.instance.registrado_por = self.request.user
        respuesta = super().form_valid(form)
        messages.success(
            self.request,
            f"Pago registrado para {self.get_gimnasio().nombre}.",
        )
        return respuesta


class FacturacionUpdateView(GimnasioDePlataformaMixin, UpdateView):
    """Desde cuándo y si se le factura a un gimnasio."""

    model = Gimnasio
    form_class = FacturacionForm
    template_name = "plataforma/facturacion_form.html"

    def get_object(self, queryset=None):
        return self.get_gimnasio()

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Facturación actualizada.")
        return respuesta
