"""Cobro de cuotas por Mercado Pago, a la cuenta OAuth del PROPIO gimnasio
(la misma de la tienda, `tienda.CuentaMercadoPago`).

Acá vive la acreditación: `acreditar_cuota_leida` lleva un pago ya leído al
estado que le corresponde (molde `tienda.pagos_mp.acreditar_pedido` /
`_aplicar`). Los pagos de cuotas llegan por el MISMO webhook de la tienda
(`notification_url` por `uuid_webhook` de la cuenta): `acreditar_pedido`
desvía acá todo pago cuya `external_reference` empieza con `PREFIJO`.

**Nunca el token de la plataforma**: toda llamada a `consultar_pago`,
`crear_preferencia` y `buscar_pagos` pasa `token=Secreto(cuenta.access_token)`
(sin él `suscripcion.mercadopago` usa `MERCADOPAGO_ACCESS_TOKEN` y la plata
del alumno iría a la cuenta del dueño de TuGimApp). Lo fija
`pagos/tests_token_del_gimnasio.py`.

La máquina de estados decide por el PAR (estado del intento × estado de la
cuota), con la plata como regla: un pago aprobado se registra aunque el link
estuviera cancelado o vencido, aunque el gimnasio esté congelado, no tenga
prendidas las cuotas por MP ni el add-on, o haya desconectado la cuenta
(mientras queden tokens). Lo que nunca hace es cobrar dos veces una cuota ni
escribir el monto que dice MP: el monto se valida, no se copia.

`tienda` carga después que `pagos` (`config/settings.py`): lo de `tienda` se
importa dentro de las funciones.
"""

import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pagos import cobro
from pagos.models import CobroOnlineCuota, Cuota
from suscripcion import mercadopago
from suscripcion.mercadopago import Secreto

logger = logging.getLogger(__name__)

PREFIJO = CobroOnlineCuota.PREFIJO_REFERENCIA

_I = CobroOnlineCuota.Estado
_M = CobroOnlineCuota.MotivoRevision

# Mismos valores que `tienda.pagos_mp` (no se importan a nivel de módulo: ver
# la cabecera).
ESTADOS_MP_APROBADO = frozenset({"approved"})
ESTADOS_MP_RECHAZADO = frozenset({"rejected", "cancelled"})
ESTADOS_MP_DEVUELTO = frozenset({"refunded", "charged_back"})
TOLERANCIA_MONTO = Decimal("0.01")
CLAVE_PAGOS_ADICIONALES = "pagos_adicionales"
MEDIO_MERCADO_PAGO = "Mercado Pago"

#: Intentos cuyo pago (si lo hubo) NO movió plata: un aprobado los resuelve.
_SIN_PLATA = (_I.CREADO, _I.RECHAZADO, _I.CANCELADO, _I.VENCIDO)
#: Intentos sobre los que ya entró plata (o entró y se devolvió).
_CON_PLATA = (_I.PAGADO, _I.DUPLICADO, _I.REVISAR, _I.DEVUELTO)


class _Carrera(Exception):
    """Un UPDATE condicional no tocó la fila: hay que releer y decidir."""


# --- Lectura del pago ---------------------------------------------------------


def referencia_de(pago):
    """El UUID del intento si el pago es de una cuota, si no `None`."""
    texto = str(pago.get("external_reference") or "")
    if not texto.startswith(PREFIJO):
        return None
    try:
        return uuid.UUID(texto[len(PREFIJO):])
    except ValueError:
        return None


def _monto_coincide(pago, monto):
    """`transaction_amount` (lo que pagó el alumno por la cuota, sin el
    interés de las cuotas de la tarjeta ni la comisión de MP: nunca
    `net_received_amount`) en ARS, con tolerancia de un centavo."""
    if pago.get("currency_id") != "ARS":
        return False
    try:
        cobrado = Decimal(str(pago["transaction_amount"]))
    except (KeyError, InvalidOperation):
        return False
    return abs(cobrado - monto) <= TOLERANCIA_MONTO


def _fecha_de_pago(pago):
    """`date_approved` en la fecha LOCAL (UTC-3: un pago de las 22 h no puede
    quedar con la fecha de mañana). Sin ella, hoy."""
    momento = parse_datetime(str(pago.get("date_approved") or ""))
    if momento is None:
        return timezone.localdate()
    if timezone.is_naive(momento):
        return momento.date()
    return timezone.localtime(momento).date()


# --- Pagos adicionales (un link pagado dos veces) ----------------------------


def _adicionales(respuesta):
    if not isinstance(respuesta, dict):
        return []
    return [dict(r) for r in respuesta.get(CLAVE_PAGOS_ADICIONALES) or [] if isinstance(r, dict)]


