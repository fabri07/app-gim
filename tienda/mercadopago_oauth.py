"""OAuth de Mercado Pago para la tienda: cada gimnasio conecta SU cuenta.

Habla con la API a través de `suscripcion.mercadopago._pedir` (un único punto
de red, parcheable en los tests y que levanta bajo `TESTING`). El canje y el
refresco mandan el `client_secret` en el cuerpo: viaja envuelto en `Secreto` y
el dict se arma como argumento, nunca atado a un nombre local, para que no
quede en claro en las variables locales de un frame (Sentry las captura).
El canje no lleva `Authorization` (`sin_autorizacion=True`).

Las funciones que hablan con la API levantan `ErrorDeMercadoPago`; devuelven la
respuesta JSON tal cual (con tokens: el que llama la guarda cifrada y no la
loguea).
"""

import urllib.parse

from django.conf import settings

from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto, _pedir

URL_AUTORIZACION = "https://auth.mercadopago.com.ar/authorization"


def url_de_autorizacion(state, code_challenge):
    """A dónde mandar al dueño para que autorice (PKCE S256)."""
    consulta = urllib.parse.urlencode(
        {
            "client_id": settings.MERCADOPAGO_CLIENT_ID,
            "response_type": "code",
            "platform_id": "mp",
            "redirect_uri": settings.MERCADOPAGO_OAUTH_REDIRECT_URI,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{URL_AUTORIZACION}?{consulta}"


def _validado(respuesta):
    if not isinstance(respuesta, dict):
        raise ErrorDeMercadoPago("Respuesta inválida en POST /oauth/token")
    return respuesta


def canjear_codigo(code, code_verifier):
    """`authorization_code` -> `{access_token, refresh_token, expires_in, ...}`."""
    return _validado(
        _pedir(
            "POST",
            "/oauth/token",
            cuerpo=Secreto(
                {
                    "client_id": settings.MERCADOPAGO_CLIENT_ID,
                    "client_secret": settings.MERCADOPAGO_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.MERCADOPAGO_OAUTH_REDIRECT_URI,
                    "code_verifier": code_verifier,
                }
            ),
            sin_autorizacion=True,
        )
    )


def refrescar(refresh_token):
    """`refresh_token` (un `Secreto`) -> tokens nuevos. El refresh token es de
    un solo uso: quien llama guarda el nuevo en el mismo paso."""
    return _validado(
        _pedir(
            "POST",
            "/oauth/token",
            cuerpo=Secreto(
                {
                    "client_id": settings.MERCADOPAGO_CLIENT_ID,
                    "client_secret": settings.MERCADOPAGO_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token.valor,
                }
            ),
            sin_autorizacion=True,
        )
    )
