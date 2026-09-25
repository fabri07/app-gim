"""
Anti-spam del formulario público de solicitud, en capas y SIN dependencia nueva
(honeypot + tiempo mínimo firmado + dedup + rate-limit por IP con
`django.core.cache`, mismo patrón que `plataforma/cambio.py`).

Nada de esto reemplaza al índice único parcial de `SolicitudAcceso` ni a la
neutralidad de enumeración de la vista: son capas baratas para frenar bots
antes de tocar la base.
"""

import hashlib

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils import timezone

#: Un envío más rápido que esto es un bot (un humano tarda en llenar el form).
MIN_SEGUNDOS = 1.5
#: Máximo de altas por IP por hora.
MAX_POR_HORA = 5
#: Ventana de deduplicación por email (segundos).
DEDUP_SEGUNDOS = 600
#: Un form más viejo que esto se considera vencido (2 h).
MAX_EDAD_FORM = 60 * 60 * 2

_SIGNER_SALT = "solicitudes.antispam.tiempo"


def token_de_tiempo():
    """Valor firmado para el campo oculto que mide cuánto tardó el envío.

    Se firma con `TimestampSigner` (vía `signing.dumps`), así el cliente no
    puede falsificar un tiempo viejo para saltear el mínimo.
    """
    return signing.dumps(
        timezone.now().timestamp(), salt=_SIGNER_SALT, compress=True
    )


def demasiado_rapido(token, ahora=None):
    """True si el form se envió sospechosamente rápido, con un token inválido,
    o vencido. Cualquiera de esos casos es 'no es un humano llenando el form'.
    """
    if not token:
        return True
    try:
        arranque = signing.loads(token, salt=_SIGNER_SALT, max_age=MAX_EDAD_FORM)
    except signing.BadSignature:
        return True
    ahora = (ahora or timezone.now()).timestamp()
    return (ahora - float(arranque)) < MIN_SEGUNDOS


def honeypot_lleno(valor):
    """El campo trampa `website` debe venir vacío; si un bot lo completó, fuera."""
    return bool(valor and valor.strip())


def ip_de(request):
    """IP del cliente. Detrás de Cloudflare/Render viene en X-Forwarded-For
    (primer hop); si no, `REMOTE_ADDR`."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hash_de(valor):
    """sha256(valor + SECRET_KEY): trazabilidad sin guardar el dato en claro."""
    if not valor:
        return ""
    return hashlib.sha256(f"{valor}{settings.SECRET_KEY}".encode()).hexdigest()


def rate_limit_excedido(ip_hash):
    """True si esta IP ya superó `MAX_POR_HORA` altas en la última hora."""
    if not ip_hash:
        return False
    clave = f"solicitud:rl:{ip_hash}"
    if cache.add(clave, 1, timeout=3600):
        return False
    try:
        actual = cache.incr(clave)
    except ValueError:
        # La clave venció entre el add y el incr: arrancamos de nuevo.
        cache.set(clave, 1, timeout=3600)
        return False
    return actual > MAX_POR_HORA


def duplicado_reciente(email_hash):
    """True si ese email ya mandó una solicitud en los últimos
    `DEDUP_SEGUNDOS`. `cache.add` devuelve False si la clave ya existía."""
    if not email_hash:
        return False
    clave = f"solicitud:dedup:{email_hash}"
    return not cache.add(clave, 1, timeout=DEDUP_SEGUNDOS)
