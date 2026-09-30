"""
Cliente de Mercado Pago (Checkout Pro) para cobrar la suscripción a la app.

Molde: `plataforma/cambio.py` (`urllib` de la biblioteca estándar, un único
punto que toca la red para parchearlo en los tests, `USER_AGENT` propio), **con
la regla de errores al revés**. `cambio.py` traga todo y devuelve `None`
porque el panel no puede caerse por una API de terceros y no hay plata en
juego. Acá SÍ hay plata: **las funciones que hablan con la API levantan
`ErrorDeMercadoPago`** y quien llama decide cómo degradar. Un `None`
silencioso en el camino de la plata haría que un test que olvida parchear la
red pase en verde con el pago sin acreditar.

**Esta integración usa UNA sola credencial, la de la plataforma**; no hay
OAuth por gimnasio ni split. Si algún día el gimnasio le cobra a sus alumnos
por Mercado Pago (app `pagos`), eso necesita credenciales POR gimnasio,
cifradas con `calendario.fields.EncryptedTextField`: este módulo no sirve para
eso tal cual.

Nunca se loguea ni se devuelve en un error el token ni el cuerpo de la
respuesta de la API.
"""

import hashlib
import hmac
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone as _tz

from django.conf import settings
from django.utils import timezone

BASE = "https://api.mercadopago.com"

#: Segundos de espera. Corre dentro del request y el único worker de gunicorn
#: tiene 30 s. Cero reintentos internos: el reintento es el de Mercado Pago a
#: los 15 minutos, o el cron.
TIMEOUT = 5

USER_AGENT = "TuGimApp/1.0 (+https://www.tugimapp.com)"

_FORMATO_FECHA = "%Y-%m-%dT%H:%M:%S.000+00:00"

_CLAVES_DEL_RESUMEN = (
    "id",
    "status",
    "status_detail",
    "transaction_amount",
    "currency_id",
    "external_reference",
    "date_approved",
    "date_last_updated",
    "payment_type_id",
    "payment_method_id",
)


