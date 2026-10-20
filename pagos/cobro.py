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

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from pagos.models import CobroOnlineCuota, Cuota

#: Donde `pagos.cobro_mp` anota, en `respuesta_mp`, los pagos adicionales de
#: un link pagado dos veces (hay que devolverlos).
CLAVE_PAGOS_ADICIONALES = "pagos_adicionales"


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


#: Lo que guarda «Confirmar pago» (los campos de `ConfirmarPagoForm`) más el
#: estado y `modificado` (`auto_now` corre porque va en `update_fields`).
CAMPOS_CONFIRMAR_PAGO = (
    "monto", "fecha_pago", "medio_pago_texto", "comprobante", "estado", "modificado",
)


def _subir_comprobante(cuota):
    """Sube el comprobante nuevo (si lo hay) ANTES de tomar los locks: subir
    a R2 tarda, y con la cuota y sus intentos bloqueados la acreditación de
    MP esperaría detrás. `pre_save` es lo que hace `save()` con un
    `FileField`; el `save()` posterior ya no lo vuelve a subir. Devuelve el
    archivo recién subido (para borrarlo si al final no se guarda) o `None`."""
    campo = Cuota._meta.get_field("comprobante")
    nuevo = bool(cuota.comprobante) and not cuota.comprobante._committed
    archivo = campo.pre_save(cuota, add=False)
    return archivo if nuevo else None


def _borrar_subido(archivo):
    if archivo is None:
        return
    try:
        archivo.storage.delete(archivo.name)
    except Exception:  # noqa: BLE001 - un huérfano en el bucket no es un error del staff
        pass


def marcar_pagada(cuota, *, desde=None, ahora=None, **campos):
    """Deja la cuota PAGADA y cancela sus links de pago vivos. Devuelve si la
    marcó.

    - `desde=None` es el camino de «Confirmar pago» del staff: sube el
      comprobante ANTES del lock y guarda la instancia que trae el form con
      `update_fields=CAMPOS_CONFIRMAR_PAGO`. Si la cuota ya fue cobrada por
      Mercado Pago (un intento PAGADO) NO guarda, borra el comprobante recién
      subido y devuelve `False`: el staff estaba registrando un cobro que, en
      el medio, ya entró por otro lado.
    - `desde=<estados>` es el camino de Mercado Pago: un UPDATE condicional
      sobre la cuota (sólo si sigue en uno de esos estados) con `campos`, y
      `modificado` explícito porque `update()` no dispara `auto_now`.
    """
    ahora = ahora or timezone.now()
    subido = _subir_comprobante(cuota) if desde is None else None
    try:
        with transaction.atomic():
            _bloquear(cuota.pk)
            if desde is None:
                if cobrada_por_mercadopago(cuota.pk):
                    marcada = False
                else:
                    cuota.estado = Cuota.Estado.PAGADO
                    cuota.save(update_fields=CAMPOS_CONFIRMAR_PAGO)
                    marcada = True
            else:
                marcada = bool(
                    Cuota.objects.filter(pk=cuota.pk, estado__in=desde).update(
                        estado=Cuota.Estado.PAGADO, modificado=ahora, **campos
                    )
                )
            if marcada:
                cancelar_links_vivos(cuota.pk, ahora)
    except Exception:
        _borrar_subido(subido)
        raise
    if not marcada:
        _borrar_subido(subido)
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
    pago ni medio. La revisión del intento devuelto queda resuelta, salvo
    que tenga pagos adicionales sin devolver (siguen en «Revisar»).
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
            # El motivo propio (el devuelto) queda resuelto, pero un pago
            # adicional sin devolver sigue siendo plata a devolver: esa fila
            # sigue en «Revisar». Cuando llegue su devolución, la acreditación
            # apaga la revisión sola (mira los adicionales y que el motivo
            # propio ya tiene `revision_resuelta_en`). Los intentos ya están
            # bloqueados por `_bloquear`.
            from pagos.cobro_mp import adicionales_sin_resolver

            for pk, respuesta in CobroOnlineCuota.objects.filter(
                cuota_id=cuota.pk,
                estado=CobroOnlineCuota.Estado.DEVUELTO,
                revision_pendiente=True,
            ).values_list("pk", "respuesta_mp"):
                CobroOnlineCuota.objects.filter(pk=pk).update(
                    revision_pendiente=bool(adicionales_sin_resolver(respuesta)),
                    revision_resuelta_en=ahora,
                    modificado=ahora,
                )
    return cambiada


# --- Pantalla «Pagos con Mercado Pago» del staff ---------------------------------

