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


def _intento_abierto_de(gimnasio):
    """El link vivo que se le ofrece retomar, o `None`.

    **Sólo si es del período que el gimnasio debe HOY.** Un `CREADO` de un
    período ya cubierto (el gimnasio generó el link y después transfirió, y el
    superadmin lo cargó a mano) no se ofrece: la pantalla diría «al día» y
    «Continuar al pago» a la vez, y pagarlo sería un DUPLICADO con devolución
    manual. `plataforma` no conoce los intentos (no importa `suscripcion`),
    así que el pago manual no los cancela: el filtro vive acá. El pago online
    sí los cancela (`services._acreditar_aprobado`).

    **Y sólo si su tienda coincide con la de hoy.** El link lleva congelado el
    monto en pesos: si prendieron o apagaron la tienda después de generarlo,
    «Continuar al pago» cobraría USD 20 (o 200 en el anual) de menos o de más.
    `plataforma` no importa `suscripcion`, así que el toggle no puede cancelar
    el link; `crear_intento` lo cancela cuando genera el nuevo.
    """
    candidatos = IntentoDeCobro.objects.filter(
        gimnasio_id=gimnasio.pk,
        estado=_Estado.CREADO,
        expira__gt=timezone.now(),
    ).exclude(init_point="")
    for intento in candidatos:
        dias = (
            precios.DIAS_ANIO
            if intento.plan == IntentoDeCobro.Plan.ANUAL
            else precios.DIAS_CICLO
        )
        desde, _ = facturacion.periodo_a_cobrar(gimnasio, dias=dias)
        if (
            intento.periodo_desde == desde
            and intento.monto_tienda_usd == services.tienda_usd_para(gimnasio, intento.plan)
        ):
            return intento
    return None


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
    tienda = gimnasio.tienda_habilitada

    # Una sola llamada por request: la cotización está cacheada, pero aun así
    # dos lecturas podrían cruzar el vencimiento del caché y mostrar dos tipos
    # de cambio distintos en la misma pantalla.
    cotizacion = cambio.cotizacion_dolar()

    def plan(valor, etiqueta, monto_usd, dias, sin_descuento_usd=None):
        desde, hasta = facturacion.periodo_a_cobrar(gimnasio, dias=dias)
        return {
            "valor": valor,
            "etiqueta": etiqueta,
            "monto_usd": monto_usd,
            # Sólo el anual: lo que costarían los 12 meses de a uno, para
            # mostrar el ahorro real. El precio de lista de la portada NO se
            # tacha acá: ésta es la pantalla donde se le cobra.
            "sin_descuento_usd": sin_descuento_usd,
            "monto_ars": cambio.pesos(monto_usd, cotizacion),
            "desde": desde,
            "hasta": hasta,
        }

    planes = [
        plan(
            IntentoDeCobro.Plan.MENSUAL,
            "Mensual",
            precios.precio_usd(alumnos, tienda=tienda),
            precios.DIAS_CICLO,
        ),
        plan(
            IntentoDeCobro.Plan.ANUAL,
            "Anual",
            precios.precio_usd_anual(alumnos, tienda=tienda),
            precios.DIAS_ANIO,
            sin_descuento_usd=precios.precio_usd_anual_sin_descuento(alumnos, tienda=tienda),
        ),
    ]

    intento_abierto = _intento_abierto_de(gimnasio)

    return {
        "gimnasio": gimnasio,
        "fila": fila,
        "periodos_adeudados": periodos_adeudados(fila) if gimnasio.facturacion_aplica else 0,
        "planes": planes,
        "tramo": _tramo_de(alumnos),
        "tiene_tienda": tienda,
        "tienda_usd": precios.PRECIO_TIENDA_USD,
        "tienda_anual_usd": precios.precio_tienda_usd_anual(True),
        "meses_gratis_anual": precios.DESCUENTO_ANUAL_MESES,
        "cotizacion": cotizacion,
        "intento_abierto": intento_abierto,
        "integracion_activa": services.integracion_activa(),
        "es_demo": gimnasio.es_demo,
        "factura": gimnasio.facturacion_aplica,
        "hoy": timezone.localdate(),
        "form": form if form is not None else ElegirPlanForm(),
        "error": error,
    }


def _tramo_de(alumnos):
    """«hasta 100», «de 101 a 300» o «más de 300», leído de `ESCALONES`."""
    tope, _ = precios.escalon_de(alumnos)
    topes = [t for t, _ in precios.ESCALONES]
    anterior = topes[topes.index(tope) - 1] if topes.index(tope) else None
    if tope is None:
        return f"más de {anterior}"
    if anterior is None:
        return f"hasta {tope}"
    return f"de {anterior + 1} a {tope}"


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
        self.volvio_con_pago = bool(payment_id) and payment_id != "null"
        if self.volvio_con_pago and services.integracion_activa():
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
        elif not self.volvio_con_pago and not self.intento.payment_id:
            # Volvió SIN pagar («Volver al sitio» en Mercado Pago) y no hay
            # ningún pago anotado: decirle «si ya pagaste no vuelvas a pagar»
            # es falso y lo deja esperando algo que no va a llegar.
            situacion = "sin_pago"
        else:
            # CREADO y VENCIDO con un pago en camino: todavía no sabemos nada.
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
    Se evalúan en este orden:

    | Caso                                          | Respuesta | Por qué |
    |-----------------------------------------------|-----------|---------|
    | Integración apagada                           | 503 | MP reintenta cada 15 min hasta que se cargue la configuración; con 200 esas notificaciones se perderían. |
    | Tipo distinto de `payment` (con o sin firma)  | 200 | ANTES de la firma: no toca la base. Las IPN viejas y las de `merchant_order` pueden llegar sin firmar, y con 401 MP las reintentaría para siempre (ruido en el log). |
    | `payment` con firma inválida                  | 401 | Sin tocar la base. Un reintento no arregla una firma mal firmada; 401 en serie en el panel de MP = el secret está mal cargado. |
    | Sin `data.id`, pago desconocido o ya acreditado | 200 | Nada que hacer; el cortocircuito de `acreditar` lo hace barato. |
    | MP responde 404 al consultar el pago          | 200 | El pago no existe (p. ej. «simular notificación» del panel de MP): reintentar no lo hace aparecer, y con 500 se reintentaría para siempre. |
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

        # Antes de la firma: no toca la base ni la red (ver la tabla).
        if tipo != "payment":
            return JsonResponse({"ok": True, "accion": "ignorado"})

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

        if not data_id:
            return JsonResponse({"ok": True, "accion": "ignorado"})

        try:
            services.acreditar(data_id, url_panel_para=_url_panel_para(request))
        except ErrorDeMercadoPago as error:
            if error.codigo == 404:
                logger.warning(
                    "Webhook: Mercado Pago no encuentra el pago %s (404); lo "
                    "ignoro.",
                    data_id,
                )
                return JsonResponse({"ok": True, "accion": "pago inexistente"})
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
    `facturacion.gimnasios_anotados()`.

    Incluye los PAGADOS con un pago adicional anotado
    (`respuesta_mp["pagos_adicionales"]`, ver
    `services._registrar_pago_adicional`): es plata que hay que devolver y el
    estado del intento no lo dice. `has_key` anda igual en SQLite y Postgres.
    """

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
                | Q(respuesta_mp__has_key=services.CLAVE_PAGOS_ADICIONALES)
            )
            .select_related("gimnasio")
            .order_by("-creado", "-id")
        )
