"""Vistas del control de ingreso.

Las de la API (`/ingreso/v1/...`) son las que llama el lector de la puerta:
sin sesión, autenticadas por token (`ingreso/auth.py`). Las vistas no
escriben: el registro vive en `ingreso/services.py`.
"""

import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ingreso import auth, services
from ingreso.decision import Lectura
from ingreso.models import Credencial, Dispositivo, EventoIngreso

LARGO_ID_EVENTO_EQUIPO = EventoIngreso._meta.get_field("id_evento_equipo").max_length


class LecturaInvalida(ValueError):
    """Un evento que no se puede leer. No tumba el lote: ocupa su posición en
    la respuesta con `services.respuesta_invalida` y el resto sigue."""


class CuerpoInvalido(ValueError):
    """El request entero no tiene la forma del contrato: 400."""


def _momento_leido(crudo):
    """El `momento` que manda el lector, ya con zona. Solo el FORMATO: el
    rango lo mira quien llama, porque depende del modo."""
    if not isinstance(crudo, str):
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    texto = crudo.strip()
    # `parse_datetime` acepta "2026-09-28" (medianoche) desde Python 3.11: una
    # fecha sin hora ubicaría el ingreso a las 00:00 y ensuciaría el
    # antipassback y los reportes del día.
    if "T" not in texto and " " not in texto:
        raise LecturaInvalida(f"momento sin hora: {crudo!r}")
    try:
        momento = parse_datetime(texto)
    except ValueError:  # bien formado pero imposible: mes 13
        momento = None
    if momento is None:
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    # Un lector puede mandar la fecha sin zona. Comparar un naive contra un
    # aware explota, así que se le pone la local -- que es la del gimnasio, no
    # UTC.
    if timezone.is_naive(momento):
        momento = timezone.make_aware(momento)
    return momento


def _duracion(delta):
    minutos = round(abs(delta).total_seconds() / 60)
    dias, minutos = divmod(minutos, 60 * 24)
    horas, minutos = divmod(minutos, 60)
    if dias:
        return f"{dias} d {horas} h"
    if horas:
        return f"{horas} h {minutos} min"
    return f"{minutos} min"


def _nota_de_reloj(crudo, ahora):
    """En online el `momento` del lector no decide nada, pero si está mal es
    un reloj a corregir: queda anotado en `detalle` para el instalador."""
    if crudo is None:
        return ""
    try:
        momento = _momento_leido(crudo)
    except LecturaInvalida as exc:
        return f"reloj del lector: {exc}"
    desfase = momento - ahora
    if abs(desfase.total_seconds()) <= services.SEGUNDOS_DE_DESFASE_A_ANOTAR:
        return ""
    sentido = "adelantado" if desfase.total_seconds() > 0 else "atrasado"
    return f"reloj del lector {sentido} {_duracion(desfase)}"


def _momento_en_diferido(crudo, *, piso, tope):
    """En diferido el `momento` es LO que se registra (el equipo ya abrió):
    obligatorio y dentro del rango creíble."""
    if crudo is None:
        raise LecturaInvalida(
            "en diferido el momento es obligatorio: es la hora en que el "
            "equipo abrió"
        )
    momento = _momento_leido(crudo)
    # Con el reloj adelantado ensuciaría el antipassback y los reportes para
    # siempre; con el reloj reseteado, lo mismo hacia atrás.
    if momento > tope:
        raise LecturaInvalida(
            f"momento en el futuro ({crudo}): revisá el reloj del lector"
        )
    if momento < piso:
        raise LecturaInvalida(
            f"momento demasiado viejo ({crudo}, más de "
            f"{services.DIAS_DE_ANTIGUEDAD_TOLERADOS} días): revisá el reloj "
            "del lector"
        )
    return momento


def _id_recibido(evento):
    """El `id_evento_equipo` tal como vino, para devolverlo en la respuesta de
    un evento inválido (o `None`)."""
    if not isinstance(evento, dict):
        return None
    crudo = evento.get("id_evento_equipo")
    if isinstance(crudo, bool) or not isinstance(crudo, (str, int)):
        return None
    return crudo


