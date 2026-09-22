"""
Lo que el panel de plataforma necesita saber de cada gimnasio, en una query.

Separado de `plataforma/views.py` (que solo arma contexto) y de
`plataforma/precios.py` (que es puro) por el mismo criterio que
`tenants/analitica.py`: acá vive todo lo que toca el ORM, y se testea sin
pasar por la vista.

**Regla de costo**: el monitor lista TODOS los gimnasios de la plataforma, así
que es justo donde un N+1 se paga en cada carga. `gimnasios_anotados()` es UNA
query y `filas_del_monitor()` no agrega ninguna: el precio y el estado se
calculan en Python sobre las filas ya traídas.

**Por qué `Subquery` y no `Count(...)`/`Max(...)` con filtro sobre el join**:
son TRES relaciones multivaluadas distintas (`alumnos`, `actividad` y
`pagos_plataforma`). Anotadas como join en el mismo queryset, el producto
cartesiano las multiplica entre sí: con dos pagos registrados, un gimnasio de
3 alumnos cuenta 6. Con subqueries correlacionadas cada una se calcula sola y
el resultado no depende de las otras. **Si agregás una cuarta anotación sobre
una relación multivaluada, va como `Subquery`.**
"""

from dataclasses import dataclass
from datetime import date

from django.db.models import (
    Count,
    DateField,
    IntegerField,
    Max,
    OuterRef,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from plataforma import cambio, precios
from plataforma.models import ActividadDiaria, PagoPlataforma
from plataforma.precios import EstadoPago
from tenants.models import Gimnasio, Perfil


@dataclass(frozen=True)
class FilaMonitor:
    """Una fila del monitor: el gimnasio más lo que la plataforma le cobra.

    `cubierto_hasta` es el último día ya pago (inclusivo), o `None` si al
    gimnasio todavía no se le registró ningún pago.
    """

    gimnasio: Gimnasio
    alumnos_activos: int
    precio_usd: int
    factura: bool
    estado: EstadoPago
    vencimiento: date | None
    cubierto_hasta: date | None
    dias_de_atraso: int
    ultimo_uso_staff: date | None


def inicio_efectivo(gimnasio):
    """Desde qué día se le cuenta la facturación a ese gimnasio.

    `Gimnasio.facturacion_inicio` cuando está cargado (el superadmin puede
    arrancarle la facturación a un gimnasio en otra fecha que la de alta, y la
    migración `tenants/0013` se lo estampó a todos los que ya existían para
    que prender la facturación no fuera retroactivo); si no, la fecha de alta.

    La fecha de alta se lee con `timezone.localtime(...)` y nunca con
    `gimnasio.creado.date()`: con `TIME_ZONE` en UTC-3, un gimnasio dado de
    alta a las 22:00 tiene `creado` fechado al día siguiente en UTC, y leerlo
    mal corre un día el fin de la prueba y todos los vencimientos que salen de
    él.
    """
    if gimnasio.facturacion_inicio:
        return gimnasio.facturacion_inicio
    return timezone.localtime(gimnasio.creado).date()


def gimnasios_anotados():
    """Todos los gimnasios con sus alumnos activos, el último día que los usó
    su staff y hasta qué día están pagos, en una sola query.

    `ultimo_uso_staff` es el último DÍA (no instante) que entró alguien del
    staff, tomado de `ActividadDiaria`. No sale de `User.last_login` a
    propósito: `last_login` solo se escribe al loguearse, así que un staff que
    deja la sesión abierta en la computadora del mostrador puede usar la app
    todos los días con un `last_login` de hace meses -- justo el gimnasio que
    el monitor marcaría como dormido.

    `cubierto_hasta` es el `Max(periodo_hasta)` de sus pagos, no el período
    del último pago CARGADO: el superadmin puede registrar un pago atrasado
    después de uno más nuevo, y lo que importa es hasta cuándo está cubierto.
    """
    # Import tardío: `plataforma` es la última app del orden de dependencia y
    # no debería importar el dominio a nivel de módulo -- mismo criterio que
    # `tenants/analitica.py`.
    from alumnos.models import Alumno

    alumnos_activos = Subquery(
        Alumno.objects.filter(gimnasio=OuterRef("pk"), estado=Alumno.Estado.ACTIVO)
        .order_by()
        .values("gimnasio")
        .annotate(total=Count("id"))
        .values("total"),
        output_field=IntegerField(),
    )
    ultimo_uso_staff = Subquery(
        ActividadDiaria.objects.filter(
            gimnasio=OuterRef("pk"), rol=Perfil.Rol.STAFF
        )
        .order_by()
        .values("gimnasio")
        .annotate(ultimo=Max("fecha"))
        .values("ultimo"),
        output_field=DateField(),
    )
    cubierto_hasta = Subquery(
        PagoPlataforma.objects.filter(gimnasio=OuterRef("pk"))
        .order_by()
        .values("gimnasio")
        .annotate(ultimo=Max("periodo_hasta"))
        .values("ultimo"),
        output_field=DateField(),
    )
    return Gimnasio.objects.annotate(
        # Sin el Coalesce un gimnasio sin alumnos anota NULL, y `None` no
        # entra en ninguna comparación de `precios.escalon_de`.
        alumnos_activos=Coalesce(alumnos_activos, Value(0)),
        ultimo_uso_staff=ultimo_uso_staff,
        # Acá NO va Coalesce: `None` es un valor con significado propio
        # ("nunca pagó") que `precios.periodo_siguiente` y `estado_pago` leen
        # para arrancar el primer período al terminar la prueba.
        cubierto_hasta=cubierto_hasta,
    ).order_by("nombre")


def periodo_a_cobrar(gimnasio):
    """`(desde, hasta)` del próximo período a cobrarle a ese gimnasio ya
    anotado. `hasta` es inclusivo.

    Vive acá y no en la vista para que el formulario de registrar un pago
    proponga exactamente el mismo período que el monitor usa para decir que
    está vencido: son la misma regla leída dos veces.
    """
    return precios.periodo_siguiente(
        inicio_efectivo(gimnasio), getattr(gimnasio, "cubierto_hasta", None)
    )


def fila_de_gimnasio(gimnasio, hoy=None):
    """Arma la fila de UN gimnasio ya anotado (no consulta la base).

    La usan el monitor y el detalle, para que las dos pantallas no puedan
    mostrar un precio o un estado distinto del mismo gimnasio. `hoy` es
    parámetro para testear con fechas fijas; por defecto
    `timezone.localdate()`, nunca `timezone.now().date()`.
    """
    if hoy is None:
        hoy = timezone.localdate()
    inicio = inicio_efectivo(gimnasio)
    # `getattr` y no `gimnasio.cubierto_hasta`: la ficha y el monitor lo traen
    # anotado, pero esta función también se llama con un `Gimnasio` pelado en
    # los tests, y ahí "sin anotación" y "sin pagos" significan lo mismo.
    cubierto_hasta = getattr(gimnasio, "cubierto_hasta", None)
    factura = gimnasio.facturacion_aplica
    alumnos_activos = gimnasio.alumnos_activos
    return FilaMonitor(
        gimnasio=gimnasio,
        alumnos_activos=alumnos_activos,
        precio_usd=precios.precio_usd(alumnos_activos),
        factura=factura,
        estado=precios.estado_pago(inicio, hoy, cubierto_hasta, exenta=not factura),
        vencimiento=precios.proximo_vencimiento(inicio, hoy, cubierto_hasta),
        cubierto_hasta=cubierto_hasta,
        dias_de_atraso=(
            precios.dias_de_atraso(inicio, hoy, cubierto_hasta) if factura else 0
        ),
        ultimo_uso_staff=gimnasio.ultimo_uso_staff,
    )


def filas_del_monitor(hoy=None):
    """El monitor completo: una fila por gimnasio, ordenado por nombre.

    `hoy` se resuelve UNA vez para todas las filas: pasárselo a cada una es lo
    que garantiza que la tabla entera esté fechada igual aunque la carga cruce
    la medianoche.
    """
    if hoy is None:
        hoy = timezone.localdate()
    return [fila_de_gimnasio(gimnasio, hoy) for gimnasio in gimnasios_anotados()]


def kpis(filas, cotizacion=None):
    """Los números de arriba del panel, calculados sobre las filas ya
    armadas (no vuelve a consultar nada).

    `ingreso_mensual_usd` y `alumnos_activos` cuentan solo a los gimnasios que
    de verdad se cobran. La demo tiene dos docenas de alumnos sembrados que no
    son de nadie: sumarlos infla la facturación esperada con plata que nadie va
    a pagar y hace parecer más grande a la plataforma de lo que es. Por eso la
    etiqueta en pantalla dice «(clientes)», para que el número no se lea como
    "todo lo que hay en la base".

    La `cotizacion` entra por parámetro y no se busca acá: la pide la vista
    UNA vez por request y la comparte con la ficha y con el formulario de
    pago. Sin cotización, `ingreso_mensual_ars` es `None` y la pantalla
    muestra solo dólares.
    """
    ingreso_usd = sum(fila.precio_usd for fila in filas if fila.factura)
    return {
        "clientes": sum(1 for fila in filas if fila.factura),
        "alumnos_activos": sum(
            fila.alumnos_activos for fila in filas if fila.factura
        ),
        "ingreso_mensual_usd": ingreso_usd,
        "ingreso_mensual_ars": cambio.pesos(ingreso_usd, cotizacion),
        "vencidos": sum(1 for fila in filas if fila.estado is EstadoPago.VENCIDA),
        "por_vencer": sum(1 for fila in filas if fila.estado is EstadoPago.POR_VENCER),
    }


def para_cobrar(filas):
    """Los gimnasios que hay que cobrar ahora: vencidos y por vencer, del más
    atrasado al que falta más.

    Los vencidos van primero porque su vencimiento es el más viejo, así que
    alcanza con ordenar por fecha -- no hace falta priorizar por estado.

    El `vencimiento is not None` no es defensa de más: un gimnasio con la
    facturación arrancando a futuro no tiene vencimiento, y sin el guard el
    `sorted` compara `None` contra una fecha y se cae con `TypeError`. Hoy ese
    gimnasio además está en PRUEBA (así que ya no entraría por el estado), pero
    las dos condiciones se decidieron en lugares distintos y no tienen por qué
    seguir coincidiendo.
    """
    pendientes = [
        fila
        for fila in filas
        if fila.estado in (EstadoPago.VENCIDA, EstadoPago.POR_VENCER)
        and fila.vencimiento is not None
    ]
    return sorted(pendientes, key=lambda fila: fila.vencimiento)
