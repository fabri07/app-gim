"""Autenticación del lector de la puerta.

No usa sesión ni cookie: el lector manda `Authorization: Bearer <token>` y se
lo busca por el `sha256` de ese token, con índice único (ver `tokens.py`).
**Nunca mira `request.user`**: es lo que hace seguro el `csrf_exempt` de las
vistas de la API.
"""

from ingreso import tokens
from ingreso.models import Dispositivo

ESQUEMA = "bearer"
"""Se compara sin distinguir mayúsculas: el esquema de `Authorization` es
case-insensitive (RFC 9110 §11.1) y hay firmwares que mandan `bearer`."""


class ErrorDeAutenticacion(Exception):
    """Base. El status lo elige la vista."""


class SinToken(ErrorDeAutenticacion):
    """401. No se distingue "no hay token" de "el token no matchea": mismo
    criterio anti-enumeración que el resto del proyecto."""


class LectorDesactivado(ErrorDeAutenticacion):
    """403. Solo alcanzable con un token VÁLIDO, así que no le revela nada a
    quien no tiene la llave, y sí le dice al instalador lo que necesita: 401 es
    "revisá el token", 403 es "el gimnasio apagó este lector"."""


def dispositivo_autenticado(request):
    esquema, _, presentado = (
        request.META.get("HTTP_AUTHORIZATION", "").strip().partition(" ")
    )
    if esquema.lower() != ESQUEMA:
        raise SinToken()
    presentado = presentado.strip()
    if not presentado:
        raise SinToken()
    try:
        dispositivo = Dispositivo.objects.select_related("gimnasio").get(
            token_hash=tokens.hash_de(presentado)
        )
    except Dispositivo.DoesNotExist:
        raise SinToken() from None
    if not dispositivo.activo:
        raise LectorDesactivado()
    return dispositivo
