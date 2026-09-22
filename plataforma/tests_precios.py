"""
Tests del módulo de precios de la plataforma.

`SimpleTestCase` a propósito: `plataforma.precios` es Django-free (no toca el
ORM ni `timezone`), igual que `pagos.models.ciclo_vigente` o
`rutinas.agrupacion`. El riesgo real que cubren estos tests es que la regla de
cobro se corra un día para un lado o para el otro, y eso se fija con fechas
literales, sin base de datos de por medio.
"""

from datetime import date, timedelta

from django.test import SimpleTestCase

from plataforma import precios
from plataforma.precios import EstadoPago


class EscalonesDePrecioTests(SimpleTestCase):
    """Los bordes de los escalones son la parte que se discute con el usuario:
    100 alumnos exactos paga 10 y 300 exactos paga 15 (decisión cerrada). Un
    `<` en vez de un `<=` mueve la factura de un cliente real."""

    def test_hasta_cien_alumnos_paga_diez(self):
        self.assertEqual(precios.precio_usd(0), 10)
        self.assertEqual(precios.precio_usd(1), 10)
        self.assertEqual(precios.precio_usd(100), 10)

    def test_ciento_uno_ya_paga_quince(self):
        self.assertEqual(precios.precio_usd(101), 15)

    def test_trescientos_exactos_todavia_paga_quince(self):
        self.assertEqual(precios.precio_usd(300), 15)

    def test_trescientos_uno_paga_veinte(self):
        self.assertEqual(precios.precio_usd(301), 20)
        self.assertEqual(precios.precio_usd(5000), 20)

    def test_escalon_de_devuelve_tope_y_precio(self):
        self.assertEqual(precios.escalon_de(100), (100, 10))
        self.assertEqual(precios.escalon_de(150), (300, 15))
        self.assertEqual(precios.escalon_de(500), (None, 20))


class FinDePruebaTests(SimpleTestCase):
    def test_la_prueba_dura_treinta_dias_y_el_primer_cobro_vence_el_dia_treinta(self):
        inicio = date(2026, 1, 1)

        self.assertEqual(precios.fin_de_prueba(inicio), date(2026, 1, 31))
        self.assertEqual(
            (precios.fin_de_prueba(inicio) - inicio).days, precios.DIAS_GRATIS
        )


class PeriodoSiguienteTests(SimpleTestCase):
    """`hasta` es INCLUSIVO, igual que `Cuota.periodo_fin`: un período de 30
    días que arranca el 31/1 termina el 1/3, no el 2/3."""

    INICIO = date(2026, 1, 1)

    def test_sin_nada_cubierto_el_primer_periodo_arranca_al_terminar_la_prueba(self):
        desde, hasta = precios.periodo_siguiente(self.INICIO, None)

        self.assertEqual(desde, date(2026, 1, 31))
        self.assertEqual(hasta, date(2026, 3, 1))
        self.assertEqual((hasta - desde).days, precios.DIAS_CICLO - 1)

    def test_con_un_periodo_cubierto_el_siguiente_arranca_al_dia_siguiente(self):
        desde, hasta = precios.periodo_siguiente(self.INICIO, date(2026, 3, 1))

        self.assertEqual(desde, date(2026, 3, 2))
        self.assertEqual((hasta - desde).days, precios.DIAS_CICLO - 1)


