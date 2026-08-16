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
cancelar reservas, y reconciliar reservas que quedaron "desencajadas" tras
un cambio de configuración (reubicándolas si es posible, cancelándolas si
no).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from .models import ConfiguracionTurnos, CupoExcepcion, HorarioAtencion, Reserva, obtener_configuracion

CIERRE_RESERVA = timedelta(hours=1)


def lunes_de_semana(offset: int = 0) -> date:
    """Lunes de la semana actual desplazada `offset` semanas (0 = esta
    semana, negativo = anteriores, positivo = siguientes). Lógica de fechas
    pura, sin depender del request -- la usan tanto `MisTurnosView` como
    `AgendaView` para paginar su grilla semana por semana."""
    hoy = timezone.localdate()
    return hoy - timedelta(days=hoy.weekday()) + timedelta(weeks=offset)


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


def _franjas_de_horarios(
    horarios, duracion_minutos: int
) -> list[tuple[time, time]]:
    """Corta una lista de `HorarioAtencion` (ya filtrada a un solo día) en
    franjas de `duracion_minutos`, deduplicando por `hora_inicio` (si dos
    rangos solapados generan el mismo horario, aparece una sola vez). Lógica
    compartida por `franjas_del_dia` (una consulta por día) y la precarga en
    bloque de `grilla_semanal` (evita repetirla por cada día de la grilla).
    """
    vistas = set()
    franjas = []
    for horario in horarios:
        for inicio, fin in franjas_de_rango(
            horario.hora_desde, horario.hora_hasta, duracion_minutos
        ):
            if inicio in vistas:
                continue
            vistas.add(inicio)
            franjas.append((inicio, fin))

    franjas.sort(key=lambda par: par[0])
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
    return _franjas_de_horarios(horarios, config.duracion_minutos)


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
    día (franjas + cupo + conteo real de `Reserva` de esa fecha/hora) y un
    dict de apoyo para `reservada_por_mi` si `alumno` no es `None`.

    A diferencia de `franjas_del_dia`/`vacantes_de_franja` (pensadas para
    resolver UN día/franja puntual), acá se precargan TODOS los
    `HorarioAtencion` y `CupoExcepcion` del gimnasio en un solo query cada
    uno y se agrupan en memoria por `dia_semana`/`hora_inicio` -- para no
    repetir una consulta por cada uno de los `dias` de la grilla (horarios) ni
    una por cada franja de cada día (excepciones), que escalaba linealmente
    con `dias` y con la cantidad de franjas.
    """
    config = obtener_configuracion(gimnasio)
    hasta = desde + timedelta(days=dias - 1)

    horarios_por_dia = {}
    for horario in HorarioAtencion.objects.for_gimnasio(gimnasio):
        horarios_por_dia.setdefault(horario.dia_semana, []).append(horario)
    franjas_por_dia_semana = {
        dia_semana: _franjas_de_horarios(horarios, config.duracion_minutos)
        for dia_semana, horarios in horarios_por_dia.items()
    }

    vacantes_por_excepcion = {
        (excepcion.dia_semana, excepcion.hora_inicio): excepcion.vacantes
        for excepcion in CupoExcepcion.objects.for_gimnasio(gimnasio)
    }

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
        dia_semana = fecha.weekday()
        franjas_dia = []
        for hora_inicio, hora_fin in franjas_por_dia_semana.get(dia_semana, []):
            vacantes = vacantes_por_excepcion.get(
                (dia_semana, hora_inicio), config.vacantes_default
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
    backend de producción, sí lo hace). Se pasa primero por
    `obtener_configuracion()` (que la crea si todavía no existe) para no
    romper con `DoesNotExist` si el gimnasio nunca generó su config.
    """
    with transaction.atomic():
        config = obtener_configuracion(gimnasio)
        config = ConfiguracionTurnos.objects.select_for_update().get(pk=config.pk)

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


@dataclass(frozen=True)
class EventoReconciliacion:
    """Detalle de lo que le pasó a UNA reserva durante la reconciliación
    (Parte B). `hora_nueva` seteada = migrada a ese horario; `None` = cancelada.
    Lo consume `_generar_novedades_personales` para avisarle al alumno."""

    alumno: "Alumno"
    fecha: date
    hora_original: time
    hora_nueva: time | None


@dataclass(frozen=True)
class ResultadoReconciliacion:
    migradas: int
    canceladas: int
    eventos: tuple[EventoReconciliacion, ...] = ()


def _franja_mas_cercana(franjas: list[tuple[time, time]], hora_original: time) -> time | None:
    """De una lista de franjas del día (ya ordenada ascendente por
    `hora_inicio`, ver `_franjas_de_horarios`), la `hora_inicio` con menor
    distancia absoluta en minutos a `hora_original`. Empate -> la más
    temprana (`min()` es estable y la lista ya viene ordenada). `None` si
    `franjas` está vacía.
    """
    if not franjas:
        return None

    def _minutos(hora: time) -> int:
        return hora.hour * 60 + hora.minute

    objetivo = _minutos(hora_original)
    return min(franjas, key=lambda franja: abs(_minutos(franja[0]) - objetivo))[0]


