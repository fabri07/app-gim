"""Tienda del alumno adaptada a la compu (plan 2026-10-18, tarea 3).

Las dos columnas son sólo CSS (`.con-lateral`, `.tabla--pedidos`): el orden
del HTML es el del celular. Estos tests fijan qué queda en cada columna y que
los formularios y el «Botón de arrepentimiento» siguen intactos.
"""

from django.test.html import Element, parse_html
from django.urls import reverse

from tienda import services
from tienda.tests_vistas_alumno import _BaseAlumno


def _main(respuesta):
    html = respuesta.content.decode()
    inicio = html.index("<main")
    fin = html.index("</main>") + len("</main>")
    return parse_html(html[inicio:fin])


def _clases(el):
    return (dict(el.attributes).get("class") or "").split()


def _hijos(el):
    return [c for c in el.children if isinstance(c, Element)]


def _buscar(el, pred):
    encontrados = []
    if pred(el):
        encontrados.append(el)
    for hijo in _hijos(el):
        encontrados.extend(_buscar(hijo, pred))
    return encontrados


def _con_clase(el, clase):
    return _buscar(el, lambda e: clase in _clases(e))


def _tag(el, nombre):
    return _buscar(el, lambda e: e.name == nombre)


class CatalogoCabeceraTests(_BaseAlumno):
    def test_titulo_a_la_izquierda_y_links_a_la_derecha_desde_lg(self):
        main = _main(self.client.get(reverse("tienda:catalogo")))
        cabecera = _con_clase(main, "tarjeta")[0]

        for clase in ("lg:flex", "lg:justify-between", "lg:items-center"):
            self.assertIn(clase, _clases(cabecera))
        h1, p = _hijos(cabecera)
        self.assertEqual(h1.name, "h1")
        self.assertIn("Tienda", str(h1))
        self.assertEqual(p.name, "p")
        self.assertIn("lg:mb-0", _clases(p))
        self.assertIn(reverse("tienda:carrito"), str(p))
        self.assertIn(reverse("tienda:mis_pedidos"), str(p))


class CarritoDosColumnasTests(_BaseAlumno):
    def test_con_lineas_principal_y_lateral(self):
        self.agregar(2)
        r = self.client.get(reverse("tienda:carrito"))
        main = _main(r)

        self.assertIn("contenido--alumno", _clases(main))
        tarjeta, = _con_clase(main, "tarjeta")
        lateral, = _con_clase(tarjeta, "con-lateral")
        principal, costado = _hijos(lateral)

        self.assertEqual(len(_tag(tarjeta, "h1")), 1)
        self.assertIn("Tu carrito", str(_tag(principal, "h1")[0]))
        self.assertIn("Remera", str(_tag(principal, "ul")[0]))
        self.assertNotIn("Total", str(principal))

        total = _hijos(costado)[0]
        self.assertIn("Total:", str(total))
        self.assertIn("lg:mt-0", _clases(total))
        self.assertIn(reverse("tienda:checkout"), str(costado))
        self.assertIn("Confirmar pedido", str(costado))
        self.assertIn("Seguir viendo productos", str(costado))
        self.assertEqual(r.content.decode().count("Seguir viendo productos"), 1)

    def test_vacio_sin_columnas(self):
        r = self.client.get(reverse("tienda:carrito"))
        main = _main(r)
        html = r.content.decode()

        self.assertIn("contenido--alumno", _clases(main))
        self.assertEqual(_con_clase(main, "con-lateral"), [])
        self.assertEqual(html.count("<h1>Tu carrito</h1>"), 1)
        self.assertIn("Tu carrito está vacío.", html)
        self.assertEqual(html.count("Seguir viendo productos"), 1)


