"""Login con Google para staff/dueño (Frente C).

Coexiste con usuario+contraseña -- no lo reemplaza (decisión charlada con el
dueño del producto, ver `ISSUES.md` [2026-07-29]). Nunca crea cuentas nuevas:
solo verifica la identidad de Google y busca un `User` existente por email en
`tenants/views.py::GoogleLoginCallbackView` -- la política de "no
self-service" del proyecto (registro público cerrado) se mantiene igual acá.

Mismo patrón OAuth que `calendario/services.py` (`Flow` + PKCE), pero mucho
más chico: no persiste tokens a largo plazo, solo intercambia el `code` y lee
el email verificado del `id_token`. Los `import` de las libs de Google son
locales a cada función, mismo criterio que `calendario/services.py`.
"""

from django.conf import settings

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def disponible() -> bool:
    """True solo si están las 3 credenciales GOOGLE_* de login (ver settings)."""
    return bool(settings.GOOGLE_STAFF_LOGIN_ENABLED)


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": _TOKEN_URI,
            "redirect_uris": [settings.GOOGLE_LOGIN_REDIRECT_URI],
        }
    }


def build_flow(state=None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        _client_config(),
        scopes=settings.GOOGLE_LOGIN_SCOPES,
        state=state,
        redirect_uri=settings.GOOGLE_LOGIN_REDIRECT_URI,
    )


def build_authorization_url() -> tuple[str, str, str]:
    """URL a la que mandar al staff + el `state` (anti-CSRF) + el
    `code_verifier` de PKCE. Los tres se guardan en sesión -- mismo motivo
    que `calendario/services.py::build_authorization_url`.

    `access_type="online"` a propósito (a diferencia de Calendar, que usa
    `offline`): login no necesita `refresh_token`, es una verificación de
    identidad puntual, no una integración que vuelve a llamar a la API
    después. `prompt="select_account"` para que un staff con varias cuentas
    de Google pueda elegir cuál usar, en vez de asumir la última logueada.
    """
    flow = build_flow()
    url, state = flow.authorization_url(
        access_type="online",
        include_granted_scopes="true",
        prompt="select_account",
    )
    return url, state, flow.code_verifier


def intercambiar_code(code: str, state: str, code_verifier: str | None = None):
    """Cambia el authorization code por credenciales de Google. Pega a la
    red real -- se mockea en los tests, nunca se ejercita sin mock."""
    flow = build_flow(state=state)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


class IdentidadNoVerificada(Exception):
    """El id_token de Google no pasó la verificación (firma/aud/iss) o el
    email de la cuenta no está verificado del lado de Google."""


def _decodificar_id_token(id_token_jwt: str) -> dict:
    """Valida firma, `aud` (contra nuestro Client ID) e `iss` (que sea
    Google) del id_token -- nunca se confía en un email pasado como texto
    plano por query param o body, siempre en el JWT firmado por Google."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    return google_id_token.verify_oauth2_token(
        id_token_jwt, google_requests.Request(), settings.GOOGLE_OAUTH_CLIENT_ID
    )


def verificar_identidad(code: str, state: str, code_verifier: str | None = None) -> str:
    """Intercambia el `code` y devuelve el email verificado de la cuenta de
    Google que autorizó el login. Levanta `IdentidadNoVerificada` si Google
    no marca el email como verificado (cuentas sin verificar, dominios
    custom mal configurados, etc.) -- nunca devuelve un email en el que no
    se pueda confiar."""
    credentials = intercambiar_code(code, state, code_verifier)
    info = _decodificar_id_token(credentials.id_token)
    if not info.get("email_verified"):
        raise IdentidadNoVerificada("Google no verificó el email de esta cuenta.")
    return info["email"]
