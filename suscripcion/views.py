"""
Las pantallas del cobro online y el webhook de Mercado Pago.

**Ninguna vista escribe nada**: todas llaman a `suscripcion.services`, que es
el único lugar que toca `IntentoDeCobro` y `PagoPlataforma`.

Dos públicos distintos en la misma app, y cada vista declara el suyo (lo fija
`tests_vistas.test_toda_vista_declara_su_publico`):

- el **staff** del gimnasio (`StaffRequiredMixin`): ver la suscripción,
  generar el link de pago y volver de Mercado Pago;
- **Mercado Pago** (`WebhookView`): no tiene sesión, se autentica por firma.

Con la cuenta congelada estas pantallas SIGUEN abriendo para el staff: es el
tercer nivel de allowlist de `plataforma/middleware.py`
(`APPS_SIN_BLOQUEO_CON_REGISTRO`). Es la pantalla donde el gimnasio paga para
salir del bloqueo.
"""

import json
import logging

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView, TemplateView

from plataforma import cambio, facturacion, precios
from plataforma.mixins import SuperadminRequiredMixin
from suscripcion import mercadopago, services
from suscripcion.forms import ElegirPlanForm
from suscripcion.mercadopago import ErrorDeMercadoPago
from suscripcion.models import IntentoDeCobro
from tenants.mixins import StaffRequiredMixin
from tenants.models import Gimnasio

logger = logging.getLogger(__name__)

_Estado = IntentoDeCobro.Estado


def _url_panel_para(request):
    """`url_panel_para(gimnasio)` para los mails al superadmin: el link a la
    ficha del gimnasio en el panel de plataforma. `services` no tiene
    `request`, así que la URL absoluta la arma quien lo tiene."""

    def url(gimnasio):
        return request.build_absolute_uri(
            reverse("plataforma:gimnasio_detalle", args=[gimnasio.pk])
        )

    return url


def _facturacion_de(request):
    """`(gimnasio anotado, fila)` del gimnasio del usuario: el ÚNICO lugar
    donde estas vistas leen el estado de cobro. Lo usan la pantalla de la
    suscripción y el retorno, que tienen que contar la misma historia.

    **El gimnasio sale de `facturacion.gimnasio_anotado`, nunca de
    `request.user.perfil.gimnasio`**: esa es una instancia pelada, sin
    `cubierto_hasta`, y con ella `periodo_a_cobrar` propone el primer período
    después de la prueba aunque el gimnasio ya haya pagado tres. Es el bug más
    fácil de reintroducir acá, y lo fija un test.
    """
    gimnasio = facturacion.gimnasio_anotado(request.user.perfil.gimnasio_id)
    return gimnasio, facturacion.fila_de_gimnasio(gimnasio)


def periodos_adeudados(fila):
    """Cuántos períodos mensuales hay que pagar para quedar al día: el que
    venció y sigue corriendo, más uno por cada ciclo entero ya pasado. 0 si no
    hay atraso. Es lo que le avisa al dueño que un solo pago puede no alcanzar.
    """
    if not fila.dias_de_atraso:
        return 0
    return fila.dias_de_atraso // precios.DIAS_CICLO + 1


def _contexto_suscripcion(request, *, form=None, error=None):
    """Todo lo que muestra `mi_suscripcion.html`. Lo comparten el GET de
    `MiSuscripcionView` y el re-render de `IniciarPagoView` cuando el link no
    se pudo generar, para que las dos pantallas no puedan divergir. El estado
    de cobro sale de `_facturacion_de`.
    """
    gimnasio, fila = _facturacion_de(request)
    alumnos = gimnasio.alumnos_activos

    # Una sola llamada por request: la cotización está cacheada, pero aun así
    # dos lecturas podrían cruzar el vencimiento del caché y mostrar dos tipos
    # de cambio distintos en la misma pantalla.
    cotizacion = cambio.cotizacion_dolar()

    def plan(valor, etiqueta, monto_usd, dias):
        desde, hasta = facturacion.periodo_a_cobrar(gimnasio, dias=dias)
        return {
            "valor": valor,
            "etiqueta": etiqueta,
            "monto_usd": monto_usd,
            "monto_ars": cambio.pesos(monto_usd, cotizacion),
            "desde": desde,
            "hasta": hasta,
        }

    planes = [
        plan(
            IntentoDeCobro.Plan.MENSUAL,
            "Mensual",
            precios.precio_usd(alumnos),
            precios.DIAS_CICLO,
        ),
        plan(
            IntentoDeCobro.Plan.ANUAL,
            "Anual",
            precios.precio_usd_anual(alumnos),
            precios.DIAS_ANIO,
        ),
    ]

    intento_abierto = IntentoDeCobro.objects.filter(
        gimnasio_id=gimnasio.pk,
        estado=_Estado.CREADO,
        expira__gt=timezone.now(),
    ).exclude(init_point="").first()

    return {
        "gimnasio": gimnasio,
        "fila": fila,
        "periodos_adeudados": periodos_adeudados(fila) if gimnasio.facturacion_aplica else 0,
        "planes": planes,
        "cotizacion": cotizacion,
        "intento_abierto": intento_abierto,
        "integracion_activa": services.integracion_activa(),
        "es_demo": gimnasio.es_demo,
        "factura": gimnasio.facturacion_aplica,
        "hoy": timezone.localdate(),
        "form": form if form is not None else ElegirPlanForm(),
        "error": error,
    }


