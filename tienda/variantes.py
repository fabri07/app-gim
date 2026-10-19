"""Variantes por características: el nombre calculado y las sugerencias.

Un producto tiene hasta dos características (texto libre: «Talle», «Color»,
«Sabor»…) y cada variante un valor por característica. `Variante.nombre` se
calcula de esos valores («M · Negro»): todo lo que ya leía `nombre`
(catálogo, carrito, snapshot del pedido, exportación) sigue igual.

`SUGERENCIAS` es sólo ayuda para el generador del formulario, nunca
validación: nada de `TextChoices` global (el vocabulario del gimnasio #2 rara
vez es el del #1; pasó con `CategoriaEjercicio`).
"""

import unicodedata

SEPARADOR = " · "
LARGO_MAXIMO_NOMBRE = 60
MAXIMO_VARIANTES = 50

SUGERENCIAS = {
    "Talle": ["XS", "S", "M", "L", "XL", "XXL", "XXXL"],
    "Talle numérico": [str(n) for n in range(34, 47, 2)],
    "Color": ["Negro", "Blanco", "Gris", "Azul", "Rojo", "Verde", "Rosa", "Beige"],
    "Peso": ["100 g", "250 g", "500 g", "1 kg", "2 kg", "3 kg", "5 kg"],
    "Sabor": ["Chocolate", "Vainilla", "Frutilla", "Cookies", "Banana", "Neutro"],
}


def componer_nombre(valor_1, valor_2):
    """«M · Negro», «M» o «» (producto sin características)."""
    return SEPARADOR.join(v for v in ((valor_1 or "").strip(), (valor_2 or "").strip()) if v)


def clave(texto):
    """Para comparar sin mayúsculas ni acentos: «Sábor » == «sabor»."""
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))
