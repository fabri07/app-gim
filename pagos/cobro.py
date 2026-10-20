"""Las transiciones de una cuota que pueden cruzarse con un cobro online.

Dominio puro, sin Mercado Pago: lo usan las vistas del staff y del alumno
(`pagos/views.py`) y lo va a usar la acreditación de Mercado Pago. Existe
porque, con un cobro online, la cuota deja de cambiar sólo por manos humanas:
un webhook la puede marcar PAGADA mientras el staff tiene abierto «Confirmar
pago», el alumno está subiendo un comprobante o alguien aprieta «Anular».
Antes, cada vista hacía `save()` del objeto que había leído al principio del
request y pisaba sin enterarse lo que hubiera pasado en el medio.

Dos reglas para todo lo de acá:

- **Cambiar el estado es un UPDATE condicional** (`filter(estado__in=...)`):
  si la cuota ya no está en el estado que se esperaba, no se toca y quien
  llama se entera (devuelve `False`/0) para decírselo a la persona.
- **Orden de locks SIEMPRE cuota → intentos** (`_bloquear`). La acreditación
  de MP toma los mismos dos locks en el mismo orden; al revés, dos requests
  concurrentes se podrían trabar mutuamente.

Cualquier cosa que saque a una cuota de impaga (pagarla, anularla, corregirle
el monto) cancela los links de pago vivos: un link abierto cobra el monto con
el que se generó, que ya no es el que corresponde.
"""

from django.db import transaction
from django.utils import timezone

from pagos.models import CobroOnlineCuota, Cuota


def _bloquear(cuota_pk):
    """Toma el lock de la cuota y DESPUÉS el de sus intentos de cobro online.

    En SQLite `select_for_update` es un no-op: la suite no puede mostrar un
    deadlock, por eso el orden se fija en un solo lugar."""
    list(Cuota.objects.select_for_update().filter(pk=cuota_pk).values_list("pk", flat=True))
    list(
        CobroOnlineCuota.objects.select_for_update()
        .filter(cuota_id=cuota_pk)
        .values_list("pk", flat=True)
    )


def cobrada_por_mercadopago(cuota_pk):
    """¿La cuota tiene un intento de cobro online PAGADO?

    Es LA forma de saber si una cuota se cobró por Mercado Pago: nunca
    `medio_pago_texto`, que los gimnasios ya llenan a mano con «Mercado Pago»
    para las transferencias a su alias."""
    return CobroOnlineCuota.objects.filter(
        cuota_id=cuota_pk, estado=CobroOnlineCuota.Estado.PAGADO
    ).exists()


def cancelar_links_vivos(cuota_pk, ahora):
    """Cancela los links de pago todavía abiertos (CREADO) de la cuota.

    Cancelar no impide que MP acredite un pago que el alumno ya había
    empezado: la acreditación se decide por el estado de la CUOTA, no del
    intento (la plata manda). Lo que evita es reusar ese link."""
    return CobroOnlineCuota.objects.filter(
        cuota_id=cuota_pk, estado=CobroOnlineCuota.Estado.CREADO
    ).update(estado=CobroOnlineCuota.Estado.CANCELADO, modificado=ahora)


def marcar_pagada(cuota, *, desde=None, ahora=None, **campos):
    """Deja la cuota PAGADA y cancela sus links de pago vivos. Devuelve si la
    marcó.

    - `desde=None` es el camino de «Confirmar pago» del staff: guarda la
      instancia que trae el form con `save()` completo (el `FileField` del
      comprobante sólo se sube así). Si la cuota ya fue cobrada por Mercado
      Pago (un intento PAGADO) NO guarda y devuelve `False`: el staff estaba
      registrando un cobro que, en el medio, ya entró por otro lado.
    - `desde=<estados>` es el camino de Mercado Pago: un UPDATE condicional
      sobre la cuota (sólo si sigue en uno de esos estados) con `campos`, y
      `modificado` explícito porque `update()` no dispara `auto_now`.
    """
    ahora = ahora or timezone.now()
    with transaction.atomic():
        _bloquear(cuota.pk)
        if desde is None:
            if cobrada_por_mercadopago(cuota.pk):
                return False
            cuota.estado = Cuota.Estado.PAGADO
            cuota.save()
            marcada = True
        else:
            marcada = bool(
                Cuota.objects.filter(pk=cuota.pk, estado__in=desde).update(
                    estado=Cuota.Estado.PAGADO, modificado=ahora, **campos
                )
            )
        if marcada:
            cancelar_links_vivos(cuota.pk, ahora)
    return marcada


