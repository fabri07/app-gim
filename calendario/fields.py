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

    `clave_setting` es el nombre del setting con la clave Fernet. El default
    (Google) conserva el comportamiento de siempre. Con OTRA clave (tokens de
    Mercado Pago de la tienda), **leer sin la clave en el entorno devuelve
    `None`** en vez de levantar `ImproperlyConfigured`: si no, sacar las
    variables con cuentas conectadas daría 500 en el admin y al borrar un
    gimnasio. Escribir sin clave sigue levantando (nunca se guarda en claro).
    """

    CLAVE_POR_DEFECTO = "GOOGLE_TOKEN_ENCRYPTION_KEY"

    def __init__(self, *args, clave_setting=CLAVE_POR_DEFECTO, **kwargs):
        self.clave_setting = clave_setting
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Omitido si es el default: así no aparece migración en `calendario`.
        if self.clave_setting != self.CLAVE_POR_DEFECTO:
            kwargs["clave_setting"] = self.clave_setting
        return name, path, args, kwargs

    def _fernet(self):
        key = getattr(settings, self.clave_setting, "")
        if not key:
            raise ImproperlyConfigured(
                f"{self.clave_setting} no está seteada; no se pueden "
                "cifrar/descifrar tokens."
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
        except (ImproperlyConfigured, ValueError):
            # Con otra clave (MP), una clave ausente o mal formada
            # (`Fernet(...)` levanta `ValueError`) es «sin clave»: None.
            # Con la de Google no cambia nada.
            if self.clave_setting == self.CLAVE_POR_DEFECTO:
                raise
            return None
