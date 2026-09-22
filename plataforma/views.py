"""
Panel del dueño del producto: cómo viene la plataforma y a quién hay que
cobrarle.

Las vistas solo arman contexto: el precio, el estado de cobro y los KPIs los
calculan `plataforma.facturacion` y `plataforma.precios`, que se testean sin
pasar por acá.
"""

from django.views.generic import DetailView, TemplateView

from plataforma import facturacion
from plataforma.mixins import SuperadminRequiredMixin
from tenants.models import Gimnasio


class InicioView(SuperadminRequiredMixin, TemplateView):
    """Monitor de todos los gimnasios: KPIs, qué cobrar esta semana y la
    tabla completa.

    Es de SOLO LECTURA: registrar pagos, congelar cuentas y dar de alta
    gimnasios llegan en las fases siguientes.
    """

    template_name = "plataforma/inicio.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = facturacion.filas_del_monitor()
        context["filas"] = filas
        context["kpis"] = facturacion.kpis(filas)
        context["para_cobrar"] = facturacion.para_cobrar(filas)
        return context


class GimnasioDetalleView(SuperadminRequiredMixin, DetailView):
    """Ficha de plataforma de un gimnasio: lo mismo que su fila del monitor,
    con espacio para lo que traen las fases siguientes (pagos registrados,
    estado de la cuenta, actividad)."""

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
        context["fila"] = facturacion.fila_de_gimnasio(self.object)
        return context
