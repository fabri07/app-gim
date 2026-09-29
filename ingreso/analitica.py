"""Reservas contra ingresos reales, para el panel del staff.

La grilla «Asistencia por día y horario» del panel cuenta RESERVAS: lo que la
gente dijo que iba a hacer. Con un lector en la puerta existe además lo que
hizo. Esto pone las dos cosas lado a lado y lista a quién entra sin reserva.

Costo fijo, porque corre en cada carga del panel: dos agregados para las
grillas y tres queries para la lista (configuración de turnos, ingresos de la
semana, reservas de la semana). Nada por fila ni por alumno.
"""

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import ExtractHour, ExtractWeekDay
from django.utils import timezone

from ingreso.models import EventoIngreso
from ingreso.models import ConfiguracionIngreso
from ingreso.presentacion import inicio_del_dia
from tenants.analitica import grilla_de_calor

#: Ventana de la comparación. La misma para las dos grillas: comparar todo el
#: historial de reservas contra las semanas que lleva conectada la puerta
#: haría que las reservas «ganen» en todas las celdas.
SEMANAS_COMPARACION = 8

#: Ventana de la lista «Vinieron sin reserva»: es para actuar esta semana, no
#: un ranking histórico.
DIAS_SIN_RESERVA = 7

#: Cuántos alumnos lista la tarjeta antes del «y N más».
TOPE_SIN_RESERVA = 10


def _ingresos(gimnasio):
    """Los ingresos que cuentan como «vino»: pasó y de entrada. Mismo criterio
    que `presentacion.resumen_para_el_panel`."""
    return EventoIngreso.objects.for_gimnasio(gimnasio).filter(
        resultado=EventoIngreso.Resultado.PERMITIDO,
        sentido=EventoIngreso.Sentido.ENTRADA,
    )


def reservas_contra_ingresos(gimnasio, *, hoy=None):
    """Dos grillas día × hora de las últimas `SEMANAS_COMPARACION` semanas
    (hoy incluido), con las MISMAS columnas y la MISMA escala de color.

    La hora del ingreso sale de `momento` convertido a hora local: Django
    extrae con la zona activa (`TIME_ZONE`), así que un ingreso de las 22:30
    de Buenos Aires cae en las 22 y en su día, no en el siguiente de UTC.
    """
    from turnos.models import Reserva

    hoy = hoy or timezone.localdate()
    desde = hoy - timedelta(days=SEMANAS_COMPARACION * 7 - 1)

    reservas = {
        (f["dia"], f["hora"]): f["total"]
        for f in Reserva.objects.for_gimnasio(gimnasio)
        .filter(fecha__gte=desde, fecha__lte=hoy)
        .annotate(dia=ExtractWeekDay("fecha"), hora=ExtractHour("hora_inicio"))
        .values("dia", "hora")
        .annotate(total=Count("pk"))
    }
    ingresos = {
        (f["dia"], f["hora"]): f["total"]
        for f in _ingresos(gimnasio)
        .filter(
            momento__gte=inicio_del_dia(desde),
            momento__lt=inicio_del_dia(hoy + timedelta(days=1)),
        )
        .annotate(dia=ExtractWeekDay("momento"), hora=ExtractHour("momento"))
        .values("dia", "hora")
        .annotate(total=Count("pk"))
    }

    horas = sorted({hora for (_dia, hora) in reservas} | {hora for (_dia, hora) in ingresos})
    maximo = max([*reservas.values(), *ingresos.values()], default=0)
    return {
        "semanas": SEMANAS_COMPARACION,
        "reservas": grilla_de_calor(reservas, horas=horas, maximo=maximo),
        "ingresos": grilla_de_calor(ingresos, horas=horas, maximo=maximo),
        "total_reservas": sum(reservas.values()),
        "total_ingresos": sum(ingresos.values()),
    }


