"""Nunca el token de la plataforma para cobrarle al alumno.

`suscripcion.mercadopago._headers` manda el token de LA PLATAFORMA
(`MERCADOPAGO_ACCESS_TOKEN`) cuando una llamada no pasa `token=`. En la tienda
y en las cuotas eso es grave en silencio: la preferencia se crearía en la
cuenta del dueño de TuGimApp y la plata del alumno iría a parar ahí, o el pago
del gimnasio se leería con otra cuenta y daría 404. Ningún test de
comportamiento lo ve si parchea la función entera.

Por eso un test estructural: recorre el código (no los tests ni las
migraciones) de `tienda/` y `pagos/` con `ast` y exige que toda llamada a
`crear_preferencia`, `consultar_pago` o `buscar_pagos` lleve `token=` y que no
sea la constante `None`. Verificado en rojo quitando el `token=` de
`tienda/pagos_mp.py::acreditar_pedido`.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

FUNCIONES = frozenset({"crear_preferencia", "consultar_pago", "buscar_pagos"})
APPS = ("tienda", "pagos")


def _archivos():
    for app in APPS:
        for ruta in sorted((Path(settings.BASE_DIR) / app).rglob("*.py")):
            if "migrations" in ruta.parts or ruta.name.startswith("tests"):
                continue
            yield ruta


def _nombre(llamada):
    funcion = llamada.func
    if isinstance(funcion, ast.Name):
        return funcion.id
    if isinstance(funcion, ast.Attribute):
        return funcion.attr
    return None


def llamadas_a_mercado_pago():
    """`(ruta relativa, línea, función, nodo Call)` de cada llamada."""
    base = Path(settings.BASE_DIR)
    for ruta in _archivos():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Call) and _nombre(nodo) in FUNCIONES:
                yield str(ruta.relative_to(base)), nodo.lineno, _nombre(nodo), nodo


def _token_valido(llamada):
    for kw in llamada.keywords:
        if kw.arg == "token":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


class TokenDelGimnasioTests(SimpleTestCase):
    def test_toda_llamada_a_mercado_pago_pasa_el_token_del_gimnasio(self):
        encontradas = list(llamadas_a_mercado_pago())
        sin_token = [
            f"{ruta}:{linea} {funcion}(...) sin token= del gimnasio"
            for ruta, linea, funcion, nodo in encontradas
            if not _token_valido(nodo)
        ]
        self.assertEqual(
            sin_token, [],
            "Sin `token=` Mercado Pago usa el token de la PLATAFORMA: la plata "
            "del alumno iría a la cuenta de TuGimApp.",
        )

    def test_encuentra_las_llamadas_conocidas_de_la_tienda(self):
        """Si el recorrido no encontrara nada (un cambio de carpeta, un
        filtro mal puesto), el test de arriba pasaría vacío."""
        de_la_tienda = {
            funcion
            for ruta, _, funcion, _ in llamadas_a_mercado_pago()
            if ruta == str(Path("tienda") / "pagos_mp.py")
        }
        self.assertEqual(de_la_tienda, set(FUNCIONES))

    def test_detecta_una_llamada_sin_token_o_con_none(self):
        """El detector mismo, sobre código de muestra."""
        for codigo, esperado in (
            ("mercadopago.consultar_pago(1)", False),
            ("mercadopago.consultar_pago(1, token=None)", False),
            ("consultar_pago(1, token=Secreto(c.access_token))", True),
            ("mercadopago.buscar_pagos('r', token=token)", True),
        ):
            with self.subTest(codigo=codigo):
                llamada = ast.parse(codigo).body[0].value
                self.assertEqual(_token_valido(llamada), esperado)
