"""Las escrituras del control de ingreso, y el padrón. Las vistas no escriben."""

import hashlib
import json

from django.db import transaction

from alumnos.models import Alumno
from ingreso import limites, tokens
from ingreso.models import Credencial, EventoIngreso, obtener_configuracion

LARGO_DETALLE = EventoIngreso._meta.get_field("detalle").max_length

MAX_EVENTOS_POR_LOTE = 100
"""Tope del lote. Con `contexto_de` el costo son unas pocas queries fijas más
un `bulk_create`, así que el tope es por el tamaño del body y por los 30 s de
gunicorn, no por el costo por evento."""

HORAS_DE_ADELANTO_TOLERADAS = 1
"""Solo para los eventos EN DIFERIDO (en online manda el reloj del servidor).
Un lector con el reloj adelantado fecha eventos en el futuro, y eso ensucia el
antipassback y los reportes para siempre. Una hora cubre un desfasaje de zona
horaria mal configurada sin aceptar un evento de la semana que viene."""

SEGUNDOS_DE_DESFASE_A_ANOTAR = 60
"""En modo online manda el reloj del servidor (ver `registrar_lote`); si el
`momento` que mandó el lector difiere en más que esto, el desfase se anota en
`detalle` para que el instalador vea que hay un reloj mal."""

RESULTADO_INVALIDO = "invalido"
"""El `resultado` de un evento que no se pudo leer. No es un
`EventoIngreso.Resultado`: ese evento no se registra."""

DIAS_DE_ANTIGUEDAD_TOLERADOS = 7
"""Solo para los eventos EN DIFERIDO (en online manda el reloj del servidor).
El piso simétrico del tope al futuro: un lector con el reloj reseteado (una
pila agotada lo devuelve al 2000-01-01) fecha sus eventos en el pasado remoto. Aceptado, ese
ingreso queda para siempre en los reportes históricos de un día en que nadie
entró. Siete días es cuánto se espera que un lector pueda quedar sin internet
acumulando su lote en diferido: pasado eso, lo que llega es un reloj roto y no
un backlog. `contexto_de` ya no trae el historial entero ante una lectura
vieja (acota por lectura, ver `decision.py`); este piso es la segunda capa,
la que evita que el dato envenenado se guarde.

Riesgo aceptado: los eventos de un lector que estuvo más de siete días
desconectado salen como inválidos (uno por uno, el resto del lote se registra)
y no quedan en el historial."""


def generar_credencial_qr(alumno, *, regenerar=False):
    """El QR del alumno. Fijo: sirve impreso en una tarjeta para el que no
    tiene smartphone. `regenerar=True` lo reemplaza (el viejo deja de servir).

    Regenerar es un UPDATE en la misma fila, así que no queda rastro del
    código anterior: es el costo asumido de la unicidad por alumno.
    """
    credencial, creada = Credencial.objects.get_or_create(
        gimnasio=alumno.gimnasio,
        alumno=alumno,
        tipo=Credencial.Tipo.QR,
        defaults={"valor": tokens.generar()},
    )
    if not creada and regenerar:
        credencial.valor = tokens.generar()
        credencial.activa = True
        credencial.save(update_fields=["valor", "activa", "modificado"])
    return credencial


@transaction.atomic
def generar_credenciales_en_bloque(gimnasio):
    """Le crea el QR a todos los alumnos ACTIVOS que todavía no tienen.

    Un `bulk_create` y no una query por alumno: el gimnasio de 200 alumnos es
    justo el caso que motiva el botón.
    """
    ya_tienen = Credencial.objects.for_gimnasio(gimnasio).filter(
        tipo=Credencial.Tipo.QR
    ).values_list("alumno_id", flat=True)
    faltantes = (
        Alumno.objects.for_gimnasio(gimnasio)
        .filter(estado=Alumno.Estado.ACTIVO)
        .exclude(pk__in=ya_tienen)
    )
    nuevas = [
        Credencial(
            gimnasio=gimnasio, alumno=alumno,
            tipo=Credencial.Tipo.QR, valor=tokens.generar(),
        )
        for alumno in faltantes
    ]
    Credencial.objects.bulk_create(nuevas)
    return len(nuevas)


