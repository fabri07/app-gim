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
    """Lo que la vista traduce a 400. Subclase de `ValueError` para que la
    atrape el mismo `except` que el JSON mal formado."""


def _momento_de(crudo, *, piso, tope):
    """El `momento` que manda el lector, ya con zona y dentro del rango
    creíble. Ausente es ahora."""
    if crudo is None:
        return timezone.now()
    if not isinstance(crudo, str):
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    momento = parse_datetime(crudo)  # ValueError si la fecha es imposible
    if momento is None:
        raise LecturaInvalida(f"momento ilegible: {crudo!r}")
    # Un lector puede mandar la fecha sin zona. Comparar un naive contra un
    # aware explota, así que se le pone la local -- que es la del gimnasio, no
    # UTC.
    if timezone.is_naive(momento):
        momento = timezone.make_aware(momento)
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
            "del lector o descartá ese evento"
        )
    return momento


def _lectura_de(evento, *, piso, tope):
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
    id_equipo = "" if id_equipo is None else str(id_equipo).strip()
    # SQLite no valida el largo de un `varchar` y Postgres sí: sin esto, un
    # `DataError` en el `bulk_create` que la suite no ve.
    if len(id_equipo) > LARGO_ID_EVENTO_EQUIPO:
        raise LecturaInvalida(
            f"id_evento_equipo de más de {LARGO_ID_EVENTO_EQUIPO} caracteres"
        )
    return Lectura(
        tipo=tipo, valor=valor, sentido=sentido,
        momento=_momento_de(evento.get("momento"), piso=piso, tope=tope),
        id_equipo=id_equipo,
        en_diferido=bool(evento.get("abierto_por_el_equipo")),
    )


def _parsear(cuerpo):
    """`list[Lectura]`, o `LecturaInvalida` con un mensaje que el instalador
    pueda leer. Un evento inválido rechaza el lote ENTERO (nada se registra a
    medias), y el mensaje dice cuál fue para que el puente pueda descartarlo."""
    datos = json.loads(cuerpo)
    if not isinstance(datos, dict) or "eventos" not in datos:
        raise LecturaInvalida("se espera un objeto con «eventos»")
    eventos = datos["eventos"]
    if not isinstance(eventos, list):
        raise LecturaInvalida("«eventos» tiene que ser una lista")
    if len(eventos) > services.MAX_EVENTOS_POR_LOTE:
        raise LecturaInvalida(
            f"máximo {services.MAX_EVENTOS_POR_LOTE} eventos por request"
        )

    ahora = timezone.now()
    tope = ahora + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
    piso = ahora - timedelta(days=services.DIAS_DE_ANTIGUEDAD_TOLERADOS)
    lecturas = []
    for numero, evento in enumerate(eventos, start=1):
        try:
            lecturas.append(_lectura_de(evento, piso=piso, tope=tope))
        except LecturaInvalida as exc:
            raise LecturaInvalida(f"evento {numero}: {exc}") from None
    return lecturas


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
            lecturas = _parsear(request.body)
        except (KeyError, ValueError, TypeError) as exc:
            # `json.JSONDecodeError` y `UnicodeDecodeError` son `ValueError`.
            return JsonResponse(
                {"error": str(exc) or "cuerpo invalido"}, status=400
            )

        resultados = services.registrar_lote(dispositivo, lecturas)
        return JsonResponse({"resultados": resultados})