class MiPedidoDosColumnasTests(_BaseAlumno):
    def setUp(self):
        super().setUp()
        self.pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 1}, suplantando=False,
        )
        self.url = reverse("tienda:mi_pedido", args=[self.pedido.pk])

    def _columnas(self):
        main = _main(self.client.get(self.url))
        self.assertIn("contenido--alumno", _clases(main))
        tarjeta, = _con_clase(main, "tarjeta")
        lateral, = _con_clase(tarjeta, "con-lateral")
        return _hijos(lateral)

    def test_pendiente_items_a_la_izquierda_y_pago_al_costado(self):
        self.pedido.items.update(cantidad=1)
        principal, costado = self._columnas()

        self.assertIn(f"Pedido #{self.pedido.pk}", str(_tag(principal, "h1")[0]))
        self.assertIn("1 × Remera", str(principal))
        self.assertNotIn("Transferí", str(principal))

        self.assertIn("Transferí", str(costado))
        form, = [f for f in _tag(costado, "form")
                 if dict(f.attributes).get("enctype") == "multipart/form-data"]
        self.assertEqual(dict(form.attributes).get("hx-boost"), "false")
        self.assertEqual(
            dict(form.attributes)["action"],
            reverse("tienda:mi_pedido_comprobante", args=[self.pedido.pk]),
        )
        self.assertIn("Cancelar pedido", str(costado))
        self.assertIn(reverse("tienda:mis_pedidos"), str(costado))

    def test_pagado_el_arrepentimiento_va_al_costado(self):
        services.marcar_pagado(self.pedido)
        principal, costado = self._columnas()

        self.assertIn("Retiralo en el gimnasio", str(costado))
        self.assertIn("Pedir cancelación", str(costado))
        self.assertNotIn("Pedir cancelación", str(principal))


class MisPedidosTablaTests(_BaseAlumno):
    def _pagado(self):
        pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno,
            carrito={self.variante.pk: 1}, suplantando=False,
        )
        services.marcar_pagado(pedido)
        return pedido

    def test_con_pedidos_una_tabla_sin_columna_de_accion(self):
        pedido = self._pagado()
        main = _main(self.client.get(reverse("tienda:mis_pedidos")))

        self.assertIn("contenido--alumno", _clases(main))
        tabla, = _tag(main, "table")
        self.assertEqual(_clases(tabla), ["tabla", "tabla--pedidos"])
        self.assertEqual(len(_tag(tabla, "th")), 4)
        self.assertEqual(_con_clase(tabla, "celda-accion"), [])
        fila = _tag(_tag(tabla, "tbody")[0], "tr")[0]
        self.assertEqual(len(_tag(fila, "td")), 4)
        self.assertIn(reverse("tienda:mi_pedido", args=[pedido.pk]), str(fila))
        self.assertIn("Volver al inicio", str(main))

    def test_hay_espacio_entre_celdas_para_el_separador_del_celular(self):
        """En el celular el « · » sale de `td + td::before` más el espacio
        entre celdas: `</td><td>` pegados lo dejarían pegado al dato."""
        self._pagado()
        for url in (reverse("tienda:mis_pedidos"),
                    reverse("tienda:mis_pedidos") + "?arrepentimiento=1"):
            html = self.client.get(url).content.decode()
            fila = html[html.index("<tbody>"):html.index("</tbody>")]
            self.assertRegex(fila, r"</td>\s+<td")
            self.assertNotRegex(fila, r"</td><td")

    def test_sin_pedidos_el_aviso_queda_fuera_de_la_tabla(self):
        main = _main(self.client.get(reverse("tienda:mis_pedidos")))

        self.assertEqual(_tag(main, "table"), [])
        self.assertIn("Todavía no hiciste pedidos.", str(main))

    def test_arrepentimiento_un_form_por_fila_en_la_celda_de_accion(self):
        pedido = self._pagado()
        url = reverse("tienda:mis_pedidos") + "?arrepentimiento=1"
        main = _main(self.client.get(url))

        tabla, = _tag(main, "table")
        self.assertEqual(len(_tag(tabla, "th")), 5)
        celda, = _con_clase(tabla, "celda-accion")
        self.assertEqual(celda.name, "td")
        form, = _tag(celda, "form")
        self.assertEqual(
            dict(form.attributes)["action"],
            reverse("tienda:mi_pedido_pedir_cancelacion", args=[pedido.pk]),
        )
        boton, = _tag(form, "button")
        self.assertEqual(
            sorted(_clases(boton)),
            sorted(["boton", "w-full", "mt-2", "lg:w-auto", "lg:mt-0"]),
        )
        # Número sin link y estado como texto, como antes.
        self.assertEqual(_tag(tabla, "a"), [])
        self.assertIn(f"#{pedido.pk}", str(_tag(tabla, "strong")[0]))
        self.assertEqual(_con_clase(tabla, "badge"), [])

    def test_arrepentimiento_sin_pedidos_cancelables(self):
        url = reverse("tienda:mis_pedidos") + "?arrepentimiento=1"
        main = _main(self.client.get(url))

        self.assertEqual(_tag(main, "table"), [])
        self.assertIn("No tenés pedidos que se puedan cancelar ahora.", str(main))
        self.assertIn("Ver todos mis pedidos", str(main))
