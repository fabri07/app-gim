"""Parser de kilos para el ranking de reconocimientos.

`RutinaAsignadaItem.kilos` es texto libre a propósito: el staff y el alumno
escriben lo que se les ocurre («20», «20 kg», «15kg c/u», «10-12kg»,
«corporal», «60s», «RPE 8»). Para el ranking sólo cuenta lo que se lee sin
ambigüedad como un peso en kilos; todo lo demás se descarta (devuelve None)
en vez de adivinar.

Se acepta: un número (coma o punto decimal), opcionalmente un rango con
guión (gana el mayor), opcionalmente la unidad kg/kgs/kilo/kilos y, sólo si
hubo unidad, un aclarador de reparto («c/u», «c/lado», «por lado», «cada uno»).

Se descarta: vacíos, texto sin número, tiempos, porcentajes, signos («+10kg»),
otras unidades («libras»), varios pesos sueltos («20kg 30kg»), cero y
cualquier valor mayor a `MAXIMO_KILOS` (un error de tipeo no debe dominar el
ranking).
"""
import re
from decimal import Decimal

MAXIMO_KILOS = Decimal("500")

_NUM = r"[0-9]+(?:[.,][0-9]+)?"  # no `\d`: aceptaría dígitos árabes o de ancho completo
_PATRON = re.compile(
    rf"(?P<a>{_NUM})"
    rf"(?:\s*[-–]\s*(?P<b>{_NUM}))?"
    r"(?:\s*(?P<unidad>kgs?|kilos?)"
    r"(?:\s*(?:c/u|c/lado|por lado|cada uno))?)?"
)


def parsear_kilos(texto):
    """Devuelve los kilos como `Decimal`, o None si no se lee como un peso."""
    if not texto:
        return None
    m = _PATRON.fullmatch(texto.strip().lower())
    if not m:
        return None
    valores = [Decimal(g.replace(",", ".")) for g in (m.group("a"), m.group("b")) if g]
    valor = max(valores)
    if valor <= 0 or valor > MAXIMO_KILOS:
        return None
    return valor
