"""Campo de modelo que cifra su contenido en reposo (Parte C).

Se usa para los tokens OAuth de Google (`refresh_token`/`access_token`): que la
DB no guarde el token en texto plano. Cifra con Fernet usando
`settings.GOOGLE_TOKEN_ENCRYPTION_KEY`. Un valor vacío (`None`/`""`) se guarda
tal cual, no se cifra.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


class EncryptedTextField(models.TextField):
    """`TextField` cuyo valor se persiste cifrado con Fernet.

    El cifrado es no determinístico (Fernet incluye timestamp/IV), así que NO se
    puede filtrar por este campo -- y no hace falta: solo guarda tokens que se
    leen por instancia. Si la clave está rotada o el dato corrupto, `from_db`
    devuelve `None` (se trata como "sin token" -> se fuerza reconexión) en vez
    de reventar el request.
    """

    def _fernet(self):
        key = settings.GOOGLE_TOKEN_ENCRYPTION_KEY
        if not key:
            raise ImproperlyConfigured(
                "GOOGLE_TOKEN_ENCRYPTION_KEY no está seteada; no se puede "
                "cifrar/descifrar tokens de Google Calendar."
            )
        return Fernet(key.encode() if isinstance(key, str) else key)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return self._fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        try:
            return self._fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return None
