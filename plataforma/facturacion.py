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
son dos relaciones multivaluadas distintas (`alumnos` y `perfiles`). Anotadas
las dos como join en el mismo queryset, el producto cartesiano multiplica el
conteo de alumnos por la cantidad de perfiles del gimnasio. Con subqueries
correlacionadas cada una se calcula sola y el resultado no depende de la otra.
"""

from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import (
    Count,
    DateTimeField,
    IntegerField,
    Max,
    OuterRef,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from plataforma import precios
from plataforma.precios import EstadoPago
from tenants.models import Gimnasio, Perfil


@dataclass(frozen=True)
class FilaMonitor:
    """Una fila del monitor: el gimnasio más lo que la plataforma le cobra.

    `cubierto_hasta` es siempre `None` en Fase 1 (todavía no hay modelo de
    pagos de plataforma). Queda en la fila desde ahora para que Fase 2 solo
    tenga que agregar la anotación: ni las pantallas ni `precios` cambian.
    """

    gimnasio: Gimnasio
    alumnos_activos: int
    precio_usd: int
    factura: bool
    estado: EstadoPago
    vencimiento: date | None
    cubierto_hasta: date | None
    dias_de_atraso: int
    ultimo_uso_staff: datetime | None


def inicio_efectivo(gimnasio):
    """Desde qué día se le cuenta la facturación a ese gimnasio.

    Hoy es la fecha de alta en hora LOCAL. `timezone.localtime(...)` y nunca
    `gimnasio.creado.date()`: con `TIME_ZONE` en UTC-3, un gimnasio dado de
    alta a las 22:00 tiene `creado` fechado al día siguiente en UTC, y leerlo
    mal corre un día el fin de la prueba y todos los vencimientos que salen
    de él.

    Fase 2 agrega `Gimnasio.facturacion_inicio` (para arrancarle la
    facturación a un gimnasio en otra fecha que la de alta) y esta función
    pasa a devolverlo cuando esté cargado. Es el único lugar que hay que
    tocar.
    """
    return timezone.localtime(gimnasio.creado).date()


def facturacion_aplica(gimnasio):
    """Si a ese gimnasio se le cobra o no.

    Hoy la única exención es la cuenta de demostración compartida, que no es
    de nadie. Fase 2 agrega `Gimnasio.facturacion_exenta` (un gimnasio real
    que el dueño del producto decide no cobrar) y esta función pasa a mirar
    las dos cosas -- por eso es una función y no un `not gimnasio.es_demo`
    repetido en cada llamador.
    """
    return not gimnasio.es_demo


def gimnasios_anotados():
    """Todos los gimnasios con sus alumnos activos y el último ingreso de su
    staff, en una sola query.

    `ultimo_uso_staff` sale hoy de `User.last_login`, que es lo único que ya
    se registra: dice cuándo entró por última vez alguien del gimnasio, no
    cuánto lo usa. Fase 4 lo reemplaza por el máximo de `ActividadDiaria` de
    rol staff, sin que cambie ni el nombre de la anotación ni las pantallas.
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
        Perfil.objects.filter(gimnasio=OuterRef("pk"), rol=Perfil.Rol.STAFF)
        .order_by()
        .values("gimnasio")
        .annotate(ultimo=Max("usuario__last_login"))
        .values("ultimo"),
        output_field=DateTimeField(),
    )
    return Gimnasio.objects.annotate(
        # Sin el Coalesce un gimnasio sin alumnos anota NULL, y `None` no
        # entra en ninguna comparación de `precios.escalon_de`.
        alumnos_activos=Coalesce(alumnos_activos, Value(0)),
        ultimo_uso_staff=ultimo_uso_staff,
    ).order_by("nombre")


def fila_de_gimnasio(gimnasio, hoy=None):
    """Arma la fila de UN gimnasio ya anotado (no consulta la base).

    La usan el monitor y el detalle, para que las dos pantallas no puedan
    mostrar un precio o un estado distinto del mismo gimnasio. `hoy` es
    parámetro para testear con fechas fijas; por defecto
    `timezone.localdate()`, nunca `timezone.now().date()`.
    """
    hoy = hoy or timezone.localdate()
    inicio = inicio_efectivo(gimnasio)
    # Fase 2 anota `cubierto_hasta` en `gimnasios_anotados()`; hasta entonces
    # no existe ningún pago registrado y todos los gimnasios lo tienen en
    # None. Se lee con `getattr` para que agregar la anotación sea el único
    # cambio necesario.
    cubierto_hasta = getattr(gimnasio, "cubierto_hasta", None)
    factura = facturacion_aplica(gimnasio)
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
    hoy = hoy or timezone.localdate()
    return [fila_de_gimnasio(gimnasio, hoy) for gimnasio in gimnasios_anotados()]


def kpis(filas):
    """Los números de arriba del panel, calculados sobre las filas ya
    armadas (no vuelve a consultar nada).

    `ingreso_mensual_usd` suma solo a los gimnasios que de verdad se cobran:
    meter la demo ahí infla la facturación esperada con plata que nadie va a
    pagar.
    """
    return {
        "clientes": sum(1 for fila in filas if fila.factura),
        "alumnos_activos": sum(fila.alumnos_activos for fila in filas),
        "ingreso_mensual_usd": sum(fila.precio_usd for fila in filas if fila.factura),
        "vencidos": sum(1 for fila in filas if fila.estado is EstadoPago.VENCIDA),
        "por_vencer": sum(1 for fila in filas if fila.estado is EstadoPago.POR_VENCER),
    }


def para_cobrar(filas):
    """Los gimnasios que hay que cobrar ahora: vencidos y por vencer, del más
    atrasado al que falta más.

    Los vencidos van primero porque su vencimiento es el más viejo, así que
    alcanza con ordenar por fecha -- no hace falta priorizar por estado.
    """
    pendientes = [
        fila
        for fila in filas
        if fila.estado in (EstadoPago.VENCIDA, EstadoPago.POR_VENCER)
    ]
    return sorted(pendientes, key=lambda fila: fila.vencimiento)
