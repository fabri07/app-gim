"""
Qué le cobra la plataforma a cada gimnasio y cuándo.

Módulo **Django-free a propósito** (sin ORM, sin `timezone`, sin settings),
mismo criterio que `pagos.models.ciclo_vigente` o `rutinas.agrupacion`: la
regla de cobro se testea con fechas literales y `SimpleTestCase`, sin base de
datos de por medio. El "hoy" siempre lo pasa quien llama, que es el único que
sabe leerlo en hora local (ver `plataforma.facturacion`).

El modelo comercial (decisión cerrada con el dueño del producto):

- El precio es en USD y depende de la cantidad de alumnos **ACTIVOS** del
  gimnasio, por escalones: hasta 100 → 10, 101 a 300 → 15, más de 300 → 20.
  Los bordes son inclusivos: 100 alumnos exactos pagan 10 y 300 exactos pagan
  15.
- Los primeros `DIAS_GRATIS` días desde el alta son gratis, así que el primer
  cobro vence el día 30. De ahí en adelante se cobra cada `DIAS_CICLO` días.
- `DIAS_AVISO_COBRO` días antes de un vencimiento el gimnasio entra en
  POR_VENCER: es lo que llena la tarjeta «Para cobrar esta semana» del panel.

Ojo con la simetría con `pagos`: acá `DIAS_CICLO` es 30 (lo que la PLATAFORMA
le cobra al gimnasio) y en `pagos` es 28 (lo que el gimnasio le cobra a sus
alumnos). Son dos negocios distintos y las dos constantes tienen que poder
moverse por separado.
"""

import enum
from datetime import timedelta

#: Días que dura un período de facturación de la plataforma.
DIAS_CICLO = 30

#: Días de prueba gratis desde el alta del gimnasio. El primer cobro vence
#: justo cuando se terminan.
DIAS_GRATIS = 30

#: Con cuántos días de anticipación un vencimiento entra en «Para cobrar».
DIAS_AVISO_COBRO = 7

#: `(tope de alumnos activos, precio en USD)`, de menor a mayor. El tope es
#: INCLUSIVO y `None` significa "sin tope" (tiene que ser el último).
ESCALONES = (
    (100, 10),
    (300, 15),
    (None, 20),
)


class EstadoPago(enum.Enum):
    """En qué situación de cobro está un gimnasio.

    Cada estado trae su etiqueta en castellano y el modificador de `.badge`
    con el que se pinta. Van acá y no en cada template porque el mismo estado
    se muestra en el monitor y en el detalle del gimnasio: separados, las dos
    pantallas pueden terminar diciendo cosas distintas del mismo gimnasio.
    """

    EXENTA = ("Exenta", "neutro")
    PRUEBA = ("En prueba", "neutro")
    AL_DIA = ("Al día", "ok")
    POR_VENCER = ("Por vencer", "alerta")
    VENCIDA = ("Vencida", "riesgo")

    def __init__(self, etiqueta, badge):
        self.etiqueta = etiqueta
        self.badge = badge


def escalon_de(alumnos_activos):
    """`(tope, precio_usd)` del escalón en el que cae ese gimnasio.

    Se expone además de `precio_usd` para poder mostrar el tramo completo
    ("hasta 300 alumnos") sin repetir la tabla en la pantalla.
    """
    for tope, precio in ESCALONES:
        if tope is None or alumnos_activos <= tope:
            return tope, precio
    # Inalcanzable mientras el último escalón tenga tope None; queda como
    # red de seguridad para que un ESCALONES mal editado falle a los gritos.
    raise ValueError("ESCALONES tiene que terminar con un escalón sin tope")


def precio_usd(alumnos_activos):
    """Cuánto paga por mes un gimnasio con esa cantidad de alumnos activos."""
    return escalon_de(alumnos_activos)[1]


def fin_de_prueba(inicio):
    """El día en que se termina la prueba gratis, que es el día en que vence
    el primer cobro."""
    return inicio + timedelta(days=DIAS_GRATIS)


def periodo_siguiente(inicio, cubierto_hasta):
    """`(desde, hasta)` del primer período todavía NO cubierto por un pago.

    `hasta` es **inclusivo**, igual que `Cuota.periodo_fin`: un período de 30
    días que arranca el 31/1 termina el 1/3. Sin pagos registrados
    (`cubierto_hasta is None`) el primer período arranca cuando se termina la
    prueba.
    """
    if cubierto_hasta is None:
        desde = fin_de_prueba(inicio)
    else:
        desde = cubierto_hasta + timedelta(days=1)
    return desde, desde + timedelta(days=DIAS_CICLO - 1)


def proximo_vencimiento(inicio, hoy, cubierto_hasta):
    """Cuándo hay que cobrarle a ese gimnasio.

    Es el primer día del período no cubierto: el gimnasio paga por adelantado,
    así que el período arranca el mismo día en que vence el cobro.

    Con la facturación arrancando a futuro (`hoy < inicio`, que el superadmin
    puede dejar así desde «Editar facturación») el vencimiento es el fin de la
    prueba, o sea `inicio + DIAS_GRATIS`. **Antes devolvía `None` y era peor**:
    el gimnasio aparecía en el panel con un guion donde va la fecha, que se lee
    como "no sé" o como un dato roto. La prueba de ese gimnasio ya tiene fecha
    de fin conocida; mostrarla no es cobrarle por adelantado, es decir cuándo
    empieza a correrle el reloj. El estado sigue siendo PRUEBA
    (`inicio + 30` nunca cae dentro de los `DIAS_AVISO_COBRO` días siguientes
    a un `hoy` anterior a `inicio`).
    """
    if hoy < inicio:
        return fin_de_prueba(inicio)
    return periodo_siguiente(inicio, cubierto_hasta)[0]


def estado_pago(inicio, hoy, cubierto_hasta, *, exenta=False):
    """En qué `EstadoPago` está el gimnasio al día `hoy`.

    El orden de los cortes importa: primero la exención (la cuenta de demo no
    se cobra nunca, y sin este corte aparecería como el gimnasio más atrasado
    de todos), después el vencimiento pasado, después el aviso. PRUEBA es lo
    que queda cuando el gimnasio nunca pagó y el primer vencimiento todavía
    está lejos; AL_DIA, cuando ya pagó al menos un período.
    """
    if exenta:
        return EstadoPago.EXENTA
    # `proximo_vencimiento` siempre devuelve una fecha: con la facturación
    # arrancando a futuro es el fin de la prueba, que está lo bastante lejos
    # como para que los dos cortes de abajo no lo alcancen y el gimnasio caiga
    # solo en PRUEBA.
    vencimiento = proximo_vencimiento(inicio, hoy, cubierto_hasta)
    if vencimiento < hoy:
        return EstadoPago.VENCIDA
    if vencimiento <= hoy + timedelta(days=DIAS_AVISO_COBRO):
        return EstadoPago.POR_VENCER
    return EstadoPago.PRUEBA if cubierto_hasta is None else EstadoPago.AL_DIA


def dias_de_atraso(inicio, hoy, cubierto_hasta):
    """Cuántos días hace que venció el cobro pendiente (0 si no venció).

    El día del vencimiento todavía no es atraso -- mismo criterio que el
    vencimiento de una cuota en `pagos`: se vence al día siguiente.
    """
    vencimiento = proximo_vencimiento(inicio, hoy, cubierto_hasta)
    if vencimiento >= hoy:
        return 0
    return (hoy - vencimiento).days
