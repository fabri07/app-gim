"""
El camino de la plata del cobro online — ÚNICO lugar que escribe sobre
`IntentoDeCobro` y único que crea un `PagoPlataforma` con `origen=ONLINE`.

Las vistas (retorno del navegador, webhook) y los comandos (vencer,
reconciliar) sólo llaman a estas funciones. Molde: `solicitudes/services.py`
(transiciones idempotentes con `select_for_update()` + re-chequeo del estado, y
mails por `transaction.on_commit` con los valores ya resueltos). Las URLs
absolutas las arma quien llama y llegan como callables: este módulo no tiene
`request`, y el cron tampoco.

**`acreditar` es el único punto de entrada para acreditar**, y lo llaman tres
caminos que compiten entre sí (el webhook, el retorno del navegador y la
reconciliación). Tiene que llegar al mismo estado final sin importar quién
llegue primero: por eso el estado del pago sale SIEMPRE de consultar la API, y
el cortocircuito de idempotencia es sobre `(payment_id, estado_mp)`.
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plataforma import cambio, facturacion, precios
from plataforma.models import PagoPlataforma
from suscripcion import emails, mercadopago
from suscripcion.mercadopago import ErrorDeMercadoPago
from suscripcion.models import IntentoDeCobro
from tenants.models import Gimnasio

logger = logging.getLogger(__name__)

_Estado = IntentoDeCobro.Estado
_Plan = IntentoDeCobro.Plan

#: Cuánto vive un link de pago. 48 y no 24 porque el aviso que llega un
#: viernes a la noche tiene que poder pagarse el domingo; y no una semana
#: porque el tipo de cambio queda congelado en el link: una semana de
#: inflación argentina es un monto que ya no es el que corresponde.
HORAS_VALIDEZ_LINK = 48

#: Diferencia máxima entre lo que Mercado Pago dice que se cobró y lo que el
#: intento congeló. Un centavo: el redondeo de `float` en la preferencia.
TOLERANCIA_MONTO = Decimal("0.01")

#: Hasta cuántos días atrás la reconciliación busca intentos sin pago.
DIAS_RECONCILIACION = 7

ESTADOS_MP_APROBADO = frozenset({"approved"})
ESTADOS_MP_RECHAZADO = frozenset({"rejected", "cancelled"})
ESTADOS_MP_DEVUELTO = frozenset({"refunded", "charged_back"})

#: Intentos cuyo pago anterior (si lo hubo) NO movió plata: un pago nuevo
#: aprobado sobre ellos se acredita. **`VENCIDO` y `CANCELADO` van a
#: propósito**: la plata en la cuenta le gana a nuestro reloj. El link vence
#: a las 48 h según NUESTRO criterio, pero Mercado Pago puede aprobar un pago
#: iniciado un minuto antes, o el gimnasio pudo pagar un link que después
#: canceló al cambiar de plan. Un `filter(estado=CREADO)` acá se come un pago
#: real: el gimnasio pagó y nadie lo anota.
_ACREDITABLES = frozenset(
    {_Estado.CREADO, _Estado.VENCIDO, _Estado.CANCELADO, _Estado.RECHAZADO}
)

#: Intentos cuyo pago anterior SÍ movió plata (se acreditó, o se frenó con la
#: plata adentro para que la mire un humano). Un pago distinto sobre ellos no
#: pisa nada.
_CON_PLATA = frozenset(
    {_Estado.PAGADO, _Estado.DUPLICADO, _Estado.REVISAR, _Estado.DEVUELTO}
)


class CobroNoDisponible(Exception):
    """No se pudo generar el link de pago. `motivo` es una clave de
    `MENSAJES`, que es lo que ve el gimnasio en pantalla."""

    def __init__(self, motivo):
        self.motivo = motivo
        super().__init__(motivo)

    @property
    def mensaje(self):
        return MENSAJES[self.motivo]


MENSAJES = {
    "integracion_apagada": (
        "El pago online no está disponible en este momento. Escribinos y lo "
        "resolvemos por transferencia."
    ),
    "no_aplica": "A tu cuenta no se le cobra la suscripción: no tenés nada que pagar.",
    "sin_cotizacion": (
        "Ahora mismo no podemos calcular el monto en pesos. Probá de nuevo en "
        "unos minutos."
    ),
    "mercadopago": (
        "Mercado Pago no respondió. No se te cobró nada: probá de nuevo en "
        "unos minutos."
    ),
}


def integracion_activa():
    return bool(settings.COBRO_ONLINE_ENABLED)


def vencer_intentos_abiertos(gimnasio, *, ahora):
    """Pasa a `VENCIDO` los links `CREADO` de ese gimnasio que ya expiraron.
    Devuelve cuántos.

    **Vencer es perezoso**: lo llama `crear_intento` en la misma transacción,
    antes de insertar. Con el índice único parcial (un `CREADO` por período),
    un `CREADO` eterno bloquearía que el gimnasio genere un link nuevo, y el
    cron de GitHub Actions llega 2-4 h tarde en el plan free: vencer no puede
    depender de él.

    `modificado` va explícito porque `QuerySet.update()` no dispara
    `auto_now`.
    """
    return IntentoDeCobro.objects.filter(
        gimnasio=gimnasio, estado=_Estado.CREADO, expira__lt=ahora
    ).update(estado=_Estado.VENCIDO, modificado=ahora)


def crear_intento(
    gimnasio_pk, plan, *, url_retorno_para, url_notificacion, email_de_quien_pago=""
):
    """Genera (o reusa) el link de pago del próximo período del gimnasio.

    `url_retorno_para(referencia_externa) -> str` y `url_notificacion` son
    absolutas y las arma la vista, que tiene el `request`. Levanta
    `CobroNoDisponible` con el motivo; en ese caso no queda ningún intento
    nuevo en la base.
    """
    if not integracion_activa():
        raise CobroNoDisponible("integracion_apagada")

    ahora = timezone.now()
    with transaction.atomic():
        # Serializa dos clicks concurrentes del mismo gimnasio: el segundo
        # espera acá y después encuentra el `CREADO` del primero.
        Gimnasio.objects.select_for_update().get(pk=gimnasio_pk)
        gimnasio = facturacion.gimnasio_anotado(gimnasio_pk)
        facturacion.exigir_anotado(gimnasio)

        if not gimnasio.facturacion_aplica:
            raise CobroNoDisponible("no_aplica")

        vencer_intentos_abiertos(gimnasio, ahora=ahora)

        dias = precios.DIAS_ANIO if plan == _Plan.ANUAL else precios.DIAS_CICLO
        desde, hasta = facturacion.periodo_a_cobrar(gimnasio, dias=dias)

        abierto = IntentoDeCobro.objects.filter(
            gimnasio=gimnasio, estado=_Estado.CREADO, periodo_desde=desde
        ).first()
        if abierto is not None:
            if abierto.plan == plan:
                # El doble click (o volver a la pantalla) reusa el link vivo:
                # no se genera un segundo cobro del mismo período, y dos
                # pantallas no pueden mostrar dos tipos de cambio distintos.
                return abierto
            # Cambió de plan: el link anterior deja de valer. Si igual lo
            # paga, `acreditar` lo acredita (CANCELADO es acreditable) o lo
            # frena por solapamiento.
            IntentoDeCobro.objects.filter(pk=abierto.pk).update(
                estado=_Estado.CANCELADO, modificado=ahora
            )

        cotizacion = cambio.cotizacion_dolar()
        if cotizacion is None:
            # Que un cliente no haya podido pagar es un evento que se quiere
            # ver. Nunca un monto en 0 ni un tipo de cambio inventado.
            logger.warning(
                "Cobro online sin cotización del dólar para el gimnasio %s",
                gimnasio.slug,
            )
            raise CobroNoDisponible("sin_cotizacion")

        alumnos = gimnasio.alumnos_activos
        if plan == _Plan.ANUAL:
            monto_usd = precios.precio_usd_anual(alumnos)
        else:
            monto_usd = precios.precio_usd(alumnos)
        monto_ars = cambio.pesos(monto_usd, cotizacion)

        intento = IntentoDeCobro.objects.create(
            gimnasio=gimnasio,
            plan=plan,
            periodo_desde=desde,
            periodo_hasta=hasta,
            monto_usd=monto_usd,
            monto_ars=monto_ars,
            tipo_cambio=cotizacion["venta"],
            alumnos_activos=alumnos,
            expira=ahora + timedelta(hours=HORAS_VALIDEZ_LINK),
            email_de_quien_pago=email_de_quien_pago,
        )

        try:
            preferencia = mercadopago.crear_preferencia(
                titulo=(
                    f"TuGimApp — plan {plan} del {desde:%d/%m/%Y} al "
                    f"{hasta:%d/%m/%Y}"
                ),
                monto_ars=monto_ars,
                referencia_externa=str(intento.referencia_externa),
                url_retorno=url_retorno_para(intento.referencia_externa),
                url_notificacion=url_notificacion,
                expira=intento.expira,
                email_pagador=email_de_quien_pago,
                ahora=ahora,
            )
        except ErrorDeMercadoPago:
            # Se levanta ADENTRO del `atomic`: el rollback se lleva el intento
            # recién creado, así no queda uno huérfano sin link que además
            # ocuparía el índice de «un CREADO por período».
            raise CobroNoDisponible("mercadopago") from None

        intento.preference_id = preferencia["id"]
        intento.init_point = preferencia["init_point"]
        intento.save(update_fields=["preference_id", "init_point", "modificado"])
    return intento


def _fecha_local(texto):
    """La fecha LOCAL de un `date_approved` de Mercado Pago (ISO con offset).
    Sin dato, o si no parsea, `timezone.localdate()`."""
    try:
        instante = parse_datetime(texto) if texto else None
    except (TypeError, ValueError):
        instante = None
    if instante is None:
        return timezone.localdate()
    if timezone.is_naive(instante):
        return instante.date()
    return timezone.localtime(instante).date()


def _descongelar_si_corresponde(gimnasio_pk):
    """Devuelve `(descongelado, sigue_debiendo_desde)`.

    Se llama sólo desde `acreditar`, dentro de la misma transacción y recién
    creado el `PagoPlataforma`, así que tres de las cuatro condiciones ya se
    cumplen por construcción: (a) el status aprobado vino de consultar la API,
    no del cuerpo de un webhook; (b) el monto coincidió; (c) el pago se creó en
    esta transacción. La cuarta se chequea acá: (d) recalculado desde la base,
    el gimnasio queda cubierto HOY (`cubierto_hasta >= timezone.localdate()`).
    Quien debe tres meses y paga uno no se descongela: sigue debiendo desde
    `cubierto_hasta + 1 día`, y eso dice el mail.

    **Esto no viola la regla de `Gimnasio.EstadoCuenta`** («los escalones son
    MANUALES, nunca un cron»), aunque lo parezca. Esa regla está escrita entera
    sobre el peligro de CORTAR automático: un corte el día equivocado le rompe
    el día a alguien que quizás ya transfirió. Restaurar tiene el riesgo
    inverso: dejar afuera, un fin de semana, a un gimnasio que YA pagó. La
    regla es **«restauración automática, corte nunca»**; no la «arregles»
    borrando esto.

    `estado_cuenta` y `estado_cuenta_desde` van en el MISMO `update()`: una
    fecha vieja sobre una cuenta normal se lee como que sigue congelada. Y es
    `update()` y no `save()` porque `Gimnasio.modificado` versiona el logo, el
    fondo y el ícono PWA (`version_media`).

    **Riesgo aceptado:** `estado_cuenta` no guarda el motivo, así que un
    gimnasio congelado por otra razón que no sea la deuda se descongela con un
    pago que lo deja al día. El arreglo, si hace falta, es un campo `motivo`,
    no un caso especial acá.
    """
    cubierto_hasta = PagoPlataforma.objects.filter(gimnasio_id=gimnasio_pk).aggregate(
        ultimo=Max("periodo_hasta")
    )["ultimo"]
    if cubierto_hasta is None or cubierto_hasta < timezone.localdate():
        desde = cubierto_hasta + timedelta(days=1) if cubierto_hasta else None
        return False, desde
    descongelados = (
        Gimnasio.objects.filter(pk=gimnasio_pk)
        .exclude(estado_cuenta=Gimnasio.EstadoCuenta.NORMAL)
        .update(estado_cuenta=Gimnasio.EstadoCuenta.NORMAL, estado_cuenta_desde=None)
    )
    return descongelados > 0, None


def _avisar_problema(intento, problema, url_panel, pago_adicional_id=""):
    transaction.on_commit(
        lambda: emails.enviar_cobro_con_problema(
            intento,
            problema=problema,
            url_panel=url_panel,
            pago_adicional_id=pago_adicional_id,
        )
    )


def _monto_coincide(pago, intento):
    """Se VALIDA el monto de Mercado Pago, nunca se escribe: lo que queda
    registrado es lo que el intento congeló.

    Es `transaction_amount` y no `total_paid_amount` (con cuotas incluye el
    interés que paga el comprador, que no es un pago de más) ni
    `net_received_amount` (resta la comisión de MP: usarlo bajaría el KPI de
    ingresos ~2% en silencio)."""
    if pago.get("currency_id") != "ARS":
        return False
    try:
        cobrado = Decimal(str(pago["transaction_amount"]))
    except (KeyError, InvalidOperation):
        return False
    return abs(cobrado - intento.monto_ars) <= TOLERANCIA_MONTO


def acreditar(payment_id, *, url_panel_para=None):
    """Lleva un pago de Mercado Pago al estado que le corresponde. Devuelve el
    intento, o `None` si el pago no es de ningún intento nuestro.

    Idempotente y conmutativo: el webhook, el retorno del navegador y la
    reconciliación pueden llamarlo en cualquier orden y cualquier cantidad de
    veces. `url_panel_para(gimnasio) -> str` es opcional (el cron no tiene
    `request`); sin él los mails salen sin link.

    `ErrorDeMercadoPago` se propaga: quien llama decide (el webhook responde
    500 para que MP reintente; el retorno muestra «confirmando»).
    """
    # ANTES de abrir la transacción: no se retiene un lock mientras se espera a
    # la red. Y el estado sale SIEMPRE de acá, nunca del cuerpo de un webhook,
    # que viaja sin firmar (la firma cubre el `data.id`, no el `action`).
    pago = mercadopago.consultar_pago(payment_id)

    try:
        referencia = uuid.UUID(str(pago.get("external_reference") or ""))
    except (ValueError, AttributeError):
        return None
    pk = (
        IntentoDeCobro.objects.filter(referencia_externa=referencia)
        .values_list("pk", flat=True)
        .first()
    )
    if pk is None:
        return None

    with transaction.atomic():
        intento = (
            IntentoDeCobro.objects.select_for_update()
            .select_related("gimnasio")
            .get(pk=pk)
        )
        pago_id = str(pago["id"])
        estado_mp = pago.get("status", "") or ""
        url_panel = url_panel_para(intento.gimnasio) if url_panel_para else ""

        if intento.payment_id and intento.payment_id != pago_id:
            # Un pago DISTINTO del que el intento ya tiene: un link de
            # preferencia se puede pagar más de una vez.
            if intento.estado in _CON_PLATA:
                # El pago anterior movió plata: no se pisa nada. Si el nuevo
                # también está aprobado, hay que devolverlo. Una notificación
                # vieja de un pago rechazado que llega tarde cae acá y no hace
                # nada.
                if estado_mp in ESTADOS_MP_APROBADO:
                    _avisar_problema(
                        intento, "pago_adicional", url_panel, pago_adicional_id=pago_id
                    )
                return intento
            # El pago anterior NO se acreditó (CREADO, VENCIDO, CANCELADO,
            # RECHAZADO): se reemplaza y se sigue. Es el caso normal de una
            # tarjeta rechazada seguida de un reintento con otra tarjeta en la
            # misma pantalla de Mercado Pago: tratarlo como «pago adicional»
            # dejaría sin acreditar un pago aprobado.

        # Cortocircuito por `(payment_id, estado_mp)`, NO por `payment_id`
        # solo: Mercado Pago manda otra notificación con el MISMO `payment_id`
        # cuando el pago pasa a `refunded`/`charged_back`. Cortar por id solo
        # se tragaría la devolución y el gimnasio se quedaría con el período
        # cobrado.
        if intento.payment_id == pago_id and intento.estado_mp == estado_mp:
            return intento

        intento.payment_id = pago_id
        intento.estado_mp = estado_mp
        intento.respuesta_mp = mercadopago.resumen_de_pago(pago)

        if estado_mp in ESTADOS_MP_APROBADO and intento.estado in _ACREDITABLES:
            _acreditar_aprobado(intento, pago, pago_id, url_panel)
        elif estado_mp in ESTADOS_MP_RECHAZADO and intento.estado in (
            _Estado.CREADO,
            _Estado.VENCIDO,
        ):
            # Sin mail: el gimnasio lo ve en pantalla y reintenta con un link
            # nuevo. El índice parcial ya lo libera: sólo mira `CREADO`.
            intento.estado = _Estado.RECHAZADO
        elif estado_mp in ESTADOS_MP_DEVUELTO and intento.estado == _Estado.PAGADO:
            # NO se borra el `PagoPlataforma` ni se recongela la cuenta: cortar
            # el acceso es siempre una decisión humana (docstring de
            # `Gimnasio.EstadoCuenta`). El mail le dice al superadmin cómo
            # está la cuenta hoy para que decida.
            intento.estado = _Estado.DEVUELTO
            _avisar_problema(intento, "devuelto", url_panel)
        # Cualquier otro status (`pending`, `in_process`, `authorized`…) sólo
        # queda guardado.

        intento.save()
    return intento


def _acreditar_aprobado(intento, pago, pago_id, url_panel):
    """Rama del pago aprobado sobre un intento acreditable. Muta `intento`
    (lo guarda quien llama) y encola los mails."""
    if not _monto_coincide(pago, intento):
        intento.estado = _Estado.REVISAR
        _avisar_problema(intento, "revisar", url_panel)
        return

    # Solapamiento con cualquier pago ya registrado del gimnasio (el
    # superadmin pudo haber cargado ese período a mano, por transferencia). La
    # `UniqueConstraint(gimnasio, periodo_desde)` sólo ve el mismo día de
    # inicio: un pago manual que arranca otro día y se pisa con este período
    # pasaría sin ruido.
    solapado = PagoPlataforma.objects.filter(
        gimnasio_id=intento.gimnasio_id,
        periodo_desde__lte=intento.periodo_hasta,
        periodo_hasta__gte=intento.periodo_desde,
    ).exists()
    if solapado:
        intento.estado = _Estado.DUPLICADO
        _avisar_problema(intento, "duplicado", url_panel)
        return

    try:
        # Su propio `atomic()`: sin él, el `IntegrityError` marca para rollback
        # la transacción de afuera y no se podría guardar el DUPLICADO.
        with transaction.atomic():
            pago_plataforma = PagoPlataforma.objects.create(
                gimnasio_id=intento.gimnasio_id,
                fecha_pago=_fecha_local(pago.get("date_approved")),
                # Los montos son los del INTENTO, no los de MP: lo que se le
                # cobra es lo que se le mostró.
                monto_usd=intento.monto_usd,
                monto_ars=intento.monto_ars,
                tipo_cambio=intento.tipo_cambio,
                alumnos_activos=intento.alumnos_activos,
                periodo_desde=intento.periodo_desde,
                periodo_hasta=intento.periodo_hasta,
                notas=f"Mercado Pago, pago {pago_id}",
                origen=PagoPlataforma.Origen.ONLINE,
                registrado_por=None,
            )
    except IntegrityError:
        # NUNCA tragarlo en silencio. El `except IntegrityError: return None`
        # de `solicitudes` existe para no filtrar información; acá significaría
        # quedarse con la plata del gimnasio sin decírselo a nadie.
        intento.estado = _Estado.DUPLICADO
        _avisar_problema(intento, "duplicado", url_panel)
        return

    intento.estado = _Estado.PAGADO
    intento.pago = pago_plataforma
    intento.acreditado_en = timezone.now()

    descongelado, sigue_debiendo_desde = _descongelar_si_corresponde(
        intento.gimnasio_id
    )
    transaction.on_commit(
        lambda: emails.enviar_cobro_acreditado(
            intento,
            descongelado=descongelado,
            sigue_debiendo_desde=sigue_debiendo_desde,
            url_panel=url_panel,
        )
    )


def reconciliar_pendientes(*, limite=50, url_panel_para=None):
    """Busca en Mercado Pago los pagos de los intentos recientes que todavía
    no tienen ninguno anotado, y los acredita. Devuelve cuántos intentos
    quedaron pagados.

    Es lo único que cubre «el webhook se perdió y el usuario cerró la
    pestaña». Un `ErrorDeMercadoPago` en un intento se loguea y no corta el
    resto: la próxima corrida lo vuelve a intentar.
    """
    if not integracion_activa():
        raise CobroNoDisponible("integracion_apagada")

    desde = timezone.now() - timedelta(days=DIAS_RECONCILIACION)
    pendientes = IntentoDeCobro.objects.filter(
        estado__in=(_Estado.CREADO, _Estado.VENCIDO),
        creado__gte=desde,
        payment_id="",
    ).order_by("creado", "id")[:limite]

    acreditados = 0
    for intento in pendientes:
        pagado = False
        try:
            resultados = mercadopago.buscar_pagos(str(intento.referencia_externa))
            # TODOS los resultados y no sólo el primero: si hubo dos pagos
            # aprobados, el segundo tiene que llegar a `acreditar` para que
            # salga el aviso de «pago adicional».
            for resultado in resultados:
                final = acreditar(resultado["id"], url_panel_para=url_panel_para)
                if final is not None and final.estado == _Estado.PAGADO:
                    pagado = True
        except ErrorDeMercadoPago as error:
            logger.warning(
                "Reconciliación: no se pudo consultar el intento %s: %s",
                intento.pk,
                error,
            )
        if pagado:
            acreditados += 1
    return acreditados
