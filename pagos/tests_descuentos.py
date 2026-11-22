"""Descuento en la cuota (pieza 3 de reconocimientos): modelo y constraint."""

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from alumnos.models import Alumno
from pagos.models import Cuota
from tenants.models import Gimnasio


def _cuota(gimnasio, alumno, monto, descuento=None, dia=1):
    datos = dict(
        gimnasio=gimnasio,
        alumno=alumno,
        periodo_inicio=date(2026, 1, dia),
        periodo_fin=date(2026, 1, dia + 27 if dia + 27 <= 31 else 31),
        monto=Decimal(monto),
    )
    if descuento is not None:
        datos["descuento"] = Decimal(descuento)
    return Cuota.objects.create(**datos)


class CuotaDescuentoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="A", apellido="B")

    def test_neto_es_monto_menos_descuento(self):
        cuota = _cuota(self.gimnasio, self.alumno, "10000.00", "1500.00")
        self.assertEqual(cuota.monto_a_cobrar, Decimal("8500.00"))

    def test_sin_descuento_el_neto_es_el_monto(self):
        cuota = _cuota(self.gimnasio, self.alumno, "10000.00")
        self.assertEqual(cuota.descuento, Decimal("0"))
        self.assertEqual(cuota.descuento_motivo, "")
        self.assertEqual(cuota.monto_a_cobrar, Decimal("10000.00"))

    def test_neto_en_queryset(self):
        _cuota(self.gimnasio, self.alumno, "10000.00", "1000.00")
        neto = Cuota.objects.annotate(n=Cuota.NETO).get().n
        self.assertEqual(neto, Decimal("9000.00"))

    def _rechaza(self, monto, descuento):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _cuota(self.gimnasio, self.alumno, monto, descuento)

    def test_rechaza_descuento_negativo(self):
        self._rechaza("10000.00", "-1.00")

    def test_rechaza_mas_del_90_por_ciento(self):
        self._rechaza("10000.00", "9000.01")

    def test_rechaza_descuento_con_monto_cero(self):
        self._rechaza("0.00", "1.00")

    def test_acepta_exactamente_90_por_ciento(self):
        cuota = _cuota(self.gimnasio, self.alumno, "10000.00", "9000.00")
        self.assertEqual(cuota.monto_a_cobrar, Decimal("1000.00"))

    def test_acepta_descuento_cero_con_monto_negativo_dato_viejo(self):
        cuota = _cuota(self.gimnasio, self.alumno, "-5.00", "0.00")
        self.assertEqual(cuota.descuento, Decimal("0"))


class MigracionDeDescuentoTests(TransactionTestCase):
    """Una cuota que ya existía queda con descuento 0 al migrar (db_default)."""

    def _migrar(self, destino):
        # Las otras apps quedan en su última migración: sólo `pagos` retrocede
        # (si no, el estado de `alumnos` no coincide con las columnas reales).
        hojas = MigrationExecutor(connection).loader.graph.leaf_nodes()
        destino = list(destino) + [n for n in hojas if n[0] in ("alumnos", "tenants")]
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_cuota_sin_la_columna_nueva_queda_con_descuento_cero(self):
        apps_viejas = self._migrar([("pagos", "0007_cobroonlinecuota")])
        gimnasio = apps_viejas.get_model("tenants", "Gimnasio").objects.create(nombre="M", slug="m")
        alumno = apps_viejas.get_model("alumnos", "Alumno").objects.create(
            gimnasio=gimnasio, nombre="A", apellido="B"
        )
        apps_viejas.get_model("pagos", "Cuota").objects.create(
            gimnasio=gimnasio, alumno=alumno, periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 28), monto=Decimal("-3.00"),
        )

        self._migrar(self._hoja())

        with connection.cursor() as cursor:
            cursor.execute("SELECT descuento, descuento_motivo FROM pagos_cuota")
            filas = cursor.fetchall()
        self.assertEqual(len(filas), 1)
        self.assertEqual(Decimal(str(filas[0][0])), Decimal("0"))
        self.assertEqual(filas[0][1], "")

    def test_insert_crudo_sin_la_columna_usa_el_default_de_la_base(self):
        gimnasio = Gimnasio.objects.create(nombre="M", slug="m")
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="A", apellido="B")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO pagos_cuota (creado, modificado, gimnasio_id, alumno_id, "
                "periodo_inicio, periodo_fin, monto, estado, medio_pago_texto, comprobante, observaciones) "
                "VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, %s, '2026-01-01', '2026-01-28', 100, "
                "'pendiente', '', '', '')",
                [gimnasio.pk, alumno.pk],
            )
        cuota = Cuota.objects.get()
        self.assertEqual(cuota.descuento, Decimal("0"))
        self.assertEqual(cuota.descuento_motivo, "")

    def test_se_puede_revertir_y_reaplicar(self):
        self._migrar([("pagos", "0007_cobroonlinecuota")])
        self._migrar(self._hoja())

    def _hoja(self):
        return [n for n in MigrationExecutor(connection).loader.graph.leaf_nodes() if n[0] == "pagos"]