def adicionales_sin_resolver(respuesta):
    return [r for r in _adicionales(respuesta) if not r.get("resuelto")]


def _revision_propia_pendiente(intento):
    """El motivo del propio intento sigue sin resolver por el staff."""
    return bool(intento.motivo_revision) and intento.revision_resuelta_en is None


def _respuesta_con(pago, intento):
    """El resumen del pago, conservando `pagos_adicionales` (la única traza
    de un pago que hay que devolver)."""
    respuesta = dict(pago)
    adicionales = _adicionales(intento.respuesta_mp)
    if adicionales:
        respuesta[CLAVE_PAGOS_ADICIONALES] = adicionales
    return respuesta


# --- Escrituras -----------------------------------------------------------------


def _leer(cuota_pk, intento_pk):
    """La cuota y el intento con las filas bloqueadas, SIEMPRE en ese orden
    (`pagos.cobro._bloquear`). En SQLite no bloquea nada y los UPDATE
    condicionales son los que mandan. Aparte para que los tests simulen una
    lectura vieja."""
    cuota = Cuota.objects.select_for_update().get(pk=cuota_pk)
    intento = CobroOnlineCuota.objects.select_for_update().get(pk=intento_pk)
    return cuota, intento


def _transicionar(intento, ahora, **campos):
    """UPDATE del intento condicionado a lo que se leyó (estado, pago y
    `modificado`): si otro proceso lo movió, `_Carrera`."""
    tocadas = CobroOnlineCuota.objects.filter(
        pk=intento.pk,
        estado=intento.estado,
        payment_id=intento.payment_id,
        estado_mp=intento.estado_mp,
        modificado=intento.modificado,
    ).update(modificado=ahora, **campos)
    if tocadas != 1:
        raise _Carrera


def _cuota_sigue(cuota):
    """La cuota sigue en el estado leído (las ramas que no la escriben
    deciden por él)."""
    if not Cuota.objects.filter(pk=cuota.pk, estado=cuota.estado).exists():
        raise _Carrera


def _avisar(intento_pk, staff=None, alumno=None):
    """Los avisos salen DESPUÉS del commit (y no salen si se deshace: un
    savepoint revertido descarta los suyos). Un push caído no rompe nada."""
    from notificaciones import services as avisos

    def mandar():
        try:
            intento = CobroOnlineCuota.objects.select_related(
                "cuota__alumno__perfil__usuario", "gimnasio"
            ).get(pk=intento_pk)
            if staff:
                avisos.notificar_cuota_mp_al_staff(intento, staff)
            if alumno:
                avisos.notificar_cuota_mp_al_alumno(intento, alumno)
        except Exception:  # noqa: BLE001 - un push caído no frena nada
            logger.exception("No se pudo avisar la acreditación del cobro de cuota %s", intento_pk)

    transaction.on_commit(mandar)


def _registrar_adicional(intento, pago_id, estado_mp, ahora):
    """Otro pago aprobado sobre un intento que ya cobró (el alumno pagó dos
    veces el mismo link): el intento y la cuota quedan como están, el pago se
    anota para devolverlo. Quien llama ya descartó los anotados (el aviso
    sale una vez)."""
    respuesta = dict(intento.respuesta_mp or {})
    registrados = _adicionales(respuesta)
    registrados.append(
        {"payment_id": pago_id, "status": estado_mp, "detectado": ahora.isoformat()}
    )
    respuesta[CLAVE_PAGOS_ADICIONALES] = registrados
    _transicionar(intento, ahora, respuesta_mp=respuesta, revision_pendiente=True)
    logger.warning(
        "Pago de Mercado Pago adicional sobre el cobro de cuota %s (gimnasio %s): pago %s. "
        "Hay que devolverlo desde Mercado Pago.",
        intento.pk, intento.gimnasio_id, pago_id,
    )
    _avisar(intento.pk, staff="pago_duplicado")


def _resolver_adicional(intento, pago_id, ahora):
    """La devolución de un pago anotado como adicional: su entrada queda
    resuelta, sin alarma."""
    respuesta = dict(intento.respuesta_mp or {})
    registrados = _adicionales(respuesta)
    cambio = False
    for entrada in registrados:
        if str(entrada.get("payment_id")) == pago_id and not entrada.get("resuelto"):
            entrada["resuelto"] = ahora.isoformat()
            cambio = True
    if not cambio:
        return
    respuesta[CLAVE_PAGOS_ADICIONALES] = registrados
    _transicionar(
        intento, ahora, respuesta_mp=respuesta,
        revision_pendiente=bool(adicionales_sin_resolver(respuesta))
        or _revision_propia_pendiente(intento),
    )


