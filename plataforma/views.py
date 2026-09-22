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
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
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

    def get_form_kwargs(self):
        # El form necesita el gimnasio para poder avisar «ya hay un pago que
        # arranca ese día». No es el que se guarda: eso lo estampa
        # `form_valid` desde la URL.
        kwargs = super().get_form_kwargs()
        kwargs["gimnasio"] = self.get_gimnasio()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cotizacion"] = self._cotizacion()
        return context

    def _cotizacion(self):
        """La cotización del request, pedida UNA sola vez.

        **Solo en GET**: en POST los montos los manda el formulario y nadie
        los usa, así que pedirla sería salir a la red (hasta 3 s de espera,
        con un único worker de gunicorn) para tirar el resultado. Y si el
        formulario vuelve con errores, quien manda es lo que el superadmin ya
        tipeó, no un valor nuevo.
        """
        if self.request.method != "GET":
            return None
        if not hasattr(self, "_cotizacion_cache"):
            self._cotizacion_cache = cambio.cotizacion_dolar()
        return self._cotizacion_cache

    def get_initial(self):
        gimnasio = self.get_gimnasio()
        alumnos_activos = gimnasio.alumnos_activos
        monto_usd = precios.precio_usd(alumnos_activos)
        desde, hasta = facturacion.periodo_a_cobrar(gimnasio)
        cotizacion = self._cotizacion()
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

    def get_initial(self):
        """Un gimnasio con `facturacion_inicio` en `NULL` (uno nuevo: para él
        significa "desde la fecha de alta") llega con el campo vacío, y ahora
        es obligatorio. Se precarga con la fecha que YA está en efecto, así
        guardar sin tocar nada no cambia nada -- solo persiste explícitamente
        lo que el panel venía calculando. Sin esto, el superadmin tendría que
        adivinar una fecha para poder guardar el otro campo.
        """
        inicial = super().get_initial()
        inicial["facturacion_inicio"] = facturacion.inicio_efectivo(
            self.get_gimnasio()
        )
        return inicial

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Facturación actualizada.")
        return respuesta


class EstadoCuentaView(GimnasioDePlataformaMixin, TemplateView):
    """Congela o descongela la cuenta de un gimnasio.

    Las tres transiciones pasan por la misma vista porque son la misma
    escritura (`estado_cuenta` + desde cuándo) y separarlas en tres clases
    multiplicaría por tres el lugar donde olvidarse de limpiar la fecha.

    - GET de «Bloquear alumnos» / «Suspender»: pantalla de confirmación.
    - GET de «Restaurar»: 405. Restaurar no destruye nada, así que es un POST
      directo desde la ficha y no tiene pantalla propia; un GET ahí solo puede
      venir de una URL tipeada a mano o del prefetch de un navegador, y NO
      puede aplicar el cambio.
    - POST: aplica el estado.

    La escritura va con `QuerySet.update()` y no con `save()` a propósito:
    `modificado` (de `TimeStampedModel`) versiona la URL del logo y la del
    ícono de la PWA, así que tocarlo acá le invalidaría la caché del ícono a
    todos los celulares del gimnasio por un cambio que no tiene nada que ver
    con sus archivos.
    """

    template_name = "plataforma/confirmar_estado.html"

    #: Qué dice la pantalla de confirmación de cada estado. El texto describe
    #: la CONSECUENCIA (quién deja de entrar), no la acción: «¿Confirmás?» no
    #: le dice nada a quien está por dejar afuera a un gimnasio entero.
    COPY = {
        Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS: {
            "titulo": "Bloquear a los alumnos",
            "confirmar": "Sí, bloquear a los alumnos",
            "consecuencia": (
                "Los alumnos de este gimnasio dejan de ver su rutina y de "
                "reservar turnos, y dejan de recibir notificaciones. El staff "
                "sigue entrando normalmente para poder ponerse al día."
            ),
        },
        Gimnasio.EstadoCuenta.SUSPENDIDA: {
            "titulo": "Suspender la cuenta",
            "confirmar": "Sí, suspender la cuenta",
            "consecuencia": (
                "Nadie de este gimnasio entra más a la app: ni los alumnos ni "
                "el staff. Tampoco puede exportar sus datos desde la app (eso "
                "se hace con «manage.py exportar_gimnasio»)."
            ),
        },
    }

    @property
    def estado(self):
        """El estado pedido por la URL, validado contra las choices.

        Se resuelve al usarlo (y no en `dispatch`) para que la autorización
        del panel corra primero: quien no es el superadmin tiene que recibir
        403, no enterarse de qué estados existen probando la URL.
        """
        estado = self.kwargs["estado"]
        if estado not in Gimnasio.EstadoCuenta.values:
            raise Http404("Ese estado de cuenta no existe.")
        return estado

    def get(self, request, *args, **kwargs):
        if self.estado == Gimnasio.EstadoCuenta.NORMAL:
            return HttpResponseNotAllowed(["POST"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.COPY[self.estado])
        context["url_cancelar"] = self.get_success_url()
        return context

    def post(self, request, *args, **kwargs):
        gimnasio = self.get_gimnasio()
        estado = self.estado
        normal = estado == Gimnasio.EstadoCuenta.NORMAL
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            estado_cuenta=estado,
            # Se limpia al restaurar: una fecha colgada de una cuenta normal
            # se leería en la ficha como que sigue congelada.
            estado_cuenta_desde=None if normal else timezone.now(),
        )
        if normal:
            aviso = f"{gimnasio.nombre} vuelve a tener acceso completo."
        else:
            aviso = (
                f"{gimnasio.nombre}: "
                f"{Gimnasio.EstadoCuenta(estado).label.lower()}."
            )
        messages.success(request, aviso)
        return redirect(self.get_success_url())


class ExportacionToggleView(GimnasioDePlataformaMixin, View):
    """Prende y apaga el botón «Exportar mis datos» de un gimnasio.

    Es la casilla `exportacion_habilitada`, que hasta ahora solo se tildaba
    desde `/admin/`: el caso real es un gimnasio que deja de pagar y pide sus
    datos, o sea exactamente el momento en que el dueño del producto ya está
    mirando esta ficha.

    POST-only y con `update()`, mismo criterio que `EstadoCuentaView`: no toca
    `modificado`, que versiona el logo y el ícono de la PWA.
    """

    def post(self, request, *args, **kwargs):
        gimnasio = self.get_gimnasio()
        habilitada = not gimnasio.exportacion_habilitada
        Gimnasio.objects.filter(pk=gimnasio.pk).update(
            exportacion_habilitada=habilitada
        )
        messages.success(
            request,
            (
                f"{gimnasio.nombre} ya puede exportar sus datos."
                if habilitada
                else f"{gimnasio.nombre} ya no puede exportar sus datos."
            ),
        )
        return redirect(self.get_success_url())