@method_decorator(never_cache, name="dispatch")
class MiSuscripcionView(StaffRequiredMixin, TemplateView):
    """Estado de la suscripción del gimnasio y el formulario para pagarla.

    Todo caso raro (integración apagada, cuenta demo, gimnasio sin cobro) se
    explica con un **200**, nunca un 4xx: bajo el `hx-boost` global un 4xx es
    un click que no hace nada y no deja ningún mensaje.
    """

    template_name = "suscripcion/mi_suscripcion.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(_contexto_suscripcion(self.request))
        return contexto


@method_decorator(never_cache, name="dispatch")
class IniciarPagoView(StaffRequiredMixin, View):
    """Genera (o reusa) el link de pago y manda al gimnasio a Mercado Pago.

    El form que postea acá lleva `hx-boost="false"`: el redirect a
    mercadopago.com.ar es cross-origin, htmx no puede seguirlo y el click
    quedaría tragado sin error.
    """

    http_method_names = ["post"]
    template_name = "suscripcion/mi_suscripcion.html"

    def _render(self, request, form, error=None):
        return render(
            request,
            self.template_name,
            _contexto_suscripcion(request, form=form, error=error),
            # 200 y no 4xx: ver el docstring de `MiSuscripcionView`.
            status=200,
        )

    def post(self, request):
        form = ElegirPlanForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        try:
            intento = services.crear_intento(
                request.user.perfil.gimnasio_id,
                form.cleaned_data["plan"],
                url_retorno_para=lambda ref: request.build_absolute_uri(
                    reverse("suscripcion:retorno", args=[ref])
                ),
                url_notificacion=request.build_absolute_uri(
                    reverse("suscripcion:webhook")
                ),
                email_de_quien_pago=request.user.email,
            )
        except services.CobroNoDisponible as exc:
            return self._render(request, form, error=exc.mensaje)
        return HttpResponseRedirect(intento.init_point)


@method_decorator(never_cache, name="dispatch")
class RetornoView(StaffRequiredMixin, TemplateView):
    """Adonde vuelve el navegador desde Mercado Pago.

    La referencia de la URL y todo el querystring son **dato del cliente**:
    con un UUID no se adivina la referencia del vecino, y aun así se valida
    que el intento sea del gimnasio del usuario (404 si no). El `status` que
    trae el querystring se IGNORA: la única autoridad sobre un pago es la
    consulta a la API que hace `services.acreditar`.

    Un `ErrorDeMercadoPago` acá no es un error en pantalla: el intento queda
    como estaba y se muestra «estamos confirmando tu pago». El webhook y la
    reconciliación lo levantan después. Un lookup fallido no puede parecerse a
    un pago fallido.
    """

    template_name = "suscripcion/retorno.html"

    def get(self, request, *args, **kwargs):
        self.intento = get_object_or_404(
            IntentoDeCobro,
            referencia_externa=kwargs["referencia"],
            gimnasio_id=request.user.perfil.gimnasio_id,
        )
        payment_id = request.GET.get("payment_id") or request.GET.get(
            "collection_id"
        )
        # Mercado Pago manda el literal "null" cuando no hubo pago.
        if payment_id and payment_id != "null" and services.integracion_activa():
            try:
                services.acreditar(payment_id, url_panel_para=_url_panel_para(request))
            except ErrorDeMercadoPago:
                logger.warning(
                    "No se pudo confirmar el pago %s desde el retorno; lo "
                    "levantan el webhook o la reconciliación.",
                    payment_id,
                )
            self.intento.refresh_from_db()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        estado = self.intento.estado
        extra = {}
        if estado == _Estado.PAGADO:
            # Pagado NO es «al día»: un gimnasio con varios períodos vencidos
            # paga el más viejo, y `services.acreditar` sólo descongela si con
            # eso queda cubierto hasta hoy. El mensaje sale del estado de cobro
            # recién leído, no del intento: «¡Listo!» con una fecha pasada y un
            # «Ir al inicio» que termina en el cartel es peor que no decir nada.
            gimnasio, fila = _facturacion_de(self.request)
            normal = gimnasio.estado_cuenta == Gimnasio.EstadoCuenta.NORMAL
            if fila.estado == precios.EstadoPago.VENCIDA or not normal:
                situacion = "pagado_con_deuda"
                extra = {"cuenta_congelada": not normal}
            else:
                situacion = "pagado"
        elif estado == _Estado.RECHAZADO:
            situacion = "rechazado"
        elif estado in (_Estado.REVISAR, _Estado.DUPLICADO):
            situacion = "revisando"
        elif estado == _Estado.DEVUELTO:
            situacion = "devuelto"
        elif estado == _Estado.CANCELADO:
            situacion = "cancelado"
        else:
            # CREADO y VENCIDO: todavía no sabemos nada del pago.
            situacion = "confirmando"
        contexto.update({"intento": self.intento, "situacion": situacion, **extra})
        return contexto


