"""El token con el que un lector se autentica contra la app.

Django-free a propósito (`SimpleTestCase`, sin base).

La base guarda SOLO el `sha256` del token, con índice único: se verifica
buscando por ese hash, en una query indexada y en tiempo constante, y un
volcado de la base no contiene ninguna llave usable.

Tres decisiones que conviene no deshacer:

1. **No se guarda en claro** (como sí hace `solicitudes.SolicitudAccesoToken`).
   Ese es un token de un solo uso con vencimiento; éste es una llave de larga
   vida que abre una puerta y baja el padrón entero de alumnos.
2. **No se usa `make_password`/`check_password`.** PBKDF2 cuesta ~100 ms de CPU
   BLOQUEANTE por request, y gunicorn corre con un único worker sync
   (`render.yaml:46`, sin `--workers`). Entre los scans y el poll del padrón son
   miles de requests por día. Con 256 bits de entropía de `secrets`, el
   key-stretching no compra nada: no hay diccionario que atacar.
3. **Sin "pimienta" con `SECRET_KEY`**, aunque `solicitudes/antispam.py:75-79` la
   use para otra cosa: rotar `SECRET_KEY` dejaría sin funcionar, en silencio y de
   golpe, la puerta de todos los gimnasios.
"""

import hashlib
import secrets

BYTES_DE_TOKEN = 32


def generar():
    """Un token nuevo, para mostrarle al staff UNA sola vez."""
    return secrets.token_urlsafe(BYTES_DE_TOKEN)


def hash_de(token):
    """Lo único que se persiste. No lleva sal: la entrada ya es aleatoria."""
    return hashlib.sha256(token.encode()).hexdigest()
