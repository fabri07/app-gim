"""Indicadores de ventas de la tienda (molde: los de `tenants/analitica.py`)."""

from datetime import date, datetime
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from alumnos.models import Alumno
from tenants.models import Gimnasio
from tienda import analitica
from tienda.models import ItemPedido, Pedido, Producto, Variante
from tienda.testing import crear_producto

HOY = date(2026, 10, 31)


def aware(*args):
    return timezone.make_aware(datetime(*args))


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G")
        self.remera = crear_producto(gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"))
        self.proteina = crear_producto(gimnasio=self.gimnasio, nombre="Proteína", precio=Decimal("30000"))
        self.v_remera = Variante.objects.create(producto=self.remera, nombre="M", stock=50)
        self.v_proteina = Variante.objects.create(producto=self.proteina, stock=50)

    def venta(self, pagado_en, *, variante=None, cantidad=1, estado=Pedido.Estado.PAGADO,
              gimnasio=None, alumno=None):
        """Pedido armado a mano (sin `crear_pedido`): acá importa la fecha de
        pago, no el flujo."""
        variante = variante or self.v_remera
        precio = variante.producto.precio
        pedido = Pedido.objects.create(
            gimnasio=gimnasio or self.gimnasio, alumno=alumno or self.alumno, estado=estado,
            total=precio * cantidad, reservado_hasta=pagado_en, pagado_en=pagado_en,
        )
        ItemPedido.objects.create(
            pedido=pedido, variante=variante, nombre=str(variante),
            precio_unitario=precio, cantidad=cantidad,
        )
        return pedido


class VentasPorMesTests(_Base):
    def test_una_fila_por_mes_aunque_este_vacio(self):
        filas = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)
        self.assertEqual(len(filas), 12)
        self.assertEqual((filas[0]["mes"], filas[-1]["mes"]), (date(2025, 11, 1), date(2026, 10, 1)))
        self.assertTrue(all(f["total"] == 0 and f["pedidos"] == 0 for f in filas))

    def test_dos_pedidos_del_mismo_mes_suman_en_una_fila(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina, cantidad=2)
        octubre = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]
        self.assertEqual((octubre["total"], octubre["pedidos"]), (75000.0, 2))

    def test_entregado_cuenta_y_devuelto_vencido_o_pendiente_no(self):
        self.venta(aware(2026, 10, 5, 10, 0), estado=Pedido.Estado.ENTREGADO)
        for estado in (Pedido.Estado.DEVUELTO, Pedido.Estado.VENCIDO, Pedido.Estado.CANCELADO,
                       Pedido.Estado.PENDIENTE_PAGO, Pedido.Estado.EN_REVISION):
            self.venta(aware(2026, 10, 5, 10, 0), estado=estado)
        octubre = analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]
        self.assertEqual((octubre["total"], octubre["pedidos"]), (15000.0, 1))

    def test_otro_gimnasio_no_suma(self):
        alumno = Alumno.objects.create(gimnasio=self.otro, nombre="B", apellido="P")
        ajeno = crear_producto(gimnasio=self.otro, nombre="X", precio=Decimal("999"))
        variante = Variante.objects.create(producto=ajeno, stock=5)
        self.venta(aware(2026, 10, 5, 10, 0), variante=variante, gimnasio=self.otro, alumno=alumno)
        self.assertEqual(analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]["pedidos"], 0)
        self.assertEqual(analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY), [])

    def test_pagado_a_las_2230_del_ultimo_dia_cae_en_ese_mes(self):
        """31/10 22:30 en Argentina = 1/11 01:30 UTC. Agrupado en UTC caería
        en noviembre, fuera de la ventana que termina en octubre."""
        self.venta(aware(2026, 10, 31, 22, 30))
        self.assertEqual(analitica.ventas_por_mes(self.gimnasio, hoy=HOY)[-1]["pedidos"], 1)

    def test_el_costo_no_crece_con_la_cantidad_de_pedidos(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 9, 5, 10, 0))
        with CaptureQueriesContext(connection) as pocos:
            analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        for dia in range(1, 21):
            self.venta(aware(2026, 9, dia, 11, 0), variante=self.v_proteina)
            self.venta(aware(2026, 10, dia, 11, 0))
        with CaptureQueriesContext(connection) as muchos:
            panel = analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        self.assertEqual(len(pocos), len(muchos))
        self.assertEqual(len(muchos), 2)
        self.assertEqual(panel["mes_anterior"]["pedidos"], 21)


class ProductosMasVendidosTests(_Base):
    def test_ordena_por_unidades_y_respeta_la_ventana(self):
        self.venta(aware(2026, 10, 5, 10, 0), cantidad=1)
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina, cantidad=3)
        self.venta(aware(2026, 6, 1, 10, 0), cantidad=10)  # fuera de los 90 días
        top = analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY)
        self.assertEqual([(p["nombre"], p["unidades"]) for p in top], [("Proteína", 3), ("Remera", 1)])
        self.assertEqual(top[0]["total"], 90000.0)

    def test_las_variantes_de_un_producto_se_suman(self):
        talle_l = Variante.objects.create(producto=self.remera, nombre="L", stock=5)
        self.venta(aware(2026, 10, 5, 10, 0), cantidad=1)
        self.venta(aware(2026, 10, 6, 10, 0), variante=talle_l, cantidad=2)
        top = analitica.productos_mas_vendidos(self.gimnasio, hoy=HOY)
        self.assertEqual([(p["nombre"], p["unidades"]) for p in top], [("Remera", 3)])


class PanelDeVentasTests(_Base):
    def test_ticket_promedio_y_sin_ventas(self):
        vacio = analitica.panel_de_ventas(self.gimnasio, hoy=HOY)
        self.assertFalse(vacio["hay_ventas"])
        self.assertIsNone(vacio["ticket_promedio"])
        self.venta(aware(2026, 10, 5, 10, 0))
        self.venta(aware(2026, 10, 6, 10, 0), variante=self.v_proteina)
        self.assertEqual(analitica.panel_de_ventas(self.gimnasio, hoy=HOY)["ticket_promedio"], 22500.0)

    def test_resumen_del_mes_es_una_query(self):
        self.venta(aware(2026, 10, 5, 10, 0))
        with self.assertNumQueries(1):
            resumen = analitica.resumen_del_mes(self.gimnasio, hoy=HOY)
        self.assertEqual(resumen, {"total": 15000.0, "pedidos": 1})