def _cuerpo_tolerante(crudo):
    """El cuerpo del webhook como dict, o `{}`. Nunca levanta: un JSON
    inválido, uno que no es un objeto o uno anidado de más (`RecursionError`)
    son todos «sin cuerpo», no un 500."""
    try:
        cuerpo = json.loads(crudo or b"{}")
    except (ValueError, RecursionError):
        # `JSONDecodeError` y `UnicodeDecodeError` son `ValueError`.
        return {}
    return cuerpo if isinstance(cuerpo, dict) else {}


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """La notificación de Mercado Pago de que un pago cambió.

    `csrf_exempt` porque Mercado Pago no tiene sesión ni cookie: se autentica
    por la firma `x-signature`. **La vista no mira `request.user` nunca**, y
    eso es lo que hace seguro el `csrf_exempt` (un form cross-site montado
    sobre la sesión de un staff no consigue nada que un anónimo no consiga).
    Hay un test que lo fija. Tampoco depende del tercer nivel de allowlist de
    `PlataformaMiddleware`: el request es anónimo y el middleware corta antes.

    Las respuestas no son «200 siempre»: eso tiraría el único mecanismo de
    recuperación bueno que hay.

    | Caso                                          | Respuesta | Por qué |
    |-----------------------------------------------|-----------|---------|
    | Integración apagada                           | 503 | MP reintenta cada 15 min hasta que se cargue la configuración; con 200 esas notificaciones se perderían. |
    | Firma inválida                                | 401 | Sin tocar la base. Un reintento no arregla una firma mal firmada; 401 en serie en el panel de MP = el secret está mal cargado. |
    | Tipo distinto de `payment`, cuerpo inválido, pago desconocido o ya acreditado | 200 | Nada que hacer; el cortocircuito de `acreditar` lo hace barato. |
    | `ErrorDeMercadoPago` (MP no contestó)         | 500 | A propósito: MP reintenta a los 15 min, muchísimo mejor que el cron (2-4 h tarde). |

    **Pendiente de confirmar en el sandbox**: que las notificaciones que llegan
    por la `notification_url` de la preferencia vienen firmadas. Si llegaran
    sin firma se responderían 401, MP reintentaría, y la reconciliación las
    levantaría igual (más tarde).
    """

    http_method_names = ["post"]

    def post(self, request):
        if not services.integracion_activa():
            return JsonResponse({"error": "integracion apagada"}, status=503)

        cuerpo = _cuerpo_tolerante(request.body)
        data_id = request.GET.get("data.id") or request.GET.get("id")
        if not data_id:
            datos = cuerpo.get("data")
            if isinstance(datos, dict) and datos.get("id") is not None:
                data_id = str(datos["id"])
        tipo = (
            request.GET.get("type") or request.GET.get("topic") or cuerpo.get("type")
        )

        if not mercadopago.firma_valida(
            settings.MERCADOPAGO_WEBHOOK_SECRET,
            data_id=data_id,
            request_id=request.headers.get("x-request-id", ""),
            x_signature=request.headers.get("x-signature", ""),
        ):
            # Sin el header ni el secret: sólo lo que sirve para diagnosticar.
            logger.warning(
                "Webhook de Mercado Pago con firma inválida (data.id=%s).", data_id
            )
            return JsonResponse({"error": "firma invalida"}, status=401)

        if tipo != "payment" or not data_id:
            return JsonResponse({"ok": True, "accion": "ignorado"})

        try:
            services.acreditar(data_id, url_panel_para=_url_panel_para(request))
        except ErrorDeMercadoPago:
            logger.warning(
                "Webhook: Mercado Pago no respondió al consultar el pago %s; "
                "respondo 500 para que reintente.",
                data_id,
            )
            return JsonResponse({"error": "reintentar"}, status=500)
        return JsonResponse({"ok": True})


class CobrosConProblemaView(SuperadminRequiredMixin, ListView):
    """Los intentos que necesitan una acción humana (devolver plata, revisar un
    monto), que hasta acá sólo llegaban por mail. Una sola query, sin tocar
    `facturacion.gimnasios_anotados()`."""

    template_name = "suscripcion/cobros_con_problema.html"
    context_object_name = "intentos"
    DIAS_DE_RECHAZADOS = 30

    def get_queryset(self):
        Estado = IntentoDeCobro.Estado
        desde = timezone.now() - timedelta(days=self.DIAS_DE_RECHAZADOS)
        return (
            IntentoDeCobro.objects.filter(
                Q(estado__in=[Estado.REVISAR, Estado.DUPLICADO, Estado.DEVUELTO])
                | Q(estado=Estado.RECHAZADO, creado__gte=desde)
            )
            .select_related("gimnasio")
            .order_by("-creado", "-id")
        )