def sin_reserva(gimnasio, *, ahora=None):
    """Alumnos que entraron por la puerta sin un turno en curso en los últimos
    `DIAS_SIN_RESERVA` días, de más a menos veces.

    «Sin reserva» es EXACTAMENTE lo que la puerta llamaría `sin_reserva`
    (`turnos.services.ReservasDelLote.vigente`: vale llegar hasta
    `TOLERANCIA_INGRESO` antes del turno y mientras dure). Si la regla se
    escribiera de nuevo acá, el panel y la puerta podrían no estar de acuerdo
    sobre la misma persona.

    Las reservas se traen por RANGO de fechas y no por momento
    (`reservas_vigentes_en_momentos`): acá los momentos son los que guardó el
    servidor dentro de una ventana acotada, así que no existe el reloj
    reseteado que obliga a la puerta a acotar por lectura, y un OR de miles de
    ventanas sería peor que una semana de reservas.
    """
    from turnos.models import Reserva
    from turnos.models import obtener_configuracion as configuracion_de_turnos
    from turnos.services import ReservasDelLote

    ahora = ahora or timezone.now()
    hoy = timezone.localdate(ahora)
    desde = hoy - timedelta(days=DIAS_SIN_RESERVA - 1)

    eventos = list(
        _ingresos(gimnasio)
        .filter(alumno__isnull=False, momento__gte=inicio_del_dia(desde), momento__lte=ahora)
        .values_list("alumno_id", "alumno__nombre", "alumno__apellido", "momento")
    )
    total_ingresos = len(eventos)
    if not eventos:
        return {"dias": DIAS_SIN_RESERVA, "alumnos": [], "mas": 0,
                "total_ingresos": 0, "total_sin_reserva": 0}

    config = configuracion_de_turnos(gimnasio)
    por_alumno = {}
    # Un día de margen para cada lado: un turno de las 23:30 sigue en curso
    # pasada la medianoche, y se puede entrar 15 minutos antes de uno de 00:00.
    for reserva in Reserva.objects.for_gimnasio(gimnasio).filter(
        alumno_id__in={e[0] for e in eventos},
        fecha__gte=desde - timedelta(days=1),
        fecha__lte=hoy + timedelta(days=1),
    ):
        por_alumno.setdefault(reserva.alumno_id, []).append(reserva)
    lote = ReservasDelLote(config.duracion_minutos, por_alumno)

    resumen = {}
    for alumno_id, nombre, apellido, momento in eventos:
        if lote.vigente(alumno_id, momento):
            continue
        fila = resumen.setdefault(
            alumno_id,
            {"pk": alumno_id, "nombre": f"{nombre} {apellido}", "veces": 0, "ultima": momento},
        )
        fila["veces"] += 1
        fila["ultima"] = max(fila["ultima"], momento)

    alumnos = sorted(resumen.values(), key=lambda f: (-f["veces"], -f["ultima"].timestamp()))
    return {
        "dias": DIAS_SIN_RESERVA,
        "alumnos": alumnos[:TOPE_SIN_RESERVA],
        "mas": max(0, len(alumnos) - TOPE_SIN_RESERVA),
        "total_ingresos": total_ingresos,
        "total_sin_reserva": sum(f["veces"] for f in alumnos),
    }


def para_el_panel(gimnasio):
    """El contexto de la tarjeta, o `None` si no tiene nada que comparar.

    La lista «sin reserva» sale solo si el gimnasio usa turnos (cargó algún
    horario): en uno que no los usa, TODOS entran sin reserva y la lista sería
    el padrón entero, ruido puro.
    """
    from turnos.models import HorarioAtencion

    usa_turnos = HorarioAtencion.objects.for_gimnasio(gimnasio).exists()
    comparacion = reservas_contra_ingresos(gimnasio)
    if not comparacion["total_ingresos"] and not usa_turnos:
        return None
    return {
        "comparacion": comparacion,
        "usa_turnos": usa_turnos,
        # Leído y no `obtener_configuracion`: el panel no crea filas.
        "exige_reserva": usa_turnos
        and ConfiguracionIngreso.objects.filter(gimnasio=gimnasio, exige_reserva=True).exists(),
        "sin_reserva": sin_reserva(gimnasio) if usa_turnos else None,
    }
