"""Las escrituras del control de ingreso. Las vistas no escriben."""

from django.db import transaction

from alumnos.models import Alumno
from ingreso import tokens
from ingreso.models import Credencial, EventoIngreso

MAX_EVENTOS_POR_LOTE = 100
"""Tope del lote. Con `contexto_de` el costo son unas pocas queries fijas más
un `bulk_create`, así que el tope es por el tamaño del body y por los 30 s de
gunicorn, no por el costo por evento."""

HORAS_DE_ADELANTO_TOLERADAS = 1
"""Un lector con el reloj adelantado fecha eventos en el futuro, y eso ensucia
el antipassback y los reportes para siempre. Una hora cubre un desfasaje de
zona horaria mal configurada sin aceptar un evento de la semana que viene."""

DIAS_DE_ANTIGUEDAD_TOLERADOS = 7
"""El piso simétrico: un lector con el reloj reseteado (una pila agotada lo
devuelve al 2000-01-01) fecha sus eventos en el pasado remoto. Aceptado, ese
ingreso queda para siempre en los reportes históricos de un día en que nadie
entró. Siete días es cuánto se espera que un lector pueda quedar sin internet
acumulando su lote en diferido: pasado eso, lo que llega es un reloj roto y no
un backlog. `contexto_de` ya no trae el historial entero ante una lectura
vieja (acota por lectura, ver `decision.py`); este piso es la segunda capa,
la que evita que el dato envenenado se guarde.

Riesgo aceptado: un lector que estuvo más de siete días desconectado recibe un
400 por ese backlog, y el puente tiene que descartar los eventos viejos (el
mensaje dice cuál) para poder subir el resto."""


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


def _respuesta(*, id_equipo, permitido, resultado, mensaje, alumno):
    return {
        "id_evento_equipo": id_equipo or None,
        "permitido": permitido,
        "resultado": resultado,
        "mensaje": mensaje,
        "alumno": str(alumno) if alumno is not None else None,
    }


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
    el veredicto guardado. Es la idempotencia que necesita un lector que
    reintenta el upload (decidido de nuevo, el antipassback lo daría por
    duplicado). Un id repetido DENTRO del lote se registra una vez y las dos
    posiciones reciben la misma respuesta.
    """
    from ingreso.decision import contexto_de, decidir

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
                mensaje="",
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
            # El valor leído no se guarda (es la llave de la puerta): de una
            # credencial desconocida, solo la cola, para diagnosticar.
            detalle=(
                f"…{lectura.valor_normalizado[-6:]}"
                if veredicto.credencial is None else ""
            ),
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
    return respuestas
