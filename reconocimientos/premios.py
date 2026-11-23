"""Premios por asistencia perfecta: quién califica, darlo y aplicarlo a las cuotas.

Es plata, así que tres reglas:

- **El staff confirma.** `candidatos` sólo propone; nada se otorga solo.
  `otorgar` vuelve a validar TODO contra la base (el listado pudo quedar viejo
  o el POST venir forjado) antes de escribir.
- **El único que escribe un descuento es `pagos.descuentos.aplicar_descuento`**
  (tope del 90%, nunca en una cuota pagada o anulada, cancela los links de
  Mercado Pago vivos). Acá se llama siempre con `solo_si_sin_descuento=True`:
  un descuento que el staff cargó a mano no se pisa.
- **Una cuota recibe a lo sumo un premio** (unicidad de `PremioAplicacion.cuota`)
  y un premio no se pasa de sus `cuotas`: cada aplicación toma el lock del
  otorgado y recuenta bajo el lock.

Orden de locks: otorgado → cuota → intentos de cobro (`_bloquear` de `pagos`).
Nadie más toma el lock de un `PremioOtorgado`, así que no hay ciclo posible con
la acreditación de Mercado Pago (cuota → intentos).

Decisiones (plan de la pieza 5, «Decisiones propias»):

- Asistencia perfecta = `asistidas >= meta`, sin tolerancia de faltas.
- Ventana: ciclos CERRADOS (`fin < hoy`) con `fin >= hoy - MAX_DIAS_ATRAS`
  (el mismo tope con que el staff puede cargar asistencia). Puede haber dos.
- Sin deuda: ninguna cuota PENDIENTE/VENCIDA con `periodo_inicio <= fin` del
  ciclo (la del propio ciclo y las anteriores; la pre-emitida del siguiente no
  cuenta).
- El descuento cae en las próximas `cuotas` cuotas impagas, sin descuento, sin
  otro premio y con `periodo_inicio > fin` del ciclo premiado, en orden.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, Min, OuterRef
from django.utils import timezone

from alumnos.edad import es_mayor_de_edad
from alumnos.models import Alumno
from asistencia.services import MAX_DIAS_ATRAS, dias_asistidos_por_alumno
from novedades.models import Novedad
from pagos.descuentos import CENTAVO, aplicar_descuento, descuento_maximo
from pagos.models import DIAS_CICLO, Cuota
from reconocimientos.models import Premio, PremioAplicacion, PremioOtorgado
from rutinas.models import RutinaAsignada, RutinaAsignadaItem

logger = logging.getLogger(__name__)

#: Lo que `otorgar` devuelve como segundo elemento (las vistas lo muestran).
MOTIVO_OTORGADO = "Premio otorgado."
MOTIVO_YA_OTORGADO = "Ese premio ya estaba otorgado."
MOTIVO_PREMIO_INVALIDO = "El premio elegido no existe o está desactivado."
MOTIVO_NO_CALIFICA = (
    "El alumno ya no califica para ese ciclo (cambió su asistencia, su estado "
    "o sus cuotas, o el ciclo quedó fuera de plazo)."
)

TITULO_NOVEDAD = "¡Asistencia perfecta!"
#: Días que la novedad del premio queda visible en el portal.
DIAS_NOVEDAD = 60
#: Largo de `Cuota.descuento_motivo` (`varchar(80)`): SQLite no lo valida y
#: Postgres sí, así que se trunca en Python.
LARGO_MOTIVO = 80
TIPOS_DESCUENTO = (Premio.Tipo.PORCENTAJE, Premio.Tipo.FIJO)


@dataclass(frozen=True)
class Candidato:
    alumno_id: int
    nombre: str
    apellido: str
    es_menor: bool
    #: Sin fecha de nacimiento: `es_menor` es True (no se puede verificar la
    #: edad, mismo criterio que el ranking), y esto le permite a la pantalla
    #: del staff decir «Edad sin cargar» en vez de «Menor de 18».
    edad_sin_cargar: bool
    inicio: object  # date
    fin: object  # date
    meta: int
    asistidas: int


def _ciclos_en_ventana(ancla, hoy):
    """`[(inicio, fin)]` de los ciclos CERRADOS (`fin < hoy`) con
    `fin >= hoy - MAX_DIAS_ATRAS`, del más viejo al más nuevo. Pura.

    El ciclo `k` es `[ancla + 28k, ancla + 28k + 27]`; está cerrado si
    `28k + 27 < (hoy - ancla)`, o sea `k <= (hoy - ancla) // 28 - 1`."""
    if ancla is None or ancla >= hoy:
        return []
    piso = hoy - timedelta(days=MAX_DIAS_ATRAS)
    ciclos = []
    k = (hoy - ancla).days // DIAS_CICLO - 1
    while k >= 0:
        inicio = ancla + timedelta(days=DIAS_CICLO * k)
        fin = inicio + timedelta(days=DIAS_CICLO - 1)
        if fin < piso:
            break
        ciclos.append((inicio, fin))
        k -= 1
    return list(reversed(ciclos))


def candidatos(gimnasio, hoy=None, alumno_ids=None):
    """Alumnos del gimnasio que cerraron un ciclo con asistencia perfecta y
    todavía no recibieron premio por él. `alumno_ids` acota (para revalidar
    a uno solo).

    **Costo constante** (no crece con alumnos, rutinas ni asistencias): una
    query de alumnos, dos de asistencia (`dias_asistidos_por_alumno` con UNA
    ventana por alumno que cubre sus ciclos candidatos), una de rutinas, una
    de ítems, una de deuda y una de premios ya dados. La meta es la misma
    regla que `asistencia.services.meta_del_ciclo` (pares `(dia, semana)` de
    la rutina vigente al inicio del ciclo, orden `(-fecha_inicio, -id)`),
    resuelta en lote.
    """
    hoy = hoy or timezone.localdate()

    alumnos = Alumno.objects.for_gimnasio(gimnasio).filter(
        estado=Alumno.Estado.ACTIVO, fecha_inicio_ciclo__isnull=False
    )
    if alumno_ids is not None:
        alumnos = alumnos.filter(pk__in=list(alumno_ids))
    datos = {}
    ciclos = {}
    for pk, nombre, apellido, nacimiento, ancla in alumnos.values_list(
        "pk", "nombre", "apellido", "fecha_nacimiento", "fecha_inicio_ciclo"
    ):
        propios = _ciclos_en_ventana(ancla, hoy)
        if propios:
            datos[pk] = (nombre, apellido, nacimiento)
            ciclos[pk] = propios
    if not ciclos:
        return []
    ids = list(ciclos)

    # Asistencia: una ventana por alumno, del inicio del ciclo más viejo al
    # fin del más nuevo; después se reparte por ciclo en Python.
    dias = dias_asistidos_por_alumno(
        gimnasio, {pk: (c[0][0], c[-1][1]) for pk, c in ciclos.items()}
    )

    # Rutina vigente al inicio de cada ciclo: todas las activas que ya habían
    # arrancado, de la más nueva a la más vieja; la primera con
    # `fecha_inicio <= inicio` es la vigente.
    tope = max(c[-1][0] for c in ciclos.values())
    rutinas = {}
    for rutina_id, alumno_id, fecha_inicio in (
        RutinaAsignada.objects.for_gimnasio(gimnasio)
        .filter(alumno_id__in=ids, activa=True, fecha_inicio__lte=tope)
        .order_by("alumno_id", "-fecha_inicio", "-id")
        .values_list("id", "alumno_id", "fecha_inicio")
    ):
        rutinas.setdefault(alumno_id, []).append((fecha_inicio, rutina_id))

    vigente = {}  # (alumno_id, inicio) -> rutina_id
    for pk, propios in ciclos.items():
        for inicio, _ in propios:
            for fecha_inicio, rutina_id in rutinas.get(pk, ()):
                if fecha_inicio <= inicio:
                    vigente[(pk, inicio)] = rutina_id
                    break

    metas = {}  # rutina_id -> sesiones previstas
    if vigente:
        # `order_by()` vacío: el `Meta.ordering` se sumaría al DISTINCT.
        for rutina_id, _dia, _semana in (
            RutinaAsignadaItem.objects.filter(rutina_asignada_id__in=set(vigente.values()))
            .order_by()
            .values_list("rutina_asignada_id", "dia", "semana")
            .distinct()
        ):
            metas[rutina_id] = metas.get(rutina_id, 0) + 1

    deuda_desde = dict(
        Cuota.objects.for_gimnasio(gimnasio)
        .filter(alumno_id__in=ids, estado__in=Cuota.ESTADOS_IMPAGOS)
        .order_by()
        .values("alumno_id")
        .annotate(primera=Min("periodo_inicio"))
        .values_list("alumno_id", "primera")
    )

    ya_dados = set(
        PremioOtorgado.objects.for_gimnasio(gimnasio)
        .filter(alumno_id__in=ids, estado=PremioOtorgado.Estado.OTORGADO)
        .values_list("alumno_id", "periodo_inicio")
    )

    resultado = []
    for pk, propios in ciclos.items():
        nombre, apellido, nacimiento = datos[pk]
        for inicio, fin in propios:
            if (pk, inicio) in ya_dados:
                continue
            rutina_id = vigente.get((pk, inicio))
            meta = metas.get(rutina_id) if rutina_id is not None else None
            if not meta:
                continue
            asistidas = sum(1 for dia in dias[pk] if inicio <= dia <= fin)
            if asistidas < meta:
                continue
            primera_impaga = deuda_desde.get(pk)
            if primera_impaga is not None and primera_impaga <= fin:
                continue
            resultado.append(
                Candidato(
                    alumno_id=pk,
                    nombre=nombre,
                    apellido=apellido,
                    es_menor=not es_mayor_de_edad(nacimiento, hoy),
                    edad_sin_cargar=nacimiento is None,
                    inicio=inicio,
                    fin=fin,
                    meta=meta,
                    asistidas=asistidas,
                )
            )
    resultado.sort(
        key=lambda c: (c.apellido.lower(), c.nombre.lower(), c.alumno_id, c.inicio)
    )
    return resultado


def _otorgado_vigente(gimnasio, alumno_id, inicio):
    return (
        PremioOtorgado.objects.for_gimnasio(gimnasio)
        .filter(alumno_id=alumno_id, periodo_inicio=inicio,
                estado=PremioOtorgado.Estado.OTORGADO)
        .first()
    )


def _pesos(monto):
    """`$ 28.000` (o `$ 28.000,50`): como se lee un precio en Argentina."""
    entero = monto == monto.to_integral_value()
    texto = f"{monto:,.0f}" if entero else f"{monto:,.2f}"
    return "$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _mensaje(otorgado):
    """El texto de la novedad que recibe el alumno."""
    ciclo = (
        f"Completaste todas tus sesiones del ciclo del "
        f"{otorgado.periodo_inicio:%d/%m} al {otorgado.periodo_fin:%d/%m}."
    )
    if otorgado.tipo == Premio.Tipo.OBSEQUIO:
        return (
            f"{ciclo} Ganaste «{otorgado.nombre}»: {otorgado.texto}. "
            "Pedíselo al staff del gimnasio."
        )
    if otorgado.tipo == Premio.Tipo.PORCENTAJE:
        valor = otorgado.valor.normalize()
        cuanto = f"{valor:f}%"
    else:
        cuanto = _pesos(otorgado.valor)
    donde = (
        "tu próxima cuota" if otorgado.cuotas == 1
        else f"tus próximas {otorgado.cuotas} cuotas"
    )
    return f"{ciclo} Ganaste «{otorgado.nombre}»: {cuanto} de descuento en {donde}."


def otorgar(gimnasio, alumno_id, inicio, premio_id, perfil):
    """Da el premio `premio_id` al alumno por el ciclo que arranca en `inicio`.

    Devuelve `(otorgado, motivo)`; `otorgado` es `None` si no se pudo, y en
    ese caso no se escribió nada. Revalida con `candidatos` (alumno ACTIVO del
    gimnasio, ciclo cerrado dentro de la ventana, asistencia perfecta, sin
    deuda) y exige un `Premio` ACTIVO del gimnasio: el listado del staff pudo
    quedar viejo y un POST se puede forjar.

    Doble clic o dos pestañas: el segundo devuelve el otorgado existente sin
    crear otra novedad (si los dos llegan a la vez, lo resuelve el índice
    único parcial de `PremioOtorgado`). En ese camino SÍ se reintenta
    `aplicar_pendientes`: es idempotente, y si la aplicación del primer clic
    había fallado, el segundo la completa sin esperar al cron.

    En una transacción van sólo el otorgado y la novedad personal. La
    aplicación del descuento a las cuotas que ya existan corre DESPUÉS del
    commit y sin romper (`_aplicar_sin_romper`): si falla, el premio ya quedó
    guardado, no se retienen locks de cuotas y el cron `generar_pagos`
    (idempotente) lo completa en la próxima corrida.
    """
    existente = _otorgado_vigente(gimnasio, alumno_id, inicio)
    if existente is not None:
        _aplicar_sin_romper(existente)
        return existente, MOTIVO_YA_OTORGADO

    premio = Premio.objects.for_gimnasio(gimnasio).filter(pk=premio_id, activo=True).first()
    if premio is None:
        return None, MOTIVO_PREMIO_INVALIDO

    hoy = timezone.localdate()
    candidato = next(
        (c for c in candidatos(gimnasio, hoy=hoy, alumno_ids=[alumno_id])
         if c.inicio == inicio),
        None,
    )
    if candidato is None:
        return None, MOTIVO_NO_CALIFICA

    ganador = None
    with transaction.atomic():
        try:
            with transaction.atomic():
                otorgado = PremioOtorgado.objects.create(
                    gimnasio=gimnasio,
                    alumno_id=alumno_id,
                    premio=premio,
                    nombre=premio.nombre,
                    tipo=premio.tipo,
                    valor=premio.valor,
                    cuotas=premio.cuotas,
                    texto=premio.texto,
                    da_insignia=premio.da_insignia,
                    periodo_inicio=candidato.inicio,
                    periodo_fin=candidato.fin,
                    meta=candidato.meta,
                    asistidas=candidato.asistidas,
                    insignia_hasta=(
                        candidato.fin + timedelta(days=DIAS_CICLO)
                        if premio.da_insignia else None
                    ),
                    otorgado_por=perfil,
                )
        except IntegrityError:
            # Otra pestaña ganó entre la revalidación y el INSERT. Consulta
            # directa (no `_otorgado_vigente`): lo que importa es la base.
            ganador = (
                PremioOtorgado.objects.for_gimnasio(gimnasio)
                .filter(alumno_id=alumno_id, periodo_inicio=inicio,
                        estado=PremioOtorgado.Estado.OTORGADO)
                .first()
            )
            if ganador is None:
                raise
        else:
            Novedad.objects.create(
                gimnasio=gimnasio,
                alumno_id=alumno_id,
                titulo=TITULO_NOVEDAD,
                mensaje=_mensaje(otorgado),
                fecha_publicacion=hoy,
                visible_hasta=hoy + timedelta(days=DIAS_NOVEDAD),
            )
    if ganador is not None:
        _aplicar_sin_romper(ganador)
        return ganador, MOTIVO_YA_OTORGADO
    _aplicar_sin_romper(otorgado)
    return otorgado, MOTIVO_OTORGADO


def _aplicar_sin_romper(otorgado):
    """Aplica los descuentos pendientes del alumno del premio, fuera de la
    transacción de `otorgar`. Un fallo queda en el log y no se propaga: el
    premio ya está guardado y el cron lo completa (es idempotente)."""
    if otorgado.tipo not in TIPOS_DESCUENTO:
        return
    try:
        aplicar_pendientes(alumno_ids=[otorgado.alumno_id])
    except Exception:
        logger.exception(
            "Falló la aplicación del premio otorgado %s; la completa el cron.",
            otorgado.pk,
        )


def _descuento_para(tipo, valor, monto):
    """El descuento del premio sobre una cuota de `monto`, en centavos hacia
    abajo y nunca por encima del tope (`descuento_maximo`). Puede dar 0: en
    ese caso la cuota se saltea."""
    tope = descuento_maximo(monto)
    if tipo == Premio.Tipo.PORCENTAJE:
        bruto = (Decimal(monto) * Decimal(valor) / 100).quantize(CENTAVO, rounding=ROUND_DOWN)
    else:
        bruto = Decimal(valor).quantize(CENTAVO, rounding=ROUND_DOWN)
    return min(bruto, tope)


#: Resultados de `_aplicar_una`.
APLICADA, SALTEADA, AGOTADO = "aplicada", "salteada", "agotado"


def _aplicar_una(otorgado_id, cuota_pk, gimnasio_id, descuento, motivo, ahora):
    """Aplica el premio a UNA cuota, todo o nada.

    Bajo el lock del otorgado se recuenta: si otra corrida (el cron y la
    confirmación del staff a la vez) ya completó sus cuotas o lo anularon,
    devuelve `AGOTADO` sin tocar nada. Si `aplicar_descuento` no aplica (la
    cuota se pagó, se anuló o el staff le cargó un descuento en el medio),
    `SALTEADA`. Si el INSERT de la aplicación choca con la unicidad por
    cuota, se deshace el descuento recién escrito y también `SALTEADA`.
    """
    try:
        with transaction.atomic():
            fila = (
                PremioOtorgado.objects.select_for_update()
                .filter(pk=otorgado_id, estado=PremioOtorgado.Estado.OTORGADO)
                .values_list("cuotas", flat=True)
                .first()
            )
            if fila is None:
                return AGOTADO
            if PremioAplicacion.objects.filter(otorgado_id=otorgado_id).count() >= fila:
                return AGOTADO
            if not aplicar_descuento(
                cuota_pk, gimnasio_id, descuento, motivo, ahora,
                solo_si_sin_descuento=True,
            ):
                return SALTEADA
            PremioAplicacion.objects.create(
                otorgado_id=otorgado_id, cuota_id=cuota_pk, descuento=descuento
            )
    except IntegrityError:
        return SALTEADA
    return APLICADA


def aplicar_pendientes(hoy=None, alumno_ids=None):
    """Aplica los descuentos de premios ya otorgados a las cuotas que
    correspondan. Devuelve cuántas cuotas recibieron un descuento.

    Lo llama el cron `generar_pagos` (después de emitir las cuotas) y
    `otorgar`. Corre aunque el gimnasio haya apagado `reconocimientos_habilitado`
    (lo prometido se cumple) y sólo para alumnos ACTIVOS. No depende de que
    el generador haya creado cuotas en esta corrida: re-consulta todo
    (`bulk_create(ignore_conflicts=True)` no devuelve PKs en Postgres).

    `hoy` no filtra nada (dónde cae el descuento no depende de la fecha): está
    por simetría con `candidatos` y para quien quiera registrar la corrida.

    Lecturas en DOS queries, sin importar cuántos premios o cuotas haya: los
    otorgados con su conteo de aplicaciones anotado y las cuotas candidatas.
    Las escrituras sí son por asignación (cada una con su transacción); una
    asignación que revienta queda en el log y se sigue con la próxima.
    """
    del hoy  # ver docstring
    otorgados = (
        PremioOtorgado.objects.filter(
            estado=PremioOtorgado.Estado.OTORGADO,
            tipo__in=TIPOS_DESCUENTO,
            alumno__estado=Alumno.Estado.ACTIVO,
        )
        .annotate(n_aplicaciones=Count("aplicaciones"))
        .filter(n_aplicaciones__lt=F("cuotas"))
        .order_by("alumno_id", "periodo_inicio", "pk")
    )
    if alumno_ids is not None:
        otorgados = otorgados.filter(alumno_id__in=list(alumno_ids))
    otorgados = list(otorgados)
    if not otorgados:
        return 0

    por_alumno = {}
    for cuota_pk, alumno_id, periodo_inicio, monto in (
        Cuota.objects.filter(
            alumno_id__in={o.alumno_id for o in otorgados},
            estado__in=Cuota.ESTADOS_IMPAGOS,
            descuento=0,
            monto__gt=0,
            periodo_inicio__gt=min(o.periodo_fin for o in otorgados),
        )
        .filter(~Exists(PremioAplicacion.objects.filter(cuota=OuterRef("pk"))))
        .order_by("alumno_id", "periodo_inicio")
        .values_list("pk", "alumno_id", "periodo_inicio", "monto")
    ):
        por_alumno.setdefault(alumno_id, []).append((cuota_pk, periodo_inicio, monto))

    ahora = timezone.now()
    aplicadas = 0
    usadas = set()  # cuotas ya intentadas en esta corrida
    for otorgado in otorgados:
        faltan = otorgado.restantes
        motivo = f"Premio: {otorgado.nombre}"[:LARGO_MOTIVO]
        for cuota_pk, periodo_inicio, monto in por_alumno.get(otorgado.alumno_id, ()):
            if faltan <= 0:
                break
            if cuota_pk in usadas or periodo_inicio <= otorgado.periodo_fin:
                continue
            descuento = _descuento_para(otorgado.tipo, otorgado.valor, monto)
            if descuento <= 0:
                continue
            usadas.add(cuota_pk)
            try:
                resultado = _aplicar_una(
                    otorgado.pk, cuota_pk, otorgado.gimnasio_id, descuento, motivo, ahora
                )
            except Exception:
                # Una asignación rota (su transacción ya se deshizo) no frena a
                # las demás; el `IntegrityError` esperado se maneja adentro.
                logger.exception(
                    "Falló aplicar el premio otorgado %s a la cuota %s.",
                    otorgado.pk, cuota_pk,
                )
                continue
            if resultado == AGOTADO:
                break
            if resultado == APLICADA:
                aplicadas += 1
                faltan -= 1
    return aplicadas


def anular(gimnasio, otorgado_pk):
    """Anula un premio otorgado del gimnasio: frena las aplicaciones futuras
    (lo ya aplicado queda; el staff lo corrige en la cuota si quiere) y
    libera el ciclo para volver a darlo. Devuelve si lo anuló."""
    return bool(
        PremioOtorgado.objects.for_gimnasio(gimnasio)
        .filter(pk=otorgado_pk, estado=PremioOtorgado.Estado.OTORGADO)
        .update(estado=PremioOtorgado.Estado.ANULADO, modificado=timezone.now())
    )


def marcar_entregado(gimnasio, otorgado_pk):
    """Marca entregado un obsequio vigente del gimnasio (una sola vez: la
    fecha de la primera entrega no se pisa). Devuelve si lo marcó."""
    ahora = timezone.now()
    return bool(
        PremioOtorgado.objects.for_gimnasio(gimnasio)
        .filter(
            pk=otorgado_pk,
            estado=PremioOtorgado.Estado.OTORGADO,
            tipo=Premio.Tipo.OBSEQUIO,
            entregado_en__isnull=True,
        )
        .update(entregado_en=ahora, modificado=ahora)
    )
