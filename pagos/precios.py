"""Aplicar un precio de cuota recién guardado a las cuotas que todavía no
tienen monto.

`generar_pagos_pendientes` congela el monto al emitir. Sin esto, el gimnasio
que carga su precio por primera vez seguiría viendo en $0 las cuotas ya
emitidas (la vigente y la pre-emitida) hasta el ciclo siguiente -- y el cobro
por Mercado Pago, que necesita monto > 0, tardaría hasta 28 días en aparecer.

La regla: sólo las cuotas IMPAGAS con `monto=0`. Un monto ya puesto (por el
cron con un precio anterior, o a mano por el staff) NUNCA se pisa, y una
pagada o anulada no se toca.
"""

from django.db.models import Case, Exists, F, OuterRef, Value, When
from django.utils import timezone

from pagos.models import CobroOnlineCuota, Cuota


def _impagas_sin_monto():
    """Las cuotas a las que les corresponde un precio recién guardado.

    Sin intento de cobro online vivo (CREADO): una cuota con un link de pago
    abierto no puede cambiar de monto por debajo del link, que cobra el monto
    con el que se generó. Con monto 0 no debería haber link (pagar exige
    monto > 0), así que es defensa en profundidad. El filtro va ACÁ, así cubre
    los dos caminos (gimnasio y alumno).
    """
    link_vivo = CobroOnlineCuota.objects.filter(
        cuota=OuterRef("pk"), estado=CobroOnlineCuota.Estado.CREADO
    )
    return Cuota.objects.filter(
        estado__in=Cuota.ESTADOS_IMPAGOS, monto=0
    ).filter(~Exists(link_vivo))


def aplicar_precio_a_impagas_sin_monto(gimnasio, alumno=None):
    """Completa con el precio vigente las cuotas impagas en $0.

    - Con `alumno`: sus cuotas, con su precio propio o, si no tiene, el del
      gimnasio.
    - Sin `alumno`: las del gimnasio de los alumnos SIN precio propio (al que
      tiene uno, el precio del gimnasio no le corresponde).

    Sin precio que aplicar (vacío o 0) no hace nada: 0 sobre 0 es un no-op.
    Una sola UPDATE, el costo no depende de cuántas cuotas haya.

    `modificado` se escribe a mano (`update()` no dispara `auto_now`), salvo
    en las VENCIDAS: `enviar_recordatorios` manda «tu cuota está vencida» a
    las `estado=VENCIDO, modificado__date=hoy`, así que re-estamparlas al
    cargar un precio podría disparar ese push sobre vencidas viejas.

    Devuelve cuántas cuotas completó.
    """
    if alumno is not None:
        precio = (
            alumno.precio_cuota
            if alumno.precio_cuota is not None
            else gimnasio.precio_cuota
        )
        cuotas = _impagas_sin_monto().filter(gimnasio=gimnasio, alumno=alumno)
    else:
        precio = gimnasio.precio_cuota
        cuotas = _impagas_sin_monto().filter(
            gimnasio=gimnasio, alumno__precio_cuota__isnull=True
        )
    if not precio:
        return 0
    return cuotas.update(
        monto=precio,
        modificado=Case(
            When(estado=Cuota.Estado.VENCIDO, then=F("modificado")),
            default=Value(timezone.now()),
        ),
    )


def aviso_cuotas_completadas(cantidad):
    """El mensaje que ve el staff después de guardar un precio que completó
    cuotas: sin él, el cambio en el listado de cuotas sería invisible."""
    if cantidad == 1:
        return "Se completó el monto de 1 cuota impaga que estaba en $0."
    return f"Se completó el monto de {cantidad} cuotas impagas que estaban en $0."