class ErrorDeMercadoPago(Exception):
    """La API no respondió lo esperado. El mensaje es corto y sin secretos.

    `codigo` es el status HTTP cuando la API contestó con un error (`None` si
    no hubo respuesta: red, timeout, JSON inválido). Lo usa el webhook para
    distinguir «el pago no existe» (404, no se arregla reintentando) de «Mercado
    Pago no contestó» (500 para que reintente)."""

    def __init__(self, mensaje, *, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


def _headers(clave_idempotencia):
    headers = {
        "Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if clave_idempotencia:
        headers["X-Idempotency-Key"] = clave_idempotencia
    return headers


def _pedir(metodo, ruta, *, cuerpo=None, clave_idempotencia=None):
    """El único punto que toca la red. Devuelve el JSON parseado.

    Bajo `TESTING` levanta `RuntimeError`: un patch olvidado tiene que romper
    el test, no degradarlo. El token se lee en cada llamada para que
    `override_settings` sirva.
    """
    if settings.TESTING:
        raise RuntimeError(
            "suscripcion.mercadopago._pedir no puede salir a la red en la "
            "suite: parchear la función pública que se está usando."
        )
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    # Los headers se arman en otra función y no se atan a ningún nombre local
    # de `_pedir`: Sentry captura las variables locales de cada frame y su
    # scrubber por defecto no es recursivo (filtra `token`, no `headers`), así
    # que un `headers` local con el Bearer viajaría a Sentry en cualquier
    # excepción que pase por acá.
    pedido = urllib.request.Request(
        BASE + ruta,
        data=datos,
        headers=_headers(clave_idempotencia),
        method=metodo,
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ErrorDeMercadoPago(
            f"HTTP {e.code} en {metodo} {ruta}", codigo=e.code
        ) from None
    except urllib.error.URLError:
        raise ErrorDeMercadoPago(f"Sin conexión en {metodo} {ruta}") from None
    except TimeoutError:
        raise ErrorDeMercadoPago(f"Timeout en {metodo} {ruta}") from None
    except ValueError:
        raise ErrorDeMercadoPago(f"Respuesta inválida en {metodo} {ruta}") from None
    except (OSError, http.client.HTTPException):
        # Cortes al LEER la respuesta (`ConnectionResetError`, `IncompleteRead`):
        # urllib no los envuelve y los llamadores sólo atrapan ErrorDeMercadoPago.
        raise ErrorDeMercadoPago(f"Sin conexión en {metodo} {ruta}") from None


def es_de_prueba():
    """Un token `TEST-...` es de sandbox."""
    return settings.MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")


def crear_preferencia(
    *,
    titulo,
    monto_ars,
    referencia_externa,
    url_retorno,
    url_notificacion,
    expira,
    email_pagador="",
    ahora=None,
):
    """Crea la preferencia de Checkout Pro: `{"id", "init_point"}`.

    `init_point` sale de `sandbox_init_point` con un token de prueba y de
    `init_point` con uno real: si no, el riesgo concreto es desplegarle un link
    de sandbox a un cliente real. `expira` es un datetime aware; `ahora` es
    opcional y existe para testear (por defecto, `timezone.now()`).
    """
    ahora = ahora or timezone.now()
    payload = {
        # `float()` porque `json.dumps` levanta `TypeError` con un `Decimal`;
        # el monto ya viene cuantizado a 0,01.
        "items": [
            {
                "title": titulo,
                "quantity": 1,
                "unit_price": float(monto_ars),
                "currency_id": "ARS",
            }
        ],
        "external_reference": referencia_externa,
        "back_urls": {
            "success": url_retorno,
            "failure": url_retorno,
            "pending": url_retorno,
        },
        # `binary_mode` + sin `ticket`/`atm`: «pago parcial» o «pendiente por
        # días» dejan de existir como caso (un pago de tarjeta no puede quedar
        # en revisión manual de MP, y un Rapipago pagado a los 3 días llegaría
        # con el tipo de cambio de hace 3 días). Costo asumido: se pierde al
        # que paga en efectivo.
        "binary_mode": True,
        "payment_methods": {
            "excluded_payment_types": [{"id": "ticket"}, {"id": "atm"}]
        },
        "expires": True,
        "expiration_date_from": ahora.astimezone(_tz.utc).strftime(_FORMATO_FECHA),
        "expiration_date_to": expira.astimezone(_tz.utc).strftime(_FORMATO_FECHA),
        "statement_descriptor": "TUGIMAPP",
    }
    if email_pagador:
        payload["payer"] = {"email": email_pagador}
    # MP rechaza `localhost`/`http` en estos dos; en desarrollo no hay HTTPS.
    if url_retorno.startswith("https://"):
        payload["auto_return"] = "approved"
    if url_notificacion.startswith("https://"):
        payload["notification_url"] = url_notificacion

    respuesta = _pedir(
        "POST",
        "/checkout/preferences",
        cuerpo=payload,
        clave_idempotencia=referencia_externa,
    )
    clave = "sandbox_init_point" if es_de_prueba() else "init_point"
    try:
        return {"id": respuesta["id"], "init_point": respuesta[clave]}
    except (KeyError, TypeError):
        raise ErrorDeMercadoPago(
            "Respuesta sin id o link de pago en POST /checkout/preferences"
        ) from None


def consultar_pago(payment_id):
    """`GET /v1/payments/{id}` como dict."""
    id_seguro = urllib.parse.quote(str(payment_id), safe="")
    return _pedir("GET", f"/v1/payments/{id_seguro}")


def buscar_pagos(referencia_externa):
    """Pagos de una referencia, del más nuevo al más viejo.

    Es lo único que cubre «el webhook se perdió y el usuario cerró la
    pestaña»: ahí nunca hubo un `payment_id` que consultar.
    """
    consulta = urllib.parse.urlencode(
        {
            "external_reference": referencia_externa,
            "sort": "date_created",
            "criteria": "desc",
        }
    )
    respuesta = _pedir("GET", f"/v1/payments/search?{consulta}")
    try:
        return respuesta["results"]
    except (KeyError, TypeError):
        raise ErrorDeMercadoPago(
            "Respuesta sin results en GET /v1/payments/search"
        ) from None


def resumen_de_pago(pago):
    """Sólo lo que se guarda en la base: nada del pagador. Una respuesta que
    no es un objeto JSON da `{}` (sin referencia: `acreditar` la descarta)."""
    if not isinstance(pago, dict):
        return {}
    return {k: pago[k] for k in _CLAVES_DEL_RESUMEN if k in pago}


def firma_valida(secret, *, data_id, request_id, x_signature):
    """¿El webhook lo mandó Mercado Pago? Puro: no levanta nunca.

    El header es `ts=<epoch>,v1=<hex>`. El manifest concatena, en este orden y
    **omitiendo el segmento cuyo valor falta** (no va vacío): `id:<data_id en
    minúsculas>;`, `request-id:<request_id>;`, `ts:<ts>;`.

    **El `ts` NO se valida como ventana de frescura**: un replay no puede
    cobrar dos veces porque la defensa real es la idempotencia, y un chequeo
    de reloj es un reloj más que puede desincronizarse y rechazar un reintento
    legítimo de MP a las 3 de la mañana.
    """
    try:
        if not secret or not x_signature:
            return False
        partes = {}
        for parte in str(x_signature).split(","):
            clave, sep, valor = parte.partition("=")
            if sep:
                partes[clave.strip()] = valor.strip()
        ts, v1 = partes.get("ts"), partes.get("v1")
        if not ts or not v1:
            return False
        manifest = ""
        if data_id:
            manifest += f"id:{str(data_id).lower()};"
        if request_id:
            manifest += f"request-id:{request_id};"
        manifest += f"ts:{ts};"
        esperado = hmac.new(
            secret.encode(), manifest.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(esperado.encode(), v1.encode())
    except Exception:
        return False