class ProximoVencimientoTests(SimpleTestCase):
    INICIO = date(2026, 1, 1)

    def test_sin_pagos_vence_al_terminar_la_prueba(self):
        vencimiento = precios.proximo_vencimiento(
            self.INICIO, self.INICIO + timedelta(days=5), None
        )

        self.assertEqual(vencimiento, precios.fin_de_prueba(self.INICIO))

    def test_con_un_periodo_cubierto_vence_al_dia_siguiente_del_cubierto(self):
        vencimiento = precios.proximo_vencimiento(
            self.INICIO, self.INICIO + timedelta(days=40), date(2026, 3, 1)
        )

        self.assertEqual(vencimiento, date(2026, 3, 2))

    def test_una_facturacion_que_todavia_no_arranco_vence_al_fin_de_la_prueba(self):
        """Con el arranque a futuro el vencimiento es el fin de la prueba, no
        `None`: un guion en la columna de vencimiento se lee como un dato roto,
        y la prueba de ese gimnasio ya tiene fecha de fin conocida."""
        vencimiento = precios.proximo_vencimiento(
            self.INICIO, self.INICIO - timedelta(days=1), None
        )

        self.assertEqual(vencimiento, precios.fin_de_prueba(self.INICIO))

    def test_una_facturacion_que_todavia_no_arranco_sigue_en_prueba(self):
        """Lo que NO puede pasar es que mostrar la fecha lo empuje a
        POR_VENCER: el fin de la prueba está siempre a más de
        `DIAS_AVISO_COBRO` días de un `hoy` anterior al arranque."""
        estado = precios.estado_pago(
            self.INICIO, self.INICIO - timedelta(days=1), None
        )

        self.assertIs(estado, EstadoPago.PRUEBA)

    def test_una_facturacion_que_todavia_no_arranco_no_tiene_atraso(self):
        self.assertEqual(
            precios.dias_de_atraso(
                self.INICIO, self.INICIO - timedelta(days=1), None
            ),
            0,
        )


class EstadoPagoTests(SimpleTestCase):
    """Los días se cuentan desde el alta: día 0 es el alta y el primer cobro
    vence el día 30. Con `DIAS_AVISO_COBRO = 7`, el día 22 todavía faltan 8
    días y sigue en PRUEBA; el aviso arranca el día 23."""

    INICIO = date(2026, 1, 1)

    def _estado(self, dias, cubierto_hasta=None, exenta=False):
        return precios.estado_pago(
            self.INICIO,
            self.INICIO + timedelta(days=dias),
            cubierto_hasta,
            exenta=exenta,
        )

    def test_el_dia_del_alta_esta_en_prueba(self):
        self.assertIs(self._estado(0), EstadoPago.PRUEBA)

    def test_el_dia_veintidos_sigue_en_prueba(self):
        self.assertIs(self._estado(22), EstadoPago.PRUEBA)

    def test_el_dia_veintitres_ya_avisa_que_esta_por_vencer(self):
        self.assertIs(self._estado(23), EstadoPago.POR_VENCER)

    def test_el_dia_del_vencimiento_todavia_es_por_vencer_no_vencida(self):
        self.assertIs(self._estado(30), EstadoPago.POR_VENCER)

    def test_el_dia_siguiente_al_vencimiento_esta_vencida(self):
        self.assertIs(self._estado(31), EstadoPago.VENCIDA)

    def test_con_el_periodo_pago_esta_al_dia(self):
        # Cubierto hasta el 1/3 (primer período pago), mirado el día 35.
        self.assertIs(
            self._estado(35, cubierto_hasta=date(2026, 3, 1)), EstadoPago.AL_DIA
        )

    def test_cinco_dias_antes_del_vencimiento_avisa(self):
        # Vence el 2/3; cinco días antes es el 25/2, o sea el día 55.
        self.assertIs(
            self._estado(55, cubierto_hasta=date(2026, 3, 1)), EstadoPago.POR_VENCER
        )

    def test_un_gimnasio_exento_nunca_vence(self):
        """La demo y los gimnasios exentos no se cobran: sin este corte, la
        cuenta de demostración aparecería como el gimnasio más atrasado de
        todos, arriba de «Para cobrar esta semana»."""
        self.assertIs(self._estado(300, exenta=True), EstadoPago.EXENTA)


class DiasDeAtrasoTests(SimpleTestCase):
    INICIO = date(2026, 1, 1)

    def _atraso(self, dias, cubierto_hasta=None):
        return precios.dias_de_atraso(
            self.INICIO, self.INICIO + timedelta(days=dias), cubierto_hasta
        )

    def test_el_dia_del_vencimiento_no_hay_atraso(self):
        self.assertEqual(self._atraso(30), 0)

    def test_al_dia_siguiente_hay_un_dia_de_atraso(self):
        self.assertEqual(self._atraso(31), 1)

    def test_un_gimnasio_al_dia_no_tiene_atraso(self):
        self.assertEqual(self._atraso(35, cubierto_hasta=date(2026, 3, 1)), 0)