def _lectura_de(evento, *, ahora, piso, tope):
    if not isinstance(evento, dict):
        raise LecturaInvalida("cada evento tiene que ser un objeto")
    tipo = evento.get("tipo")
    if tipo not in Credencial.Tipo.values:
        raise LecturaInvalida(
            f"tipo desconocido: {tipo!r} (se espera "
            f"{' o '.join(Credencial.Tipo.values)})"
        )
    valor = evento.get("valor")
    if not isinstance(valor, str):
        raise LecturaInvalida("falta «valor», o no es texto")
    sentido = evento.get("sentido", EventoIngreso.Sentido.ENTRADA)
    if sentido not in EventoIngreso.Sentido.values:
        raise LecturaInvalida(f"sentido desconocido: {sentido!r}")

    id_equipo = evento.get("id_evento_equipo")
    if id_equipo is None:
        id_equipo = ""
    elif isinstance(id_equipo, bool) or not isinstance(id_equipo, (str, int)):
        raise LecturaInvalida("id_evento_equipo tiene que ser texto o número")
    id_equipo = str(id_equipo).strip()
    # SQLite no valida el largo de un `varchar` y Postgres sí: sin esto, un
    # `DataError` en el `bulk_create` que la suite no ve.
    if len(id_equipo) > LARGO_ID_EVENTO_EQUIPO:
        raise LecturaInvalida(
            f"id_evento_equipo de más de {LARGO_ID_EVENTO_EQUIPO} caracteres"
        )

    # Un string "false" es verdadero en Python: aceptar cualquier cosa y
    # pasarla por `bool()` mandaría a diferido un evento online.
    en_diferido = evento.get("abierto_por_el_equipo", False)
    if en_diferido is None:
        en_diferido = False
    if not isinstance(en_diferido, bool):
        raise LecturaInvalida(
            f"abierto_por_el_equipo tiene que ser true o false, no {en_diferido!r}"
        )

    crudo = evento.get("momento")
    if en_diferido:
        # La idempotencia del backlog depende del id: sin él, el reintento de
        # un upload que se cortó registraría el mismo ingreso dos veces.
        if not id_equipo:
            raise LecturaInvalida(
                "en diferido id_evento_equipo es obligatorio: sin él, "
                "reintentar el upload duplicaría el ingreso"
            )
        momento = _momento_en_diferido(crudo, piso=piso, tope=tope)
        nota = ""
    else:
        # **En online manda el reloj del servidor.** El equipo está esperando
        # el veredicto para abrir, así que el ingreso ES ahora; el `momento`
        # que manda solo puede equivocarse. Un lector con la hora en UTC (tres
        # horas adelantado) o con el reloj reseteado dejaba a todos afuera, o
        # medía la reserva y el antipassback contra otro día.
        momento = ahora
        nota = _nota_de_reloj(crudo, ahora)

    return Lectura(
        tipo=tipo, valor=valor, sentido=sentido, momento=momento,
        id_equipo=id_equipo, en_diferido=en_diferido, nota=nota,
    )


def _parsear(cuerpo):
    """Una entrada por evento, en orden: una `Lectura`, o la respuesta de un
    evento inválido (un dict). Solo un error de FORMA del request entero
    levanta `CuerpoInvalido`; un evento mal armado no tumba a los demás."""
    datos = json.loads(cuerpo)
    if not isinstance(datos, dict) or "eventos" not in datos:
        raise CuerpoInvalido("se espera un objeto con «eventos»")
    eventos = datos["eventos"]
    if not isinstance(eventos, list):
        raise CuerpoInvalido("«eventos» tiene que ser una lista")
    if len(eventos) > services.MAX_EVENTOS_POR_LOTE:
        raise CuerpoInvalido(
            f"máximo {services.MAX_EVENTOS_POR_LOTE} eventos por request"
        )

    ahora = timezone.now()
    tope = ahora + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
    piso = ahora - timedelta(days=services.DIAS_DE_ANTIGUEDAD_TOLERADOS)
    entradas = []
    for evento in eventos:
        try:
            entradas.append(
                _lectura_de(evento, ahora=ahora, piso=piso, tope=tope)
            )
        except LecturaInvalida as exc:
            entradas.append(
                services.respuesta_invalida(str(exc), _id_recibido(evento))
            )
    return entradas


@method_decorator(csrf_exempt, name="dispatch")
class EventosApiView(View):
    """Lo que el molinete llama en cada lectura, y lo que le sube el lote
    cuando estuvo sin internet.

    `csrf_exempt` es el primero del proyecto y está acotado a la API: CSRF
    protege una sesión de navegador de ser montada por otro sitio, y acá no hay
    sesión ni cookie -- se autentica con un header que un form cross-site no
    puede setear. La vista **ignora `request.user` por completo**, y hay un test
    que lo fija.
    """

    http_method_names = ["post"]

    def post(self, request):
        try:
            dispositivo = auth.dispositivo_autenticado(request)
        except auth.LectorDesactivado:
            return JsonResponse({"error": "lector desactivado"}, status=403)
        except auth.ErrorDeAutenticacion:
            return JsonResponse({"error": "token invalido"}, status=401)

        # Heartbeat: `update()` para no tocar `modificado`.
        Dispositivo.objects.filter(pk=dispositivo.pk).update(
            ultimo_contacto=timezone.now()
        )

        try:
            entradas = _parsear(request.body)
        except RecursionError:
            # Un JSON anidado miles de niveles hace que `json.loads` agote la
            # pila: es un cuerpo inválido, no un 500.
            return JsonResponse({"error": "JSON demasiado anidado"}, status=400)
        except (KeyError, ValueError, TypeError) as exc:
            # `json.JSONDecodeError` y `UnicodeDecodeError` son `ValueError`.
            return JsonResponse(
                {"error": str(exc) or "cuerpo invalido"}, status=400
            )

        lecturas = [e for e in entradas if isinstance(e, Lectura)]
        decididas = iter(services.registrar_lote(dispositivo, lecturas))
        # Cada respuesta en la posición de su evento: el lector las
        # correlaciona por posición o por id.
        resultados = [
            next(decididas) if isinstance(e, Lectura) else e for e in entradas
        ]
        return JsonResponse({"resultados": resultados})