def reconciliar_reservas_desencajadas(gimnasio) -> ResultadoReconciliacion:
    """Para cada `Reserva` futura del gimnasio cuya `(dia_semana, hora_inicio)`
    ya no aparece en `franjas_del_dia()` con la config vigente (tras un
    cambio de horarios/duración), intenta mudarla a la franja de ese mismo
    día más cercana en horario a la original. Si ese día no queda ninguna
    franja, si la más cercana ya alcanzó su cupo, o si el alumno ya tiene
    otra reserva exactamente en esa franja/fecha, se cancela (se borra) --
    igual que el comportamiento anterior. Las reservas ya pasadas no se
    tocan (quedan como historial). Devuelve cuántas se migraron y cuántas
    se cancelaron.

    El límite de `CIERRE_RESERVA` NO aplica acá: es el sistema preservando
    una reserva que ya existía, no una reserva nueva.
    """
    config = obtener_configuracion(gimnasio)
    ahora = _ahora_local()
    migradas = 0
    canceladas = 0
    eventos: list[EventoReconciliacion] = []

    with transaction.atomic():
        for reserva in Reserva.objects.for_gimnasio(gimnasio):
            inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
            if inicio < ahora:
                continue
            if es_franja_vigente(gimnasio, reserva.fecha, reserva.hora_inicio):
                continue

            hora_original = reserva.hora_inicio
            dia_semana = reserva.fecha.weekday()
            franjas = franjas_del_dia(gimnasio, dia_semana)
            nueva_hora = _franja_mas_cercana(franjas, hora_original)

            if nueva_hora is not None:
                vacantes = vacantes_de_franja(
                    gimnasio, dia_semana, nueva_hora, config.vacantes_default
                )
                ocupadas = (
                    Reserva.objects.for_gimnasio(gimnasio)
                    .filter(fecha=reserva.fecha, hora_inicio=nueva_hora)
                    .exclude(pk=reserva.pk)
                    .count()
                )
                ya_tiene_esa = (
                    Reserva.objects.for_gimnasio(gimnasio)
                    .filter(
                        fecha=reserva.fecha, hora_inicio=nueva_hora, alumno=reserva.alumno
                    )
                    .exclude(pk=reserva.pk)
                    .exists()
                )
                if ocupadas < vacantes and not ya_tiene_esa:
                    reserva.hora_inicio = nueva_hora
                    reserva.save(update_fields=["hora_inicio"])
                    migradas += 1
                    eventos.append(
                        EventoReconciliacion(
                            alumno=reserva.alumno,
                            fecha=reserva.fecha,
                            hora_original=hora_original,
                            hora_nueva=nueva_hora,
                        )
                    )
                    continue

            eventos.append(
                EventoReconciliacion(
                    alumno=reserva.alumno,
                    fecha=reserva.fecha,
                    hora_original=hora_original,
                    hora_nueva=None,
                )
            )
            reserva.delete()
            canceladas += 1

        _generar_novedades_personales(gimnasio, eventos)

    return ResultadoReconciliacion(
        migradas=migradas, canceladas=canceladas, eventos=tuple(eventos)
    )


def _generar_novedades_personales(gimnasio, eventos: list[EventoReconciliacion]) -> None:
    """Por cada reserva migrada/cancelada, le crea al alumno una `Novedad`
    personal (Parte B) que verá en su portal. Corre dentro del mismo
    `transaction.atomic()` de la reconciliación: la migración y su aviso
    commitean juntos o nada.

    Se saltea al alumno SIN `Perfil`: no tiene login ni portal donde ver la
    novedad (la reserva igual se migró/canceló y el staff la ve en el conteo
    agregado). `visible_hasta` = la fecha de la reserva afectada, así el aviso
    se autovence una vez que esa fecha pasó.

    Import tardío de `Novedad` para no acoplar `turnos` con `novedades` a nivel
    de módulo (mismo patrón que `tenants/views.py::HomeView`). Se llama
    `full_clean()` antes de `save()` porque `create()`/`save()` no invocan
    `clean()`, y este helper es la única vía de creación de novedades personales.
    """
    from novedades.models import Novedad

    hoy = _ahora_local().date()

    for evento in eventos:
        if evento.alumno.perfil_id is None:
            continue

        if evento.hora_nueva is not None:
            titulo = "Cambió el horario de tu turno"
            mensaje = (
                f"Tu turno del {evento.fecha:%d/%m} se movió de las "
                f"{evento.hora_original:%H:%M} a las {evento.hora_nueva:%H:%M} "
                "porque el gimnasio actualizó su grilla de horarios."
            )
        else:
            titulo = "Se canceló uno de tus turnos"
            mensaje = (
                f"Tu turno del {evento.fecha:%d/%m} a las "
                f"{evento.hora_original:%H:%M} se canceló porque ya no hay un "
                "horario compatible en la nueva grilla. Podés reservar otro "
                "desde 'Reservar turno'."
            )

        novedad = Novedad(
            gimnasio=gimnasio,
            alumno=evento.alumno,
            titulo=titulo,
            mensaje=mensaje,
            fecha_publicacion=hoy,
            visible_hasta=evento.fecha,
            activa=True,
        )
        novedad.full_clean()
        novedad.save()
