"""
Tests de Fase 1 para `PagoMensual`: creación básica, unicidad por
(gimnasio, alumno, mes, año), autogeneración mensual, vencimiento de
pendientes atrasados y aislamiento por tenant.

Sigue el mismo criterio que `tenants/tests.py`: `django.test.TestCase` plano,
sin pytest ni factories (el proyecto es chico, KISS/YAGNI).
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from alumnos.models import Alumno
from pagos.models import PagoMensual, generar_pagos_pendientes, marcar_vencidos
from tenants.models import Gimnasio


class PagoMensualModelTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_crea_pago_y_str(self):
        pago = PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        self.assertEqual(pago.estado, PagoMensual.Estado.PENDIENTE)
        self.assertEqual(str(pago), "Perez, Juan - 03/2026")

    def test_unique_together_gimnasio_alumno_mes_anio(self):
        PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagoMensual.objects.create(
                    gimnasio=self.gimnasio,
                    alumno=self.alumno,
                    mes=3,
                    anio=2026,
                    monto=Decimal("20000.00"),
                )

    def test_for_gimnasio_aisla_por_tenant(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        otro_alumno = Alumno.objects.create(
            gimnasio=otro_gimnasio, nombre="Ana", apellido="Gomez"
        )
        pago_propio = PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )
        PagoMensual.objects.create(
            gimnasio=otro_gimnasio,
            alumno=otro_alumno,
            mes=3,
            anio=2026,
            monto=Decimal("15000.00"),
        )

        pagos_del_gimnasio = PagoMensual.objects.for_gimnasio(self.gimnasio)

        self.assertEqual(list(pagos_del_gimnasio), [pago_propio])


class GenerarPagosPendientesTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.activo_1 = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Juan",
            apellido="Perez",
            estado=Alumno.Estado.ACTIVO,
        )
        self.activo_2 = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Ana",
            apellido="Gomez",
            estado=Alumno.Estado.ACTIVO,
        )
        self.inactivo = Alumno.objects.create(
            gimnasio=self.gimnasio,
            nombre="Luis",
            apellido="Diaz",
            estado=Alumno.Estado.INACTIVO,
        )

        self.otro_gimnasio = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        Alumno.objects.create(
            gimnasio=self.otro_gimnasio,
            nombre="Pedro",
            apellido="Ruiz",
            estado=Alumno.Estado.ACTIVO,
        )

    def test_genera_solo_para_alumnos_activos_del_gimnasio_correspondiente(self):
        creados = generar_pagos_pendientes(mes=7, anio=2026)

        self.assertEqual(creados, 3)
        self.assertEqual(
            PagoMensual.objects.filter(gimnasio=self.gimnasio, mes=7, anio=2026).count(),
            2,
        )
        self.assertFalse(
            PagoMensual.objects.filter(alumno=self.inactivo, mes=7, anio=2026).exists()
        )

    def test_es_idempotente(self):
        generar_pagos_pendientes(mes=7, anio=2026)
        creados_segunda_vez = generar_pagos_pendientes(mes=7, anio=2026)

        self.assertEqual(creados_segunda_vez, 0)
        self.assertEqual(PagoMensual.objects.filter(mes=7, anio=2026).count(), 3)


class MarcarVencidosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def _crear_pendiente(self, mes, anio):
        return PagoMensual.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            mes=mes,
            anio=anio,
            monto=Decimal("15000.00"),
        )

    def test_pendiente_de_mes_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=5, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, PagoMensual.Estado.VENCIDO)

    def test_pendiente_de_mes_actual_o_futuro_no_cambia(self):
        pago_actual = self._crear_pendiente(mes=7, anio=2026)
        pago_futuro = self._crear_pendiente(mes=8, anio=2026)

        actualizados = marcar_vencidos(mes=7, anio=2026)

        pago_actual.refresh_from_db()
        pago_futuro.refresh_from_db()
        self.assertEqual(actualizados, 0)
        self.assertEqual(pago_actual.estado, PagoMensual.Estado.PENDIENTE)
        self.assertEqual(pago_futuro.estado, PagoMensual.Estado.PENDIENTE)

    def test_pendiente_de_anio_pasado_pasa_a_vencido(self):
        pago_pasado = self._crear_pendiente(mes=12, anio=2025)

        actualizados = marcar_vencidos(mes=1, anio=2026)

        pago_pasado.refresh_from_db()
        self.assertEqual(actualizados, 1)
        self.assertEqual(pago_pasado.estado, PagoMensual.Estado.VENCIDO)