def editar_pagada(cuota):
    """Guarda los cambios del staff sobre una cuota que YA estaba pagada
    cuando abrió el form (corregir la fecha, adjuntar el comprobante).

    Sólo si sigue PAGADA: si en el medio volvió a impaga (un pago devuelto),
    guardar el form la volvería a marcar pagada sin que nadie lo decida."""
    with transaction.atomic():
        _bloquear(cuota.pk)
        if not Cuota.objects.filter(pk=cuota.pk, estado=Cuota.Estado.PAGADO).exists():
            return False
        cuota.estado = Cuota.Estado.PAGADO
        cuota.save()
    return True


def corregir_monto(cuota_pk, gimnasio, monto, ahora=None):
    """Cambia el monto de una cuota IMPAGA y cancela sus links vivos (cobran
    el monto viejo). Una pagada o anulada no se toca. Devuelve si la cambió."""
    ahora = ahora or timezone.now()
    with transaction.atomic():
        _bloquear(cuota_pk)
        cambiada = bool(
            Cuota.objects.filter(
                pk=cuota_pk, gimnasio=gimnasio, estado__in=Cuota.ESTADOS_IMPAGOS
            ).update(monto=monto, modificado=ahora)
        )
        if cambiada:
            cancelar_links_vivos(cuota_pk, ahora)
    return cambiada


def anular(cuota_pk, ahora=None):
    """IMPAGA → ANULADO, y cancela los links vivos. Devuelve si la anuló."""
    ahora = ahora or timezone.now()
    with transaction.atomic():
        _bloquear(cuota_pk)
        anulada = bool(
            Cuota.objects.filter(
                pk=cuota_pk, estado__in=Cuota.ESTADOS_IMPAGOS
            ).update(estado=Cuota.Estado.ANULADO, modificado=ahora)
        )
        if anulada:
            cancelar_links_vivos(cuota_pk, ahora)
    return anulada


def reactivar(cuota_pk, ahora=None):
    """ANULADO → PENDIENTE (si su plazo ya pasó, la vence el próximo cron).
    Devuelve si la reactivó."""
    ahora = ahora or timezone.now()
    with transaction.atomic():
        _bloquear(cuota_pk)
        return bool(
            Cuota.objects.filter(pk=cuota_pk, estado=Cuota.Estado.ANULADO).update(
                estado=Cuota.Estado.PENDIENTE, modificado=ahora
            )
        )


def puede_volver_a_impaga(cuota_pk):
    """Una cuota PAGADA cuyo cobro por Mercado Pago se devolvió: tiene un
    intento DEVUELTO y ninguno PAGADO."""
    intentos = CobroOnlineCuota.objects.filter(cuota_id=cuota_pk)
    return (
        intentos.filter(estado=CobroOnlineCuota.Estado.DEVUELTO).exists()
        and not intentos.filter(estado=CobroOnlineCuota.Estado.PAGADO).exists()
    )


def volver_a_impaga(cuota, ahora=None, hoy=None):
    """Deshace el PAGADO de una cuota cuyo pago por Mercado Pago se devolvió.

    Sin esto la plata devuelta sigue sumando en `ingresos_por_mes` y el alumno
    sigue desbloqueado. Nunca pasa sola: la decide el staff (un devuelto puede
    estar ya compensado en efectivo). Vuelve a PENDIENTE, o a VENCIDO si su
    fecha límite ya pasó (la misma regla que `marcar_vencidos`), sin fecha de
    pago ni medio. La revisión del intento devuelto queda resuelta.
    Devuelve si la cambió.
    """
    ahora = ahora or timezone.now()
    hoy = hoy or timezone.localdate()
    estado = (
        Cuota.Estado.VENCIDO
        if cuota.fecha_limite_pago() < hoy
        else Cuota.Estado.PENDIENTE
    )
    with transaction.atomic():
        _bloquear(cuota.pk)
        if not puede_volver_a_impaga(cuota.pk):
            return False
        cambiada = bool(
            Cuota.objects.filter(pk=cuota.pk, estado=Cuota.Estado.PAGADO).update(
                estado=estado, fecha_pago=None, medio_pago_texto="", modificado=ahora
            )
        )
        if cambiada:
            CobroOnlineCuota.objects.filter(
                cuota_id=cuota.pk,
                estado=CobroOnlineCuota.Estado.DEVUELTO,
                revision_pendiente=True,
            ).update(revision_pendiente=False, revision_resuelta_en=ahora, modificado=ahora)
    return cambiada