#: Intentos sobre los que entró plata (o entró y se devolvió). Son los que
#: lista `pagos:online`: un link generado que nadie pagó no le dice nada al
#: staff.
ESTADOS_CON_PLATA = (
    CobroOnlineCuota.Estado.PAGADO,
    CobroOnlineCuota.Estado.DUPLICADO,
    CobroOnlineCuota.Estado.REVISAR,
    CobroOnlineCuota.Estado.DEVUELTO,
)

#: Pestaña «Revisar»: hay algo que el staff tiene que mirar. Sólo el booleano
#: y no también `estado=REVISAR`: todo intento que pasa a REVISAR prende
#: `revision_pendiente` (`cobro_mp._con_revision`), y un REVISAR cuya plata ya
#: se devolvió, o que el staff marcó «Ya lo resolví», tiene que salir de la
#: pestaña (con el estado en el filtro no saldría nunca: el intento no tiene
#: salida de REVISAR). La comparten la pestaña, el banner del listado, la
#: alerta del panel y el push.
Q_REVISAR_MP = Q(revision_pendiente=True)

#: Todo lo que lista la pantalla.
Q_PANTALLA_MP = Q(estado__in=ESTADOS_CON_PLATA) | Q_REVISAR_MP


def contadores_cobro_online(gimnasio):
    """`{"total", "a_revisar"}` de la pantalla `pagos:online`, en UNA query
    agregada (sin filas: el costo no depende de cuántos intentos haya)."""
    return CobroOnlineCuota.objects.for_gimnasio(gimnasio).aggregate(
        total=Count("pk", filter=Q_PANTALLA_MP),
        a_revisar=Count("pk", filter=Q_REVISAR_MP),
    )


def puede_haber_cobros_online(gimnasio):
    """¿Vale la pena preguntarle a la base por los cobros online del
    gimnasio? Sin query: la bandera prendida, o el interruptor de «Mi
    gimnasio» prendido (con la bandera apagada el form no lo muestra, así que
    no cambia: si quedó prendido es que se cobró o se pudo cobrar).

    Es lo que mantiene el panel del staff en las mismas queries de siempre con
    la bandera apagada (hoy, en producción) y sin dejar escondida plata para
    revisar si la bandera se apaga después de haber cobrado."""
    return settings.TIENDA_MP_ENABLED or gimnasio.cuotas_con_mercadopago


def marcar_revision_resuelta(intento_pk, gimnasio, ahora=None):
    """«Ya lo resolví»: el staff ya hizo lo que pedía la revisión (devolvió el
    pago, cobró la diferencia...). UPDATE condicional a que siga pendiente y
    sea del gimnasio; `modificado` explícito, que es además lo que hace que
    una acreditación concurrente (`cobro_mp._transicionar`) relea y decida de
    nuevo en vez de pisarlo. Devuelve si lo marcó.

    Marca además como resueltos TODOS los pagos adicionales anotados en
    `respuesta_mp` (el staff los devolvió o los compensó): si no, cuando
    después llega la devolución de UNO, la acreditación recalcula
    `revision_pendiente` mirando los adicionales sin resolver y la volvería a
    prender por los que el staff ya resolvió. El UPDATE va condicionado
    también al `modificado` leído: si una acreditación cambió el intento en el
    medio, se relee una vez."""
    ahora = ahora or timezone.now()
    for _ in range(2):
        fila = (
            CobroOnlineCuota.objects.filter(
                pk=intento_pk, gimnasio=gimnasio, revision_pendiente=True
            )
            .values_list("respuesta_mp", "modificado")
            .first()
        )
        if fila is None:
            return False
        respuesta, modificado = fila
        respuesta = dict(respuesta) if isinstance(respuesta, dict) else respuesta
        if isinstance(respuesta, dict) and isinstance(
            respuesta.get(CLAVE_PAGOS_ADICIONALES), list
        ):
            respuesta[CLAVE_PAGOS_ADICIONALES] = [
                dict(entrada, resuelto=entrada.get("resuelto") or ahora.isoformat())
                if isinstance(entrada, dict)
                else entrada
                for entrada in respuesta[CLAVE_PAGOS_ADICIONALES]
            ]
        tocadas = CobroOnlineCuota.objects.filter(
            pk=intento_pk, gimnasio=gimnasio, revision_pendiente=True, modificado=modificado
        ).update(
            revision_pendiente=False,
            revision_resuelta_en=ahora,
            respuesta_mp=respuesta,
            modificado=ahora,
        )
        if tocadas:
            return True
    return False