from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402

from pagos import cobro  # noqa: E402
from pagos.descuentos import aplicar_descuento, descuento_maximo  # noqa: E402
from pagos.models import CobroOnlineCuota  # noqa: E402
from pagos.precios import aplicar_precio_a_impagas_sin_monto  # noqa: E402


class ServicioDescuentoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")
        self.otro = Gimnasio.objects.create(nombre="O", slug="o")
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="A", apellido="B")
        self.cuota = _cuota(self.gimnasio, self.alumno, "10000.00")

    def _recargar(self):
        self.cuota.refresh_from_db()
        return self.cuota

    def _link(self):
        return CobroOnlineCuota.objects.create(
            gimnasio=self.gimnasio, cuota=self.cuota, monto=Decimal("10000"),
            mp_user_id="1", expira=timezone.now() + timedelta(hours=2),
        )

    def test_maximo_redondea_hacia_abajo(self):
        self.assertEqual(descuento_maximo(Decimal("100.05")), Decimal("90.04"))
        self.assertEqual(descuento_maximo(Decimal("10000")), Decimal("9000.00"))

    def test_aplica_sobre_impaga_y_cancela_el_link_creado(self):
        link = self._link()
        self.assertTrue(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("1500"), "Premio"))
        cuota = self._recargar()
        self.assertEqual(cuota.descuento, Decimal("1500.00"))
        self.assertEqual(cuota.descuento_motivo, "Premio")
        self.assertEqual(cuota.monto_a_cobrar, Decimal("8500.00"))
        link.refresh_from_db()
        self.assertEqual(link.estado, CobroOnlineCuota.Estado.CANCELADO)

    def test_pagada_y_anulada_no_se_tocan(self):
        for estado in (Cuota.Estado.PAGADO, Cuota.Estado.ANULADO):
            Cuota.objects.filter(pk=self.cuota.pk).update(estado=estado)
            self.assertFalse(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("100"), "x"))
            self.assertEqual(self._recargar().descuento, Decimal("0"))

    def test_otro_gimnasio_no(self):
        self.assertFalse(aplicar_descuento(self.cuota.pk, self.otro, Decimal("100"), "x"))
        self.assertEqual(self._recargar().descuento, Decimal("0"))

    def test_tope_del_90(self):
        self.assertFalse(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("9000.01"), "x"))
        self.assertEqual(self._recargar().descuento, Decimal("0"))
        self.assertTrue(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("9000.00"), "x"))

    def test_negativo_falla(self):
        self.assertFalse(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("-1"), "x"))

    def test_quitar_con_cero(self):
        aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("500"), "x")
        self.assertTrue(aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("0"), ""))
        cuota = self._recargar()
        self.assertEqual(cuota.monto_a_cobrar, cuota.monto)
        self.assertEqual(cuota.descuento_motivo, "")

    def test_corregir_monto_clasico_intacto(self):
        self.assertTrue(cobro.corregir_monto(self.cuota.pk, self.gimnasio, Decimal("12000")))
        cuota = self._recargar()
        self.assertEqual(cuota.monto, Decimal("12000"))
        self.assertEqual(cuota.descuento, Decimal("0"))

    def test_corregir_monto_bajo_el_descuento_vigente_falla_sin_escribir(self):
        aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("9000"), "x")
        self.assertFalse(cobro.corregir_monto(self.cuota.pk, self.gimnasio, Decimal("5000")))
        self.assertEqual(self._recargar().monto, Decimal("10000.00"))

    def test_corregir_monto_conserva_descuento_si_entra_en_el_tope(self):
        aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("1000"), "x")
        self.assertTrue(cobro.corregir_monto(self.cuota.pk, self.gimnasio, Decimal("5000")))
        cuota = self._recargar()
        self.assertEqual((cuota.monto, cuota.descuento), (Decimal("5000"), Decimal("1000")))

    def test_corregir_monto_con_descuento_nuevo(self):
        self.assertTrue(cobro.corregir_monto(
            self.cuota.pk, self.gimnasio, Decimal("5000"), descuento=Decimal("500"), motivo="m"))
        cuota = self._recargar()
        self.assertEqual((cuota.descuento, cuota.descuento_motivo), (Decimal("500"), "m"))
        self.assertFalse(cobro.corregir_monto(
            self.cuota.pk, self.gimnasio, Decimal("5000"), descuento=Decimal("4500.01")))
        self.assertEqual(self._recargar().descuento, Decimal("500"))

    def test_precio_a_impagas_sin_monto_no_toca_descuento(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=0)
        self.gimnasio.precio_cuota = Decimal("8000")
        self.gimnasio.save()
        self.assertEqual(aplicar_precio_a_impagas_sin_monto(self.gimnasio), 1)
        cuota = self._recargar()
        self.assertEqual((cuota.monto, cuota.descuento), (Decimal("8000.00"), Decimal("0")))
