"""
Servicios de dominio de `turnos`: lógica pura (sin request/vistas), 100%
testeable con `TestCase` normal.

Todas las fechas/horas se guardan y comparan como hora LOCAL de Argentina
(`TIME_ZONE = "America/Argentina/Buenos_Aires"` en `config/settings.py`;
todos los tenants son gimnasios argentinos, no hace falta lógica de timezone
por tenant). "Ahora" se obtiene siempre con `timezone.localtime()` /
`timezone.localdate()` -- nunca `datetime.now()` / `date.today()` naive, para
no depender de la timezone del servidor.

Este módulo no valida permisos ni resuelve el gimnasio del request -- eso es
responsabilidad de las vistas (Task 4). Acá solo vive la lógica de negocio:
cortar horarios de atención en franjas de turno, calcular cupos, crear y
cancelar reservas, y limpiar reservas que quedaron "desencajadas" tras un
cambio de configuración.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from .models import ConfiguracionTurnos, CupoExcepcion, HorarioAtencion, Reserva, obtener_configuracion

CIERRE_RESERVA = timedelta(hours=1)


def _ahora_local() -> datetime:
    """'Ahora' como `datetime` NAIVE en hora local de Argentina.

    Se usa `timezone.localtime()` (nunca `datetime.now()`) y se le quita el
    `tzinfo` para poder compararlo directamente contra los `datetime` naive
    que arma `datetime.combine(fecha, hora_inicio)` en el resto del módulo
    (los campos `DateField`/`TimeField` no llevan tzinfo propio).
    """
    return timezone.localtime().replace(tzinfo=None)


@dataclass(frozen=True)
class Franja:
    fecha: date
    hora_inicio: time
    hora_fin: time
    vacantes: int
    ocupadas: int
    reservada_por_mi: bool = False

    @property
    def llena(self) -> bool:
        return self.ocupadas >= self.vacantes

    @property
    def pasada(self) -> bool:
        """True si el instante de inicio ya pasó (vs. timezone.localtime())."""
        inicio = datetime.combine(self.fecha, self.hora_inicio)
        return inicio <= _ahora_local()

    @property
    def reservable(self) -> bool:
        """False si llena, pasada, o si falta menos de CIERRE_RESERVA para el inicio."""
        if self.llena:
            return False
        # Una sola lectura de "ahora" para evaluar pasada/margen de cierre de
        # forma consistente (no dos llamadas a timezone.localtime() que
        # podrían, en teoría, devolver instantes distintos).
        ahora = _ahora_local()
        inicio = datetime.combine(self.fecha, self.hora_inicio)
        if inicio <= ahora:
            return False
        return inicio - ahora >= CIERRE_RESERVA


class ErrorDeReserva(Exception):
    """Base de los errores de negocio de `crear_reserva`/`cancelar_reserva`.

    Cada subclase lleva un mensaje en español listo para pasarle directo a
    `django.contrib.messages.error()` desde la vista (Task 4).
    """


class TurnoInexistente(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ese horario no corresponde a ningún turno disponible.")


class TurnoCerrado(ErrorDeReserva):
    def __init__(self):
        super().__init__(
            "Las reservas cierran una hora antes del turno (o el turno ya pasó)."
        )


class TurnoLleno(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ese turno ya está completo.")


class ReservaDuplicada(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ya tenés una reserva para ese turno.")


def franjas_de_rango(
    hora_desde: time, hora_hasta: time, duracion_minutos: int
) -> list[tuple[time, time]]:
    """Corta `[hora_desde, hora_hasta)` en franjas de `duracion_minutos`.

    La aritmética se hace con `datetime.combine(date.min, ...) + timedelta`
    (nunca sumando directo sobre un `time`, que no lo soporta). Una franja se
    incluye SOLO si termina en o antes de `hora_hasta` -- la franja
    incompleta al final del rango se descarta, no se genera parcial. No cruza
    medianoche: se asume `hora_desde < hora_hasta` dentro del mismo día (ya
    lo garantiza el `CheckConstraint` de `HorarioAtencion`).
    """
    duracion = timedelta(minutes=duracion_minutos)
    cursor = datetime.combine(date.min, hora_desde)
    limite = datetime.combine(date.min, hora_hasta)

    franjas = []
    while True:
        fin = cursor + duracion
        if fin > limite:
            break
        franjas.append((cursor.time(), fin.time()))
        cursor = fin
    return franjas


def franjas_del_dia(gimnasio, dia_semana: int) -> list[tuple[time, time]]:
    """Junta las franjas de TODOS los `HorarioAtencion` del gimnasio para ese
    `dia_semana` (puede haber varios rangos, ej. 8-12 y 16-21), aplicando
    `franjas_de_rango` a cada uno con la duración de `ConfiguracionTurnos`.
    Deduplica por `hora_inicio` (si dos rangos solapados generan el mismo
    horario, aparece una sola vez). Sin `HorarioAtencion` ese día -> `[]`.
    """
    config = obtener_configuracion(gimnasio)
    horarios = HorarioAtencion.objects.for_gimnasio(gimnasio).filter(
        dia_semana=dia_semana
    )

    vistas = set()
    franjas = []
    for horario in horarios:
        for inicio, fin in franjas_de_rango(
            horario.hora_desde, horario.hora_hasta, config.duracion_minutos
        ):
            if inicio in vistas:
                continue
            vistas.add(inicio)
            franjas.append((inicio, fin))

    franjas.sort(key=lambda par: par[0])
    return franjas


def vacantes_de_franja(gimnasio, dia_semana: int, hora_inicio: time, default: int) -> int:
    """Busca `CupoExcepcion(gimnasio, dia_semana, hora_inicio)`; si existe,
    devuelve su `vacantes` (puede ser 0). Si no existe, devuelve `default`
    (`vacantes_default` de la config). No valida que `hora_inicio` sea una
    franja generada -- eso es responsabilidad de quien la llama; una
    excepción "huérfana" (de una duración vieja) simplemente no se usa si esa
    hora ya no aparece en `franjas_del_dia`.
    """
    excepcion = (
        CupoExcepcion.objects.for_gimnasio(gimnasio)
        .filter(dia_semana=dia_semana, hora_inicio=hora_inicio)
        .first()
    )
    if excepcion is not None:
        return excepcion.vacantes
    return default


def es_franja_vigente(gimnasio, fecha: date, hora_inicio: time) -> bool:
    """True si `(fecha.weekday(), hora_inicio)` aparece en `franjas_del_dia()`
    para ese día de esa fecha."""
    horas = {inicio for inicio, _ in franjas_del_dia(gimnasio, fecha.weekday())}
    return hora_inicio in horas


def grilla_semanal(gimnasio, desde: date, dias: int = 14, alumno=None) -> dict[date, list[Franja]]:
    """Para cada fecha en `[desde, desde+dias)`, arma la lista de `Franja` del
    día (`franjas_del_dia` + `vacantes_de_franja` + conteo real de `Reserva`
    de esa fecha/hora, en una sola query agregada) y un dict de apoyo para
    `reservada_por_mi` si `alumno` no es `None`.
    """
    config = obtener_configuracion(gimnasio)
    hasta = desde + timedelta(days=dias - 1)

    conteos = (
        Reserva.objects.for_gimnasio(gimnasio)
        .filter(fecha__range=(desde, hasta))
        .values("fecha", "hora_inicio")
        .annotate(n=Count("id"))
    )
    ocupadas_por_franja = {(c["fecha"], c["hora_inicio"]): c["n"] for c in conteos}

    mias = set()
    if alumno is not None:
        mias = set(
            Reserva.objects.for_gimnasio(gimnasio)
            .filter(fecha__range=(desde, hasta), alumno=alumno)
            .values_list("fecha", "hora_inicio")
        )

    grilla = {}
    for offset in range(dias):
        fecha = desde + timedelta(days=offset)
        franjas_dia = []
        for hora_inicio, hora_fin in franjas_del_dia(gimnasio, fecha.weekday()):
            vacantes = vacantes_de_franja(
                gimnasio, fecha.weekday(), hora_inicio, config.vacantes_default
            )
            franjas_dia.append(
                Franja(
                    fecha=fecha,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    vacantes=vacantes,
                    ocupadas=ocupadas_por_franja.get((fecha, hora_inicio), 0),
                    reservada_por_mi=(fecha, hora_inicio) in mias,
                )
            )
        grilla[fecha] = franjas_dia
    return grilla


def reservas_por_franja(gimnasio, desde: date, hasta: date) -> dict[tuple[date, time], list]:
    """Para la agenda staff: reservas del gimnasio en `[desde, hasta]`,
    agrupadas por `(fecha, hora_inicio)`."""
    reservas = (
        Reserva.objects.for_gimnasio(gimnasio)
        .filter(fecha__range=(desde, hasta))
        .select_related("alumno")
    )

    agrupadas = {}
    for reserva in reservas:
        clave = (reserva.fecha, reserva.hora_inicio)
        agrupadas.setdefault(clave, []).append(reserva)
    return agrupadas


def crear_reserva(gimnasio, alumno, fecha: date, hora_inicio: time) -> Reserva:
    """Valida y crea una `Reserva`, en este orden:

    1. `TurnoInexistente` si `hora_inicio` no es una franja vigente.
    2. `TurnoCerrado` si al inicio le falta menos de `CIERRE_RESERVA` o ya
       pasó.
    3. `ReservaDuplicada` si el alumno ya tiene una reserva en esa franja.
    4. `TurnoLleno` si ya se alcanzó el cupo de la franja.
    5. Crea y devuelve la `Reserva`; si de todos modos se dispara el
       `unique_together` (carrera entre el chequeo 3 y el `create`), se
       traduce a `ReservaDuplicada`.

    Se toma `select_for_update()` sobre la fila de `ConfiguracionTurnos` del
    gimnasio para serializar reservas concurrentes de la misma franja (en
    SQLite, el backend usado en tests, `select_for_update()` no aplica un
    lock real -- ver docstring de `turnos/tests.py` -- pero en Postgres, el
    backend de producción, sí lo hace).
    """
    with transaction.atomic():
        config = ConfiguracionTurnos.objects.select_for_update().get(gimnasio=gimnasio)

        if not es_franja_vigente(gimnasio, fecha, hora_inicio):
            raise TurnoInexistente()

        inicio = datetime.combine(fecha, hora_inicio)
        if inicio - _ahora_local() < CIERRE_RESERVA:
            raise TurnoCerrado()

        ya_reservada = (
            Reserva.objects.for_gimnasio(gimnasio)
            .filter(alumno=alumno, fecha=fecha, hora_inicio=hora_inicio)
            .exists()
        )
        if ya_reservada:
            raise ReservaDuplicada()

        ocupadas = (
            Reserva.objects.for_gimnasio(gimnasio)
            .filter(fecha=fecha, hora_inicio=hora_inicio)
            .count()
        )
        vacantes = vacantes_de_franja(
            gimnasio, fecha.weekday(), hora_inicio, config.vacantes_default
        )
        if ocupadas >= vacantes:
            raise TurnoLleno()

        try:
            return Reserva.objects.create(
                gimnasio=gimnasio, alumno=alumno, fecha=fecha, hora_inicio=hora_inicio
            )
        except IntegrityError:
            raise ReservaDuplicada()


def cancelar_reserva(reserva) -> None:
    """Borra la reserva. Solo permitido si la franja NO pasó todavía -- se
    puede cancelar hasta el instante de inicio, el límite de `CIERRE_RESERVA`
    NO aplica a cancelar, solo a crear. Si ya pasó, levanta `TurnoCerrado`
    (el caller decide qué hacer -- en la vista, se traduce a un
    `messages.error()` sin borrar).
    """
    inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
    if inicio <= _ahora_local():
        raise TurnoCerrado()
    reserva.delete()


def url_google_calendar(reserva, gimnasio) -> str:
    """Arma la URL de template de Google Calendar para agendar la reserva."""
    config = obtener_configuracion(gimnasio)
    inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
    fin = inicio + timedelta(minutes=config.duracion_minutos)

    params = {
        "action": "TEMPLATE",
        "text": f"Entrenamiento en {gimnasio.nombre}",
        "dates": f"{inicio.strftime('%Y%m%dT%H%M%S')}/{fin.strftime('%Y%m%dT%H%M%S')}",
        "ctz": "America/Argentina/Buenos_Aires",
        "details": f"Turno de entrenamiento en {gimnasio.nombre}, reservado desde la app.",
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def eliminar_reservas_desencajadas(gimnasio) -> int:
    """Borra las `Reserva` futuras (inicio >= ahora local) del gimnasio cuya
    `(dia_semana, hora_inicio)` ya NO aparece en `franjas_del_dia()` con la
    config vigente. Las reservas ya pasadas NO se tocan (quedan como
    historial). Reducir el cupo (sin cambiar horarios/duración) no borra
    ninguna -- solo horarios/duración cambian qué franjas existen. Devuelve
    la cantidad borrada.
    """
    ahora = _ahora_local()
    borradas = 0
    for reserva in Reserva.objects.for_gimnasio(gimnasio):
        inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
        if inicio < ahora:
            continue
        if not es_franja_vigente(gimnasio, reserva.fecha, reserva.hora_inicio):
            reserva.delete()
            borradas += 1
    return borradas
