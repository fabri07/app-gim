"""Descuento sobre una cuota IMPAGA (pieza 3 de reconocimientos).

Es plata: el descuento nunca se escribe en una cuota pagada o anulada, nunca
pasa del 90% del monto de lista (`TOPE_DESCUENTO`, que además fija el
`CheckConstraint` de la base) y nunca es negativo. El monto de lista no se
toca: la cuota guarda precio y rebaja por separado.
"""

from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from pagos.cobro import _bloquear, cancelar_links_vivos
from pagos.models import TOPE_DESCUENTO, Cuota

CENTAVO = Decimal("0.01")


def descuento_maximo(monto):
    """El descuento más alto permitido sobre `monto`: el 90%, redondeado HACIA
    ABAJO a centavos (redondear para arriba pasaría del tope y la base lo
    rechazaría)."""
    return (Decimal(monto) * TOPE_DESCUENTO).quantize(CENTAVO, rounding=ROUND_DOWN)


def descuento_valido(monto, descuento):
    """¿`descuento` es 0 o está entre un centavo y el tope de `monto`?"""
    descuento = Decimal(descuento)
    if descuento == 0:
        return True
    return descuento > 0 and descuento <= descuento_maximo(monto)


def aplicar_descuento(cuota_pk, gimnasio, descuento, motivo, ahora=None):
    """Fija el descuento de una cuota IMPAGA del gimnasio (`0` lo quita) y
    cancela sus links de pago vivos (cobran el monto con el que se generaron).

    Devuelve si lo aplicó: `False` si el descuento es negativo o pasa del
    tope, o si la cuota no es impaga / no es del gimnasio. El tope se valida
    contra el monto leído BAJO el lock, así un cambio de monto concurrente no
    lo esquiva."""
    ahora = ahora or timezone.now()
    descuento = Decimal(descuento)
    if descuento < 0:
        return False
    with transaction.atomic():
        _bloquear(cuota_pk)
        monto = (
            Cuota.objects.filter(
                pk=cuota_pk, gimnasio=gimnasio, estado__in=Cuota.ESTADOS_IMPAGOS
            )
            .values_list("monto", flat=True)
            .first()
        )
        if monto is None or not descuento_valido(monto, descuento):
            return False
        aplicada = bool(
            Cuota.objects.filter(
                pk=cuota_pk, gimnasio=gimnasio, estado__in=Cuota.ESTADOS_IMPAGOS
            ).update(
                descuento=descuento,
                descuento_motivo=(motivo or "") if descuento > 0 else "",
                modificado=ahora,
            )
        )
        if aplicada:
            cancelar_links_vivos(cuota_pk, ahora)
    return aplicada
