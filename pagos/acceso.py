"""
El único lugar donde se decide si un alumno está bloqueado por falta de pago.

Vive acá y no en cada vista por el mismo motivo que
`alumnos/signals.py::sincronizar_acceso_con_estado` vive en una señal: la regla
se consulta desde seis superficies distintas (el portal, el detalle del día, las
dos escrituras sobre la rutina, la reserva de turnos y el panel del staff) y
repetirla en cada una garantiza que alguna quede desactualizada. El síntoma de
que eso pase es feo en las dos direcciones: un alumno que pagó y no puede
entrenar, o uno que debe hace meses y sigue entrando.

Tres decisiones que conviene no deshacer sin querer:

1. **La tolerancia se resuelve en Python, nunca como `periodo_inicio + columna`
   en el queryset.** Esa aritmética de fecha contra columna anda en Postgres y
   da resultados silenciosamente distintos en SQLite, que es donde corre toda
   la suite: los tests pasarían y producción se comportaría distinto. Como el
   gimnasio siempre se conoce en el punto de uso, la tolerancia es un escalar y
   el umbral se calcula antes de tocar la base.

2. **Nunca bloquea por cuotas anteriores a `fecha_activacion_bloqueo`.** Prender
   la función no puede ser retroactivo -- ver `tenants/signals.py`.

3. **Una sola query.** Esta función corre en cada request del portal del alumno;
   el contador del panel del staff NO la llama por fila (ver `contar_bloqueados`).
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Exists, OuterRef

from pagos.models import Cuota


@dataclass(frozen=True)
class Bloqueo:
    """Por qué está bloqueado un alumno. `None` significa "no lo está"."""

    cuota: Cuota
    dias_de_atraso: int

    @property
    def periodo_inicio(self):
        return self.cuota.periodo_inicio


def _umbral(gimnasio, hoy):
    """La fecha límite de `periodo_inicio` para bloquear, o `None` si el
    gimnasio no tiene el bloqueo prendido.

    Una cuota bloquea si arrancó hace `dias_tolerancia_pago` días o más, o sea
    si `periodo_inicio <= hoy - tolerancia`.
    """
    if gimnasio.dias_tolerancia_pago is None:
        return None
    if gimnasio.fecha_activacion_bloqueo is None:
        # Solo alcanzable si alguien escribió la tolerancia salteándose la
        # señal (un `QuerySet.update()`, o datos cargados a mano). Fail-open a
        # propósito: ante la duda no se le corta el acceso a nadie.
        return None
    return hoy - timedelta(days=gimnasio.dias_tolerancia_pago)


def bloqueo_de(alumno, *, hoy=None):
    """La cuota que hoy le bloquea el acceso a `alumno`, o `None`.

    Devuelve la MÁS VIEJA de las que bloquean: es la que el alumno tiene que
    saldar primero y la que corresponde mostrarle.
    """
    from django.utils import timezone

    hoy = hoy or timezone.localdate()
    gimnasio = alumno.gimnasio
    umbral = _umbral(gimnasio, hoy)
    if umbral is None:
        return None

    cuota = (
        Cuota.objects.filter(
            gimnasio=gimnasio,
            alumno=alumno,
            estado__in=Cuota.ESTADOS_IMPAGOS,
            periodo_inicio__lte=umbral,
            periodo_inicio__gte=gimnasio.fecha_activacion_bloqueo,
        )
        .order_by("periodo_inicio")
        .first()
    )
    if cuota is None:
        return None
    return Bloqueo(cuota=cuota, dias_de_atraso=(hoy - cuota.periodo_inicio).days)


def cuotas_impagas_de(alumno):
    """Todas las cuotas que el alumno tiene sin saldar, de la más vieja a la
    más nueva.

    El portal las lista TODAS, no solo la del ciclo vigente. Si lo que bloquea
    es una cuota anterior y en pantalla solo aparece la actual, el alumno le
    sube el comprobante a la equivocada y sigue bloqueado sin entender por qué.
    """
    return (
        Cuota.objects.filter(
            gimnasio=alumno.gimnasio,
            alumno=alumno,
            estado__in=Cuota.ESTADOS_IMPAGOS,
        )
        .order_by("periodo_inicio")
    )


def contar_bloqueados(gimnasio, *, hoy=None):
    """Cuántos alumnos activos del gimnasio están bloqueados hoy.

    **Una sola consulta agregada, no `bloqueo_de` por fila.** Este número va en
    el panel del staff, que es exactamente donde un N+1 se paga caro: es el
    mismo patrón que ya causó un 502 en producción con el importador.
    """
    from django.utils import timezone

    from alumnos.models import Alumno

    hoy = hoy or timezone.localdate()
    umbral = _umbral(gimnasio, hoy)
    if umbral is None:
        return 0

    bloquea = Cuota.objects.filter(
        alumno=OuterRef("pk"),
        gimnasio=gimnasio,
        estado__in=Cuota.ESTADOS_IMPAGOS,
        periodo_inicio__lte=umbral,
        periodo_inicio__gte=gimnasio.fecha_activacion_bloqueo,
    )
    return (
        Alumno.objects.for_gimnasio(gimnasio)
        .filter(estado=Alumno.Estado.ACTIVO)
        .filter(Exists(bloquea))
        .count()
    )