def _con_revision(motivo):
    return dict(
        motivo_revision=motivo, revision_pendiente=True, revision_resuelta_en=None,
    )


def _aprobado(cuota, intento, pago, ahora, campos):
    """Un pago aprobado sobre un intento sin plata: decide la CUOTA."""
    if not _monto_coincide(pago, intento.monto):
        # Monto o moneda distintos: nunca «Pagada». El monto de MP se valida,
        # nunca se escribe.
        _transicionar(intento, ahora, estado=_I.REVISAR, **campos,
                      **_con_revision(_M.MONTO_DISTINTO))
        _avisar(intento.pk, staff="revisar_mp", alumno="revisar")
        return

    if cuota.estado in Cuota.ESTADOS_IMPAGOS:
        # Monto corregido por el staff con el link abierto: se paga con lo
        # cobrado (el monto del intento) y queda para revisar.
        extra = {} if cuota.monto == intento.monto else _con_revision(_M.MONTO_CORREGIDO)
        # Primero el intento (deja de ser CREADO: `marcar_pagada` cancela los
        # links vivos y no lo toca), después la cuota. Si la cuota ya no
        # estaba impaga, `_Carrera` deshace los dos.
        _transicionar(intento, ahora, estado=_I.PAGADO, **campos, **extra)
        marcada = cobro.marcar_pagada(
            cuota,
            desde=Cuota.ESTADOS_IMPAGOS,
            ahora=ahora,
            monto=intento.monto,
            fecha_pago=_fecha_de_pago(pago),
            medio_pago_texto=MEDIO_MERCADO_PAGO,
        )
        if not marcada:
            raise _Carrera
        _avisar(intento.pk, staff="revisar_mp" if extra else "pago_mp", alumno="pago_mp")
        return

    _cuota_sigue(cuota)
    if cuota.estado == Cuota.Estado.PAGADO:
        # Pagada a mano o por otro link: nunca se cobra dos veces. La cuota
        # no se toca; el pago hay que devolverlo.
        _transicionar(intento, ahora, estado=_I.DUPLICADO, **campos,
                      **_con_revision(_M.CUOTA_YA_PAGADA))
        _avisar(intento.pk, staff="pago_duplicado", alumno="revisar")
        return
    # ANULADA: la plata entró igual; lo decide una persona.
    _transicionar(intento, ahora, estado=_I.REVISAR, **campos,
                  **_con_revision(_M.CUOTA_ANULADA))
    _avisar(intento.pk, staff="revisar_mp", alumno="revisar")


def _aplicar(cuota, intento, pago, ahora):
    """La máquina de estados. Toda escritura es un UPDATE condicional;
    `_Carrera` si alguno no tocó la fila. Nunca `save()`."""
    pago_id = str(pago["id"])
    estado_mp = pago.get("status", "") or ""
    mismo = intento.payment_id == pago_id

    # 1. Un pago ya anotado como adicional: sólo cuenta su devolución.
    if any(str(r.get("payment_id")) == pago_id for r in _adicionales(intento.respuesta_mp)):
        if estado_mp in ESTADOS_MP_DEVUELTO:
            _resolver_adicional(intento, pago_id, ahora)
        return

    # 2. Idempotencia por `(payment_id, estado_mp)`: la devolución llega con
    #    el MISMO id.
    if mismo and intento.estado_mp == estado_mp:
        return

    # 3. Para el mismo pago `estado_mp` no retrocede (dos lecturas fuera del
    #    lock pueden commitear en otro orden).
    if mismo:
        if intento.estado_mp in ESTADOS_MP_DEVUELTO and estado_mp not in ESTADOS_MP_DEVUELTO:
            return
        if intento.estado_mp in ESTADOS_MP_APROBADO and estado_mp not in (
            ESTADOS_MP_APROBADO | ESTADOS_MP_DEVUELTO
        ):
            return

    campos = dict(
        payment_id=pago_id, estado_mp=estado_mp, respuesta_mp=_respuesta_con(pago, intento)
    )

    if estado_mp in ESTADOS_MP_APROBADO:
        if intento.estado in _CON_PLATA:
            if mismo:
                _transicionar(intento, ahora, **campos)
            else:
                _registrar_adicional(intento, pago_id, estado_mp, ahora)
            return
        _aprobado(cuota, intento, pago, ahora, dict(campos, acreditado_en=ahora))
        return

    if estado_mp in ESTADOS_MP_RECHAZADO:
        # Sólo se anota: con `binary_mode` un rechazo seguido de un reintento
        # aprobado en el mismo link es lo normal. El intento sigue como
        # estaba (CREADO sigue pagable). Nunca pisa la marca de un pago
        # devuelto de otro id.
        if intento.estado in _SIN_PLATA and (
            mismo or intento.estado_mp not in ESTADOS_MP_DEVUELTO
        ):
            _transicionar(intento, ahora, **campos)
        return

    if estado_mp in ESTADOS_MP_DEVUELTO:
        if mismo and intento.estado == _I.PAGADO:
            # El pago que pagó la cuota: la cuota NO vuelve sola a impaga (un
            # devuelto puede estar compensado en efectivo); el staff decide
            # con «Volver a impaga».
            _transicionar(intento, ahora, estado=_I.DEVUELTO, **campos,
                          **_con_revision(_M.DEVUELTO))
            _avisar(intento.pk, staff="devuelto_mp")
        elif mismo and intento.estado in (_I.DUPLICADO, _I.REVISAR):
            # Se devolvió un pago que no pagó la cuota: era lo que había que
            # hacer. Resuelto, sin alarma.
            _transicionar(
                intento, ahora, **campos,
                revision_resuelta_en=intento.revision_resuelta_en or ahora,
                revision_pendiente=bool(adicionales_sin_resolver(intento.respuesta_mp)),
            )
        elif mismo and intento.estado == _I.DEVUELTO:
            _transicionar(intento, ahora, **campos)
        elif intento.estado in _SIN_PLATA and (
            mismo or intento.estado_mp not in ESTADOS_MP_DEVUELTO
        ):
            # Se anota igual, así una lectura vieja de «approved» de ESTE
            # pago no lo acredita después (regla 3).
            _transicionar(intento, ahora, **campos)
        return

    # `in_process`, `pending`, `authorized`, `in_mediation`...: nada.