def nombre_corto(alumno):
    """«Juan P.»: lo que muestra la pantalla del molinete y lo que viaja en el
    padrón. El apellido completo no le agrega nada a quien pasa por la puerta
    y sí lo deja escrito en la memoria de un equipo de terceros."""
    if alumno is None:
        return None
    nombre = (alumno.nombre or "").strip()
    apellido = (alumno.apellido or "").strip()
    if not apellido:
        return nombre
    return f"{nombre} {apellido[0].upper()}."


def respuesta_invalida(error, id_equipo=None):
    """La respuesta de un evento que no se pudo leer. Ocupa su posición en el
    lote y no se registra; el resto del lote sigue."""
    return {
        "id_evento_equipo": id_equipo,
        "permitido": False,
        "resultado": RESULTADO_INVALIDO,
        "mensaje": "No pudimos leer el ingreso. Pasá por el mostrador.",
        "alumno": None,
        "error": error,
    }


def _respuesta(*, id_equipo, permitido, resultado, mensaje, alumno):
    return {
        "id_evento_equipo": id_equipo or None,
        "permitido": permitido,
        "resultado": resultado,
        "mensaje": mensaje,
        "alumno": nombre_corto(alumno),
    }


def _detalle(lectura, veredicto):
    """El valor leído no se guarda (es la llave de la puerta): de una
    credencial desconocida, solo la cola, para diagnosticar. Si además hay una
    nota (el desfase del reloj) y no entran las dos, gana la credencial y la
    nota se recorta."""
    partes = []
    if veredicto.credencial is None:
        partes.append(f"…{lectura.valor_normalizado[-6:]}")
    if lectura.nota:
        partes.append(lectura.nota)
    return " · ".join(partes)[:LARGO_DETALLE]


@transaction.atomic
def registrar_lote(dispositivo, lecturas):
    """Decide y registra. Devuelve un dict por lectura, **en el orden en que
    llegaron**: el lector correlaciona las respuestas por posición o por id.

    **Se deciden en orden cronológico, no en el orden del request.** El
    antipassback dentro del lote (`decidir` anota lo que concede en el
    contexto) depende del orden de recorrido: con una ventana de 15 minutos,
    un lote que llega como 10:10, 10:00, 10:20 daba un solo ingreso, y el mismo
    lote ordenado da dos. Un lote en diferido no tiene por qué llegar ordenado,
    y el veredicto no puede depender de eso.

    Los `id_evento_equipo` que ya existen NO se vuelven a decidir: se devuelve
    el veredicto guardado, con el mensaje reconstruido desde su resultado. Es la
    idempotencia que necesita un lector que reintenta el upload (decidido de
    nuevo, el antipassback lo daría por duplicado). Un id repetido DENTRO del
    lote se registra una vez y las dos posiciones reciben la misma respuesta.
    """
    from ingreso.decision import contexto_de, decidir, mensaje_de

    gimnasio = dispositivo.gimnasio
    ids = {lectura.id_equipo for lectura in lecturas if lectura.id_equipo}
    ya_estaban = {}
    if ids:
        # `select_related`: la respuesta lleva el nombre del alumno, y sin
        # esto cada evento ya registrado de un reintento es una query más.
        ya_estaban = {
            e.id_evento_equipo: e
            for e in EventoIngreso.objects.filter(
                dispositivo=dispositivo, id_evento_equipo__in=ids
            ).select_related("alumno")
        }

    nuevas = [l for l in lecturas if l.id_equipo not in ya_estaban]
    contexto = contexto_de(dispositivo, nuevas) if nuevas else None

    respuestas = [None] * len(lecturas)
    del_lote = {}  # id_equipo -> respuesta ya decidida en este request
    a_crear = []
    # `sorted` es estable: dos lecturas del mismo instante conservan su orden.
    for i in sorted(range(len(lecturas)), key=lambda i: lecturas[i].momento):
        lectura = lecturas[i]
        previo = ya_estaban.get(lectura.id_equipo) if lectura.id_equipo else None
        if previo is not None:
            respuestas[i] = _respuesta(
                id_equipo=previo.id_evento_equipo,
                permitido=previo.resultado == EventoIngreso.Resultado.PERMITIDO,
                resultado=previo.resultado,
                mensaje=mensaje_de(previo.resultado, previo.alumno),
                alumno=previo.alumno,
            )
            continue
        if lectura.id_equipo and lectura.id_equipo in del_lote:
            respuestas[i] = del_lote[lectura.id_equipo]
            continue

        veredicto = decidir(lectura, contexto)
        a_crear.append(EventoIngreso(
            gimnasio=gimnasio, dispositivo=dispositivo,
            alumno=veredicto.alumno, credencial=veredicto.credencial,
            resultado=veredicto.resultado, momento=lectura.momento,
            sentido=lectura.sentido,
            en_diferido=lectura.en_diferido,
            id_evento_equipo=lectura.id_equipo,
            detalle=_detalle(lectura, veredicto),
        ))
        respuestas[i] = _respuesta(
            id_equipo=lectura.id_equipo,
            permitido=veredicto.permitido,
            resultado=veredicto.resultado,
            mensaje=veredicto.mensaje,
            alumno=veredicto.alumno,
        )
        if lectura.id_equipo:
            del_lote[lectura.id_equipo] = respuestas[i]

    # `ignore_conflicts` cubre la carrera del reintento simultáneo que el
    # chequeo de arriba no ve. Ojo: no puebla los pk ni dice qué insertó, así
    # que la respuesta sale de `decidir()` y nunca de lo que devuelve esto.
    EventoIngreso.objects.bulk_create(a_crear, ignore_conflicts=True)
    # El tope diario cuenta lo que se ESCRIBIÓ: un reintento idempotente no
    # suma (no escribe) y un lote que solo trae reintentos no paga la query.
    # Con `ignore_conflicts` puede sobrecontar un reintento simultáneo: a
    # favor del freno, y por un lote como mucho.
    limites.sumar_al_cupo(dispositivo, len(a_crear))
    return respuestas


