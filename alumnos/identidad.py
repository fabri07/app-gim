"""Normalización del dato con el que un alumno inicia sesión.

El identificador del alumno es su email o su teléfono, a elección del staff.
Los dos entran tal cual en `auth.User.username`: `UnicodeUsernameValidator`
acepta `@` y `+` (su regex es `^[\\w.@+-]+\\Z`; lo único que rechaza es
whitespace), así que no hace falta un `User` custom ni tocar `AUTH_USER_MODEL`.
Ver `alumnos/tests.py::IdentidadTests.test_el_identificador_entra_en_username`.

Este módulo es Django-free a propósito, salvo por el tipo de excepción: es
lógica pura, no toca modelos ni base de datos, y se testea con `SimpleTestCase`
(mismo precedente que `importaciones/parsing.py`). El motivo no es elegancia:
el riesgo real es que la normalización difiera entre el alta y el login, porque
ahí el alumno no entra nunca y no tiene forma de darse cuenta solo. Sin base de
datos, una tabla exhaustiva de casos sale barata.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

TIPO_EMAIL = "email"
TIPO_TELEFONO = "telefono"
TIPOS = [(TIPO_EMAIL, "Email"), (TIPO_TELEFONO, "Teléfono")]

# Largo de un número argentino sin prefijo de país: característica + abonado.
_LARGO_NACIONAL = 10


def normalizar_email(valor):
    """Devuelve el email en minúsculas y sin espacios alrededor.

    El lowercase NO es cosmético: `User.objects.get(username=...)` es
    case-sensitive en Postgres, así que sin esto `Juan@x.com` y `juan@x.com`
    serían dos cuentas distintas y el alumno no podría entrar con lo que el
    staff le dictó.
    """
    valor = (valor or "").strip().lower()
    validate_email(valor)
    return valor


def normalizar_telefono(valor):
    """Devuelve el teléfono argentino en forma canónica `+54...`.

    En este orden: se descarta todo lo que no sea dígito (salvo un `+`
    inicial), se saca el `0` de la característica y el `15` que se intercala
    antes del abonado. Los dos son convenciones de discado nacional que no van
    en la forma internacional, y la gente los escribe indistintamente.
    """
    crudo = (valor or "").strip()
    digitos = re.sub(r"\D", "", crudo)

    if not digitos:
        raise ValidationError("Escribí un número de teléfono.")

    # `+54...` y `0054...` son la misma cosa: el `00` es el prefijo de salida
    # internacional. Se normalizan al mismo caso para que el chequeo de país
    # de abajo cubra los dos (si no, `0012025550123` se colaba como nacional).
    tenia_mas = crudo.startswith("+")
    if digitos.startswith("00"):
        digitos = digitos[2:]
        tenia_mas = True

    if tenia_mas and not digitos.startswith("54"):
        # AR-only a propósito. Sin este corte, un `+1...` de EE.UU. terminaría
        # convertido en `+541...`: una transformación silenciosa sobre el dato
        # con el que el alumno inicia sesión.
        raise ValidationError(
            "Por ahora solo se aceptan teléfonos argentinos. Escribilo con "
            "característica, por ejemplo 11 2233-4455."
        )

    if tenia_mas or digitos.startswith("54"):
        digitos = digitos.removeprefix("54")
    else:
        digitos = digitos.removeprefix("0")
        # El `15` solo se saca si al hacerlo queda un número de largo nacional:
        # si no, un abonado que legítimamente empieza con 15 se rompería.
        if len(digitos) == _LARGO_NACIONAL + 2:
            for largo_area in (2, 3, 4):
                resto = digitos[largo_area:]
                if resto.startswith("15"):
                    digitos = digitos[:largo_area] + resto[2:]
                    break

    if len(digitos) < _LARGO_NACIONAL:
        raise ValidationError(
            "El teléfono quedó demasiado corto. Escribilo con característica, "
            "por ejemplo 11 2233-4455."
        )
    return f"+54{digitos}"


# Menos dígitos que esto no alcanzan para decir que dos teléfonos son el mismo.
_DIGITOS_MINIMOS_CLAVE = 6


def clave_telefono(valor):
    """Clave para detectar teléfonos repetidos; `None` si no hay con qué.

    Sirve SOLO para comparar (el importador de alumnos, para no cargar dos
    veces a la misma persona), no para guardar ni para iniciar sesión. Por eso
    no levanta como `normalizar_telefono`: un número que esa función rechazaría
    (uno de afuera, uno corto) igual puede estar repetido en el padrón, y lo
    peor que se puede hacer acá es no verlo.

    Para un número argentino devuelve los 10 dígitos nacionales (característica
    más abonado), sin el `0`, el `15`, el `54` ni el `9` de celular: las cinco
    formas en que la gente escribe el mismo número dan la misma clave. Un
    número de otro país se compara por sus dígitos tal cual.
    """
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) < _DIGITOS_MINIMOS_CLAVE:
        return None

    digitos = digitos.removeprefix("00")
    # El `54` solo se saca si sobra algo más que un número nacional: si no, una
    # característica que empieza con 54 se rompería.
    if digitos.startswith("54") and len(digitos) > _LARGO_NACIONAL:
        digitos = digitos.removeprefix("54")
        # El `9` de celular (`+54 9 11 ...`) no es parte del número nacional.
        if digitos.startswith("9") and len(digitos) == _LARGO_NACIONAL + 1:
            digitos = digitos[1:]
    else:
        digitos = digitos.removeprefix("0")
        # Mismo criterio que `normalizar_telefono`: el `15` solo se saca si
        # queda un número de largo nacional.
        if len(digitos) == _LARGO_NACIONAL + 2:
            for largo_area in (2, 3, 4):
                resto = digitos[largo_area:]
                if resto.startswith("15"):
                    digitos = digitos[:largo_area] + resto[2:]
                    break

    return digitos if len(digitos) >= _DIGITOS_MINIMOS_CLAVE else None


def normalizar_identificador(tipo, valor):
    """Despacha a la función que corresponda según `tipo`."""
    if tipo == TIPO_EMAIL:
        return normalizar_email(valor)
    if tipo == TIPO_TELEFONO:
        return normalizar_telefono(valor)
    raise ValidationError("Elegí si el identificador es un email o un teléfono.")