# --- Entradas ----------------------------------------------------------------


def acreditar_cuota_leida(cuenta, pago):
    """Lleva el pago YA LEÍDO (y recortado con `resumen_de_pago`) con el
    token de `cuenta` al estado que le corresponde. Devuelve el intento, o
    `None` si el pago no es de un cobro de cuota de ESE gimnasio y ESA cuenta.

    Idempotente y conmutativo (webhook, retorno y reconciliación lo llaman en
    cualquier orden). `AcreditacionReintentable` (de la tienda: el webhook ya
    la responde con 500) si otro proceso movió la cuota o el intento dos
    veces mientras se decidía."""
    if pago.get("id") is None:
        return None
    referencia = referencia_de(pago)
    if referencia is None:
        return None
    fila = (
        CobroOnlineCuota.objects.filter(
            gimnasio_id=cuenta.gimnasio_id, referencia_externa=referencia
        )
        .values_list("pk", "cuota_id", "mp_user_id")
        .first()
    )
    if fila is None:
        return None
    intento_pk, cuota_pk, mp_user_id = fila
    if mp_user_id and mp_user_id != str(cuenta.mp_user_id or ""):
        # El link lo generó OTRA cuenta de MP del gimnasio (cambió de cuenta):
        # un pago leído con esta no lo acredita. Sin datos del pagador.
        logger.warning(
            "Pago de Mercado Pago %s del cobro de cuota %s leído con otra cuenta de MP "
            "(gimnasio %s): ignorado.",
            pago.get("id"), intento_pk, cuenta.gimnasio_id,
        )
        return None

    from tienda.pagos_mp import AcreditacionReintentable

    ahora = timezone.now()
    with transaction.atomic():
        for _ in range(2):
            try:
                # Un savepoint por vuelta: una carrera deshace lo que esta
                # vuelta ya había escrito (y sus avisos).
                with transaction.atomic():
                    cuota, intento = _leer(cuota_pk, intento_pk)
                    _aplicar(cuota, intento, pago, ahora)
                break
            except _Carrera:
                continue
        else:
            raise AcreditacionReintentable(
                f"El cobro de cuota {intento_pk} cambió dos veces mientras se acreditaba"
            )
    return CobroOnlineCuota.objects.get(pk=intento_pk)


def acreditar_cuota_por_id(cuenta, payment_id):
    """Lee el pago con el token DEL GIMNASIO y lo acredita si es de una
    cuota. `ErrorDeMercadoPago` se propaga (quien llama decide)."""
    # Fuera de la transacción (no se retiene un lock esperando a la red) y
    # recortado en la MISMA línea: la respuesta cruda trae los datos del
    # pagador y Sentry captura las variables locales de cada frame.
    pago = mercadopago.resumen_de_pago(
        mercadopago.consultar_pago(payment_id, token=Secreto(cuenta.access_token))
    )
    if referencia_de(pago) is None:
        return None
    return acreditar_cuota_leida(cuenta, pago)