REGLAS_NO_INCLUIDAS = ["reserva", "antipassback"]
"""Lo que el equipo NO puede aplicar con el padrón cacheado, declarado en la
respuesta. Son momentáneas: dependen de la hora exacta de la lectura y de los
eventos que el equipo todavía no subió, así que no se pueden precomputar. Van
siempre, estén o no prendidas: es la lista de lo que el padrón no contempla,
no de lo que este gimnasio configuró. La pantalla de configuración dice lo
mismo al lado de los dos checkboxes (riesgo aceptado 4 de la spec)."""

LARGO_VERSION_PADRON = 32


def padron_de(gimnasio):
    """Quién puede entrar, para que el lector funcione sin internet.

    **Viajan solo los alumnos ACTIVOS con sus credenciales activas.** Un
    alumno dado de baja no viaja: offline es una credencial desconocida (la
    puerta niega igual que online) y el puente lo borra del equipo porque «ya
    no figura». Si viajara, su QR -- una llave al portador -- y su id facial
    quedarían para siempre en la memoria de un equipo de terceros (la spec
    trata el dato facial con la Ley 25.326 en mente) y el padrón crecería con
    la rotación de alumnos. Consecuencia: reactivarlo obliga a volver a enrolar
    su cara. Una credencial dada de baja tampoco viaja, por lo mismo.

    **`habilitado` es el árbol de `decision.decidir` menos reserva y
    antipassback** (momentáneas, ver `REGLAS_NO_INCLUIDAS`). Entre los activos,
    la única rama que queda es la deuda: `acceso.bloqueados_entre`, la misma
    regla que el portal y que la puerta online, solo si el gimnasio prendió
    `bloquea_por_deuda`, y contra `timezone.localdate()` (entre las 21:00 y
    las 23:59 la fecha UTC ya es la de mañana y adelantaría un día el corte).
    El deudor SÍ viaja, con `habilitado: false`: vuelve apenas paga, así que el
    puente lo bloquea en el equipo en vez de borrarlo.

    **Queries fijas, no una por alumno**: la configuración, las credenciales
    con su alumno, y los deudores con `bloqueados_entre` (una sola, con el
    mismo `Exists(OuterRef)` de `contar_bloqueados`). Llamar a `bloqueo_de` por
    fila acá es el N+1 que este proyecto ya pagó con un 502, y es peor que en
    una pantalla: el lector pollea esto cada pocos minutos. Ojo: un 304 cuesta
    lo mismo que un 200 (hay que armar el padrón para calcular la versión);
    riesgo aceptado en la spec.

    **Solo lleva lo necesario**: el padrón queda escrito en la memoria de un
    equipo de terceros. Nombre corto («Juan P.», el mismo `nombre_corto` que la
    respuesta online), si puede pasar y las llaves; nunca email, teléfono,
    datos de salud ni montos. El `.only()` lo cumple ya desde la query: esos
    campos ni se traen a memoria. **`motivo: "deuda"` sí deja en el equipo
    quién debe**, y es a sabiendas: online la pantalla del molinete ya se lo
    dice a quien pasa, y sin eso el lector offline no puede mostrar lo mismo.
    """
    from django.utils import timezone

    from pagos import acceso

    from ingreso.decision import MENSAJES, SALUDO

    config = obtener_configuracion(gimnasio)
    # `order_by` por pk y no por apellido: la versión es el hash del
    # contenido, así que el orden tiene que ser total (dos «Juan Pérez»
    # empatarían) y no puede depender de un campo que no viaja.
    credenciales = list(
        Credencial.objects.for_gimnasio(gimnasio)
        .filter(activa=True, alumno__estado=Alumno.Estado.ACTIVO)
        .select_related("alumno")
        .only("tipo", "valor", "alumno", "alumno__nombre", "alumno__apellido")
        .order_by("alumno_id", "tipo")
    )
    alumnos = list({c.alumno_id: c.alumno for c in credenciales}.values())
    bloqueados = (
        acceso.bloqueados_entre(gimnasio, alumnos, hoy=timezone.localdate())
        if config.bloquea_por_deuda else {}
    )

    deuda = str(EventoIngreso.Resultado.DEUDA)
    por_alumno = {}
    for credencial in credenciales:
        alumno = credencial.alumno
        fila = por_alumno.get(alumno.pk)
        if fila is None:
            debe = alumno.pk in bloqueados
            fila = por_alumno[alumno.pk] = {
                "id": alumno.pk,
                "nombre": nombre_corto(alumno),
                "habilitado": not debe,
                "motivo": deuda if debe else None,
                "qr": None,
                "id_facial": None,
            }
        clave = "qr" if credencial.tipo == Credencial.Tipo.QR else "id_facial"
        fila[clave] = credencial.valor

    cuerpo = {
        "reglas_no_incluidas": REGLAS_NO_INCLUIDAS,
        # Los textos van UNA vez y no por fila: con dos mil alumnos, repetir
        # el mismo saludo es la mitad del cuerpo. Salen de
        # `decision.MENSAJES`/`SALUDO`, los mismos que usa la puerta online.
        # El de deuda es el genérico (sin «hace N días»): el padrón vive en el
        # lector hasta el próximo poll y los días envejecen ahí adentro -- y
        # cambiarían la versión todos los días. El puente arma el saludo
        # reemplazando `{nombre}` por el `nombre` de la fila.
        "mensajes": {
            str(EventoIngreso.Resultado.PERMITIDO): SALUDO,
            deuda: MENSAJES[EventoIngreso.Resultado.DEUDA],
            str(EventoIngreso.Resultado.SIN_CREDENCIAL):
                MENSAJES[EventoIngreso.Resultado.SIN_CREDENCIAL],
        },
        "alumnos": list(por_alumno.values()),
    }
    # La versión es el hash del contenido y no `max(modificado)`: cambia
    # exactamente cuando cambia lo que el lector tiene que guardar, ni antes ni
    # después (dar de baja un alumno o que se atrase con la cuota cambia el
    # padrón sin tocar ninguna credencial).
    serializado = json.dumps(cuerpo, sort_keys=True, ensure_ascii=False)
    cuerpo["version"] = hashlib.sha256(
        serializado.encode()
    ).hexdigest()[:LARGO_VERSION_PADRON]
    return cuerpo
