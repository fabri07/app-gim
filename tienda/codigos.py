"""Códigos de barras de las variantes de la tienda.

Cada variante tiene un código: el que trae el producto de fábrica (lo carga
el staff, a mano o con el lector) o, si no tiene, uno que inventa la app.

El inventado es un EAN-13 del rango GS1 20–29, «uso interno del comercio»:
ningún producto de fábrica lo trae, así que nunca choca con uno real. Sale
del `pk` de la variante (`20` + `pk` a 10 dígitos + verificador): es único
sin consultar la base y sin carreras entre dos staff guardando a la vez.

Las funciones de acá se copian DENTRO de la migración 0007 (una migración no
importa código de la app: el día que esto cambie, la migración no debe
cambiar con él).
"""

import re

from django.core.exceptions import ValidationError

PREFIJO_INTERNO = "20"
LARGO_MAXIMO = 32

#: Letras, dígitos y guion: lo que manda un lector y lo que se puede tipear
#: sin dudas. Se valida DESPUÉS de normalizar (mayúsculas, sin espacios).
FORMATO = re.compile(r"^[A-Z0-9-]{4,32}$")

MENSAJE_FORMATO = "Usá entre 4 y 32 letras, números y guiones, sin espacios ni acentos."
MENSAJE_RESERVADO = "Los códigos que empiezan con 20 los reserva la app: dejalo vacío y la app inventa uno."


def digito_verificador(doce_digitos):
    """Dígito de control EAN-13 de los primeros 12 dígitos (pesos 1 y 3
    alternados desde la izquierda)."""
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doce_digitos))
    return str((10 - suma % 10) % 10)


def codigo_interno(pk):
    base = f"{PREFIJO_INTERNO}{pk:010d}"
    return base + digito_verificador(base)


def normalizar(codigo):
    """Mayúsculas y sin espacios (ni al medio: un lector o un copiar/pegar a
    veces los mete). Así la unicidad «sin mayúsculas» es comparar iguales."""
    return "".join((codigo or "").split()).upper()


def es_reservado(codigo):
    """13 dígitos que empiezan con 20: la forma de un código interno."""
    return len(codigo) == 13 and codigo.isdigit() and codigo.startswith(PREFIJO_INTERNO)


def validar_codigo(codigo, pk=None):
    """Valida un código ya normalizado (vacío vale: la app inventa uno). Un
    código con forma de interno sólo vale si es el de ESTA variante (`pk`):
    así re-guardar el form con el código que la app le puso no da error, y
    nadie puede copiarse el interno de otra."""
    if not codigo:
        return
    if not FORMATO.match(codigo):
        raise ValidationError(MENSAJE_FORMATO)
    if es_reservado(codigo) and (pk is None or codigo != codigo_interno(pk)):
        raise ValidationError(MENSAJE_RESERVADO)
