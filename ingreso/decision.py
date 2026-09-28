"""El único lugar donde se decide si la puerta abre.

Molde `pagos/acceso.py`: un módulo, resultado en `dataclass(frozen=True)`, y un
docstring con lo que no hay que deshacer.

Cuatro decisiones que conviene no deshacer:

1. **`decidir` no toca la base.** `contexto_de` resuelve todo el lote de una
   vez, con un número FIJO de queries traiga una lectura o cien. La alternativa
   obvia (una query por evento) es el patrón que ya causó un 502 en producción
   con el importador. Por eso `contexto` no tiene default: un `contexto=None`
   "que se lo arma solo" es la puerta por la que volvería a entrar.

2. **El antipassback va ÚLTIMO.** Significa "podés entrar pero ya entraste".
   Adelantarlo haría que el segundo scan de un deudor diga "ya pasaste" en vez
   de "tenés una cuota vencida".

3. **Cada lectura se mide contra SU `momento`, nunca contra `now()`**: la
   reserva vigente y el antipassback. Un lote en diferido trae horas de
   eventos, y medido contra el instante del upload el reporte mentiría.

4. **`estado_cuenta` del gimnasio NO figura acá.** Que un gimnasio le deba la
   app al dueño del producto no le cierra la puerta a sus alumnos: dejar
   ochenta personas afuera de un edificio no es una palanca de cobro. La
   invariante se fija en la vista, con su test.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from alumnos.models import Alumno
from ingreso.models import Credencial, EventoIngreso, obtener_configuracion


@dataclass(frozen=True)
class Lectura:
    """Lo que el lector dice que leyó.

    `tipo` usa el vocabulario de `Credencial.Tipo` ("qr"/"facial"). `momento`
    viene con zona. `id_equipo` es el id que el evento tiene EN el equipo (la
    idempotencia del lote) y `en_diferido` dice que el equipo ya abrió por su
    cuenta con el padrón cacheado. Los dos viajan acá y no en un parámetro
    aparte de `registrar_lote`: con un `set` de ids paralelo, una lectura sin
    id matchearía por el string vacío.
    """

    tipo: str
    valor: str
    sentido: str
    momento: object
    id_equipo: str = ""
    en_diferido: bool = False

    @property
    def valor_normalizado(self):
        """El lector puede agregar espacios o un `\\r\\n`. Sin esto, la tarjeta
        buena "no anda" y nadie entiende por qué."""
        return self.valor.strip()


@dataclass(frozen=True)
class Decision:
    permitido: bool
    resultado: str
    mensaje: str
    alumno: object = None
    credencial: object = None


@dataclass
class Contexto:
    """Todo lo que la decisión necesita, ya traído de la base."""

    config: object
    credenciales: dict            # (tipo, valor normalizado) -> Credencial
    bloqueados: set               # alumno_id de los que deben
    reservas: object              # turnos.services.ReservasEnRango, o None
    #: `(alumno_id, sentido) -> [momento, ...]` de los ingresos PERMITIDOS ya
    #: guardados dentro de la ventana del lote. Una lista y no "el último":
    #: en diferido, el último de la base puede estar lejos de una lectura y
    #: uno anterior, pegado a ella.
    ultimo_permitido: dict
    #: Lo que este mismo lote ya dio por permitido, misma forma, para que el
    #: antipassback vea dos lecturas del mismo alumno dentro del mismo request.
    concedidos: dict = field(default_factory=dict)


def contexto_de(dispositivo, lecturas):
    """La ÚNICA función de este módulo que consulta la base.

    Queries fijas, traiga una lectura o cien: la configuración, las
    credenciales, y según lo que esté prendido, los bloqueados, las reservas
    (más la configuración de turnos) y los ingresos recientes.
    """
    from pagos import acceso
    from turnos import services as turnos_services

    gimnasio = dispositivo.gimnasio
    config = obtener_configuracion(gimnasio)

    # Filtrar por `gimnasio` ES el aislamiento de tenant en la puerta: el QR
    # válido de un alumno de otro gimnasio no está en este diccionario, así que
    # da SIN_CREDENCIAL. Y la clave lleva el `tipo` porque la unicidad de
    # `Credencial` es `(gimnasio, tipo, valor)`: un id facial y un QR pueden
    # compartir el texto.
    valores = {l.valor_normalizado for l in lecturas}
    credenciales = {
        (c.tipo, c.valor): c
        for c in Credencial.objects.for_gimnasio(gimnasio)
        .filter(valor__in=valores)
        .select_related("alumno")
    }
    alumnos = list({c.alumno_id: c.alumno for c in credenciales.values()}.values())
    if not alumnos:
        return Contexto(
            config=config, credenciales=credenciales, bloqueados=set(),
            reservas=None, ultimo_permitido={},
        )

    momentos = [l.momento for l in lecturas]
    desde, hasta = min(momentos), max(momentos)

    # La regla de deuda NO se reescribe acá: `pagos/acceso.py` dice en su
    # docstring que es "el único lugar donde se decide si un alumno está
    # bloqueado por falta de pago", y copiar su `Exists` sería la segunda copia
    # que ese módulo existe para evitar. `bloqueados_entre` es su versión en
    # bloque. Se evalúa contra HOY (no contra el momento de cada lectura): la
    # deuda es un estado de la cuenta, no del instante del evento.
    bloqueados = set()
    if config.bloquea_por_deuda:
        bloqueados = acceso.bloqueados_entre(gimnasio, alumnos)

    reservas = None
    if config.exige_reserva:
        reservas = turnos_services.reservas_vigentes_en_rango(
            gimnasio, alumnos, desde, hasta
        )

    ultimo_permitido = {}
    minutos = config.minutos_antipassback
    if minutos:
        # **El piso de fecha es obligatorio, no una optimización.** Sin él esta
        # query trae el historial ENTERO de cada alumno del lote: un socio con
        # dos años de ingresos son ~500 filas, y cien de ellos en un lote son
        # 50.000 filas a memoria en cada scan. La cantidad de QUERIES sería la
        # misma, así que un test que solo cuenta queries pasa con el bug puesto
        # -- exactamente la trampa que este proyecto ya pagó. Lo que se mide es
        # el piso, con su propio test.
        ventana = timedelta(minutes=minutos)
        previos = (
            EventoIngreso.objects.for_gimnasio(gimnasio)
            .filter(
                alumno__in=alumnos,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento__gte=desde - ventana,
                momento__lte=hasta + ventana,
            )
            .values_list("alumno_id", "sentido", "momento")
        )
        for alumno_id, sentido, momento in previos:
            ultimo_permitido.setdefault((alumno_id, sentido), []).append(momento)

    return Contexto(
        config=config, credenciales=credenciales, bloqueados=bloqueados,
        reservas=reservas, ultimo_permitido=ultimo_permitido,
    )


def decidir(lectura, contexto):
    """Decide sobre lo que `contexto_de` ya trajo: **no toca la base**.

    No es pura, y la impureza es deliberada: al conceder un ingreso anota el
    momento en `contexto.concedidos`, que es lo que le permite al antipassback
    ver dos lecturas del mismo alumno dentro del MISMO lote. Consecuencia para
    quien la llame: **las lecturas de un lote se recorren en orden**, y el mismo
    `contexto` se reusa para todas.
    """
    credencial = contexto.credenciales.get(
        (lectura.tipo, lectura.valor_normalizado)
    )
    if credencial is None:
        return Decision(
            False, EventoIngreso.Resultado.SIN_CREDENCIAL,
            "Credencial no reconocida. Pasá por el mostrador.",
        )
    if not credencial.activa:
        return Decision(
            False, EventoIngreso.Resultado.CREDENCIAL_INACTIVA,
            "Esa credencial está dada de baja.",
            credencial.alumno, credencial,
        )

    alumno = credencial.alumno
    if alumno.estado != Alumno.Estado.ACTIVO:
        return Decision(
            False, EventoIngreso.Resultado.ALUMNO_INACTIVO,
            "Tu cuenta está dada de baja. Hablá con el gimnasio.",
            alumno, credencial,
        )
    if alumno.pk in contexto.bloqueados:
        return Decision(
            False, EventoIngreso.Resultado.DEUDA,
            "Tenés una cuota sin pagar. Pasá por el mostrador.",
            alumno, credencial,
        )
    if contexto.config.exige_reserva and (
        contexto.reservas is None
        or contexto.reservas.vigente(alumno.pk, lectura.momento) is None
    ):
        return Decision(
            False, EventoIngreso.Resultado.SIN_RESERVA,
            "No tenés turno reservado para este horario.",
            alumno, credencial,
        )

    minutos = contexto.config.minutos_antipassback
    if minutos:
        clave = (alumno.pk, lectura.sentido)
        previos = [
            *contexto.ultimo_permitido.get(clave, ()),
            *contexto.concedidos.get(clave, ()),
        ]
        if any(
            abs((lectura.momento - previo).total_seconds()) < minutos * 60
            for previo in previos
        ):
            return Decision(
                False, EventoIngreso.Resultado.DUPLICADO,
                "Ya registramos tu ingreso hace un momento.",
                alumno, credencial,
            )
        contexto.concedidos.setdefault(clave, []).append(lectura.momento)

    return Decision(
        True, EventoIngreso.Resultado.PERMITIDO,
        f"¡Hola, {alumno.nombre}! Pasá.", alumno, credencial,
    )
