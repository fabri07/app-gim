"""
El add-on de la tienda en el precio y en el panel de plataforma (Hito 3).

Se cobra la tienda si `gimnasio.tienda_habilitada` (no `puede_usar_tienda`, que
es verdadera en la demo) y sólo donde ya se cobra (`facturacion_aplica`).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import re

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from alumnos.models import Alumno
from plataforma import facturacion, precios
from plataforma.models import PagoPlataforma
from plataforma.tests_facturacion import HOY, _gimnasio, _pago, _superadmin
from tenants.models import Gimnasio


class TiendaEnElPrecioTests(SimpleTestCase):
    """El add-on suma USD 20 por mes; el anual son 10 meses."""

    def test_el_precio_de_la_tienda_es_veinte_o_cero(self):
        self.assertEqual(precios.PRECIO_TIENDA_USD, 20)
        self.assertEqual(precios.precio_tienda_usd(True), 20)
        self.assertEqual(precios.precio_tienda_usd(False), 0)

    def test_el_anual_de_la_tienda_son_diez_meses(self):
        self.assertEqual(precios.precio_tienda_usd_anual(True), 200)
        self.assertEqual(precios.precio_tienda_usd_anual(False), 0)

    def test_mensual_con_tienda_suma_al_tramo(self):
        self.assertEqual(precios.precio_usd(50, tienda=True), 35)
        self.assertEqual(precios.precio_usd(120, tienda=True), 40)
        self.assertEqual(precios.precio_usd(400, tienda=True), 45)

    def test_anual_con_tienda_congela_tramo_y_tienda(self):
        self.assertEqual(precios.precio_usd_anual(120, tienda=True), 400)
        self.assertEqual(precios.precio_usd_anual_sin_descuento(120, tienda=True), 480)
        self.assertEqual(precios.precio_usd_anual_sin_descuento(50, tienda=True), 420)

    def test_sin_tienda_devuelve_lo_de_siempre(self):
        for n in (0, 100, 101, 300, 301):
            self.assertEqual(precios.precio_usd(n, tienda=False), precios.precio_usd(n))
            self.assertEqual(precios.precio_usd_anual(n), precios.precio_usd(n) * 10)
            self.assertEqual(
                precios.precio_usd_anual_sin_descuento(n), precios.precio_usd(n) * 12
            )

    def test_tienda_es_solo_por_keyword(self):
        with self.assertRaises(TypeError):
            precios.precio_usd(50, True)
        with self.assertRaises(TypeError):
            precios.precio_usd_anual(50, True)
        with self.assertRaises(TypeError):
            precios.precio_usd_anual_sin_descuento(50, True)


class TiendaEnElMonitorTests(TestCase):
    def _con_alumnos(self, gimnasio, n):
        for i in range(n):
            Alumno.objects.create(gimnasio=gimnasio, nombre=f"A{i}", apellido="X")

    def test_sin_tienda_la_fila_y_los_kpis_son_los_de_siempre(self):
        gim = _gimnasio("Sin Tienda", 45)
        self._con_alumnos(gim, 120)
        filas = facturacion.filas_del_monitor(hoy=HOY)

        self.assertEqual(filas[0].precio_usd, 20)
        self.assertEqual(filas[0].tienda_usd, 0)
        self.assertEqual(facturacion.kpis(filas)["ingreso_mensual_usd"], 20)

    def test_con_tienda_la_fila_trae_el_total_y_la_parte_de_la_tienda(self):
        gim = _gimnasio("Con Tienda", 45, tienda_habilitada=True)
        self._con_alumnos(gim, 120)
        filas = facturacion.filas_del_monitor(hoy=HOY)

        self.assertEqual(filas[0].precio_usd, 40)
        self.assertEqual(filas[0].tienda_usd, 20)
        self.assertEqual(facturacion.kpis(filas)["ingreso_mensual_usd"], 40)

    def test_la_demo_con_tienda_prendida_igual_no_factura(self):
        _gimnasio("Demo", 45, es_demo=True, tienda_habilitada=True)
        filas = facturacion.filas_del_monitor(hoy=HOY)

        self.assertFalse(filas[0].factura)
        kpis = facturacion.kpis(filas)
        self.assertEqual(kpis["ingreso_mensual_usd"], 0)
        self.assertEqual(kpis["clientes"], 0)

    def test_el_monitor_marca_con_tienda_solo_donde_corresponde(self):
        _gimnasio("Alfa", 45, tienda_habilitada=True)
        _gimnasio("Beta", 45)
        self.client.force_login(_superadmin())
        html = self.client.get(reverse("plataforma:inicio")).content.decode()

        # En la tabla y en «Para cobrar» (a los 45 días ya está vencido): la
        # etiqueta va en las filas de Alfa y en ninguna de Beta.
        def bloques(etiqueta, nombre):
            return [
                b for b in re.findall(rf"<{etiqueta}>.*?</{etiqueta}>", html, re.S)
                if f">{nombre}<" in b
            ]

        for etiqueta in ("tr", "li"):
            (alfa,) = bloques(etiqueta, "Alfa")
            (beta,) = bloques(etiqueta, "Beta")
            self.assertIn("con tienda", alfa, etiqueta)
            self.assertNotIn("con tienda", beta, etiqueta)


class PagoManualConTiendaTests(TestCase):
    def setUp(self):
        self.gimnasio = _gimnasio("Vida Plena", 45)
        self.jefe = _superadmin()
        self.client.force_login(self.jefe)
        for i in range(120):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{i}", apellido="Activo"
            )
        self.url = reverse("plataforma:pago_nuevo", args=[self.gimnasio.pk])

    def _datos(self, **extra):
        datos = {
            "fecha_pago": "2026-06-01",
            "monto_usd": "35.00",
            "alumnos_activos": "120",
            "periodo_desde": "2026-05-17",
            "periodo_hasta": "2026-06-15",
            "notas": "",
        }
        datos.update(extra)
        return datos

    def _inicial(self):
        with patch("plataforma.cambio.cotizacion_dolar", return_value=None):
            return self.client.get(self.url).context["form"].initial

    def test_sin_tienda_el_precargado_es_el_de_siempre(self):
        inicial = self._inicial()
        self.assertEqual(inicial["monto_usd"], 20)
        self.assertEqual(inicial["monto_tienda_usd"], 0)

    def test_con_tienda_precarga_el_total_y_la_parte_de_la_tienda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=True)
        inicial = self._inicial()
        self.assertEqual(inicial["monto_usd"], 40)
        self.assertEqual(inicial["monto_tienda_usd"], 20)

    def test_un_gimnasio_exento_no_precarga_tienda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            tienda_habilitada=True, facturacion_exenta=True
        )
        self.assertEqual(self._inicial()["monto_tienda_usd"], 0)

    def test_un_post_sin_el_campo_guarda_cero(self):
        self.client.post(self.url, self._datos())
        self.assertEqual(PagoPlataforma.objects.get().monto_tienda_usd, 0)

    def test_guarda_la_parte_de_la_tienda(self):
        self.client.post(self.url, self._datos(monto_tienda_usd="20.00"))
        self.assertEqual(
            PagoPlataforma.objects.get().monto_tienda_usd, Decimal("20.00")
        )

    def test_tienda_mayor_al_monto_se_rechaza_con_error_visible(self):
        respuesta = self.client.post(self.url, self._datos(monto_tienda_usd="40"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(PagoPlataforma.objects.exists())
        self.assertIn("monto_tienda_usd", respuesta.context["form"].errors)
        self.assertContains(respuesta, "config-error")

    def test_tienda_negativa_se_rechaza_con_un_solo_mensaje(self):
        respuesta = self.client.post(self.url, self._datos(monto_tienda_usd="-5"))
        self.assertFalse(PagoPlataforma.objects.exists())
        self.assertEqual(len(respuesta.context["form"].errors["monto_tienda_usd"]), 1)

    def test_el_historial_marca_incl_tienda(self):
        _pago(
            self.gimnasio, self.jefe, date(2026, 5, 17), date(2026, 6, 15),
            monto_usd=Decimal("35.00"), monto_tienda_usd=Decimal("20.00"),
        )
        ficha = self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        )
        self.assertContains(ficha, "incl. tienda")

    def test_la_ficha_desglosa_tramo_y_tienda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=True)
        ficha = self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        )
        self.assertContains(ficha, "USD 40")
        self.assertContains(ficha, "USD 20 del tramo + USD 20 de la tienda")

    def test_la_ficha_sin_tienda_no_desglosa(self):
        ficha = self.client.get(
            reverse("plataforma:gimnasio_detalle", args=[self.gimnasio.pk])
        )
        self.assertContains(ficha, "USD 20")
        self.assertNotContains(ficha, "del tramo +")
        self.assertNotContains(ficha, "de la tienda)")


class InsertDelCodigoViejoTests(TestCase):
    """Durante el `migrate` del `buildCommand` (y tras un rollback) el código
    viejo sigue insertando pagos sin nombrar `monto_tienda_usd`: la columna
    necesita `db_default` o el INSERT da IntegrityError (NOT NULL)."""

    def test_un_insert_sin_monto_tienda_usd_guarda_cero(self):
        gimnasio = _gimnasio("Viejo", 45)
        jefe = _superadmin()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO plataforma_pagoplataforma (creado, modificado, fecha_pago, "
                "monto_usd, alumnos_activos, periodo_desde, periodo_hasta, notas, "
                "gimnasio_id, registrado_por_id, origen) VALUES (CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, '2026-06-01', 15, 5, '2026-06-01', '2026-06-30', "
                "'', %s, %s, 'manual')",
                [gimnasio.pk, jefe.pk],
            )
        self.assertEqual(PagoPlataforma.objects.get().monto_tienda_usd, 0)
