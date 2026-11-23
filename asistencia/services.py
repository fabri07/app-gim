"""Qué días vino cada alumno, cuántas sesiones le tocan y cómo va el ciclo.

**Por qué la unión de dos fuentes.** «Vino a entrenar» sale de (1) los ingresos
del lector de la puerta (`EventoIngreso` PERMITIDO + ENTRADA) y (2) las marcas
`Asistencia` que carga el staff (turno reservado o a mano). Un gimnasio sin
lector sólo tiene la segunda; uno con lector tiene las dos y un mismo día no
puede contar doble: por eso se unen como CONJUNTO de fechas locales. Acá sólo
se LEE; nunca se escribe un evento del lector.

**Lo que NO cuenta.** El «día entrenado» que el alumno tilda solo en su portal
(`RutinaAsignadaDiaCompletado`) es declarativo y no verificable: si contara,
el ranking premiaría al que más tilda y no al que más viene.

**La meta** es el ritmo semanal del plan x 4 semanas, medido con los pares
(día, semana) que realmente tiene la rutina vigente AL INICIO del ciclo (un
cambio de plan a mitad de ciclo no mueve la meta). Sin rutina, o con una
rutina vacía, no hay meta (`None`).

Todos los días son LOCALES (`TIME_ZONE`): un ingreso de las 22:30 en Buenos
Aires es del día de Buenos Aires aunque en UTC ya sea el siguiente.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

from alumnos.models import Alumno
from asistencia.models import Asistencia
from ingreso.models import EventoIngreso
from pagos.models import ciclo_vigente
from rutinas.models import RutinaAsignada
from rutinas.progreso import sesiones_previstas

#: Cuántos días hacia atrás se puede marcar/cargar una asistencia a mano.
MAX_DIAS_ATRAS = 35


def _dias_por_fuente(gimnasio, ventanas):
    """`(lector, marcas)`: dos dicts `{alumno_id: set(fechas)}`. DOS queries.

    Cada alumno se filtra por su propia ventana `(inicio, fin)` inclusiva; los
    que no tienen nada quedan con conjunto vacío.
    """
    lector = {pk: set() for pk in ventanas}
    marcas = {pk: set() for pk in ventanas}
    if not ventanas:
        return lector, marcas

    inicio = min(v[0] for v in ventanas.values())
    fin = max(v[1] for v in ventanas.values())
    ids = list(ventanas)
    desde = timezone.make_aware(
        datetime(inicio.year, inicio.month, inicio.day)
    )
    sig = fin + timedelta(days=1)
    hasta = timezone.make_aware(datetime(sig.year, sig.month, sig.day))

    ingresos = (
        EventoIngreso.objects.for_gimnasio(gimnasio)
        .filter(
            resultado=EventoIngreso.Resultado.PERMITIDO,
            sentido=EventoIngreso.Sentido.ENTRADA,
            alumno_id__in=ids,
            momento__gte=desde,
            momento__lt=hasta,
        )
        .values_list("alumno_id", "momento")
    )
    for alumno_id, momento in ingresos:
        dia = timezone.localtime(momento).date()
        ini, fi = ventanas[alumno_id]
        if ini <= dia <= fi:
            lector[alumno_id].add(dia)

    registradas = (
        Asistencia.objects.for_gimnasio(gimnasio)
        .filter(alumno_id__in=ids, fecha__gte=inicio, fecha__lte=fin)
        .values_list("alumno_id", "fecha")
    )
    for alumno_id, dia in registradas:
        ini, fi = ventanas[alumno_id]
        if ini <= dia <= fi:
            marcas[alumno_id].add(dia)
    return lector, marcas


def dias_por_fuente(gimnasio, ventanas):
    """`(lector, marcas)` por alumno, separados. DOS queries. Lo usa «Pasar
    lista», que necesita distinguir el ingreso del lector de la marca del staff."""
    return _dias_por_fuente(gimnasio, ventanas)


def dias_asistidos_por_alumno(gimnasio, ventanas):
    """`{alumno_id: set(fechas)}` con la unión lector + marcas. DOS queries,
    sin importar cuántos alumnos haya."""
    lector, marcas = _dias_por_fuente(gimnasio, ventanas)
    return {pk: lector[pk] | marcas[pk] for pk in ventanas}


def dias_asistidos(alumno, inicio, fin):
    """Los días (set de fechas) que vino `alumno` entre `inicio` y `fin`,
    ambos inclusive."""
    return dias_asistidos_por_alumno(
        alumno.gimnasio, {alumno.pk: (inicio, fin)}
    )[alumno.pk]


def rutina_vigente_en(alumno, fecha):
    """La rutina que el alumno tenía vigente en `fecha`, o `None`. Misma regla
    y mismo orden que `RutinaAsignada.vigente_de`, pero a una fecha dada."""
    return (
        RutinaAsignada.objects.filter(
            alumno=alumno, activa=True, fecha_inicio__lte=fecha
        )
        .order_by("-fecha_inicio", "-id")
        .first()
    )


def meta_del_ciclo(alumno, inicio):
    """Sesiones previstas del ciclo que arranca en `inicio`, o `None` si no
    había rutina vigente ese día o no tiene ninguna sesión."""
    rutina = rutina_vigente_en(alumno, inicio)
    if rutina is None:
        return None
    return len(sesiones_previstas(rutina)) or None


@dataclass(frozen=True)
class ResumenCiclo:
    inicio: date
    fin: date
    meta: int | None
    dias: frozenset
    dias_lector: frozenset

    @property
    def asistidos(self):
        return len(self.dias)

    @property
    def faltan(self):
        if self.meta is None:
            return None
        return max(0, self.meta - self.asistidos)


def resumen_del_ciclo(alumno, hoy=None):
    """Cómo viene el alumno en su ciclo de 28 días vigente, o `None` si no está
    activo o no tiene ciclo (sin ancla o ancla futura). Sólo cuenta hasta `hoy`."""
    if alumno.estado != Alumno.Estado.ACTIVO:
        return None
    hoy = hoy or timezone.localdate()
    ciclo = ciclo_vigente(alumno.fecha_inicio_ciclo, hoy)
    if ciclo is None:
        return None
    inicio, fin = ciclo
    lector, marcas = _dias_por_fuente(
        alumno.gimnasio, {alumno.pk: (inicio, min(fin, hoy))}
    )
    return ResumenCiclo(
        inicio=inicio,
        fin=fin,
        meta=meta_del_ciclo(alumno, inicio),
        dias=frozenset(lector[alumno.pk] | marcas[alumno.pk]),
        dias_lector=frozenset(lector[alumno.pk]),
    )


@dataclass(frozen=True)
class DatosFicha:
    """Lo que dibuja la tarjeta «Asistencia» de la ficha del alumno."""

    resumen: ResumenCiclo | None
    #: `[(fecha, marca|None, vino_por_lector)]`, del día más nuevo al más viejo.
    dias: list


def datos_de_la_ficha(alumno, hoy=None):
    """El resumen del ciclo vigente más cada día asistido con su marca (si la
    cargó el staff) y si hubo ingreso del lector. Costo fijo: las queries del
    resumen más UNA por las marcas, con quien las cargó en el mismo JOIN."""
    hoy = hoy or timezone.localdate()
    resumen = resumen_del_ciclo(alumno, hoy)
    if resumen is None:
        return DatosFicha(resumen=None, dias=[])
    marcas = {
        m.fecha: m
        for m in Asistencia.objects.for_gimnasio(alumno.gimnasio)
        .filter(
            alumno=alumno, fecha__gte=resumen.inicio, fecha__lte=min(resumen.fin, hoy)
        )
        .select_related("registrada_por__usuario")
    }
    dias = [
        (dia, marcas.get(dia), dia in resumen.dias_lector)
        for dia in sorted(resumen.dias, reverse=True)
    ]
    return DatosFicha(resumen=resumen, dias=dias)
