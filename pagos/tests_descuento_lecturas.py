"""Descuento en la cuota: las lecturas de plata usan el neto (ingresos del
panel, avisos de cuota, exportación)."""

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

from django.test import TestCase

from alumnos.models import Alumno
from notificaciones.services import _con_mercadopago
from pagos.models import Cuota
from tenants import exportacion
from tenants.analitica import ingresos_por_mes
from tenants.models import Gimnasio


def _cuota(gimnasio, alumno, monto, descuento="0", estado=Cuota.Estado.PAGADO,
           inicio=date(2026, 3, 1), motivo=""):
    return Cuota.objects.create(
        gimnasio=gimnasio, alumno=alumno, periodo_inicio=inicio,
        periodo_fin=date(2026, 3, 28), monto=Decimal(monto),
        descuento=Decimal(descuento), descuento_motivo=motivo, estado=estado,
    )


class LecturasConDescuentoTests(TestCase):
    def setUp(self):
        self.g = Gimnasio.objects.create(nombre="G", slug="g")
        self.a1 = Alumno.objects.create(gimnasio=self.g, nombre="A", apellido="Uno")
        self.a2 = Alumno.objects.create(gimnasio=self.g, nombre="B", apellido="Dos")

    def _marzo(self):
        filas = ingresos_por_mes(self.g, hoy=date(2026, 3, 15), meses=3)
        return next(f["total"] for f in filas if f["mes"] == date(2026, 3, 1))

    def test_ingresos_suman_el_neto(self):
        _cuota(self.g, self.a1, "10000", "2000")
        self.assertEqual(self._marzo(), 8000.0)

    def test_ingresos_sin_descuento_como_antes(self):
        _cuota(self.g, self.a1, "10000")
        _cuota(self.g, self.a2, "5000", inicio=date(2026, 3, 2))
        self.assertEqual(self._marzo(), 15000.0)

    def test_aviso_usa_el_neto(self):
        cuota = _cuota(self.g, self.a1, "10000", "2000", estado=Cuota.Estado.PENDIENTE)
        texto = _con_mercadopago(cuota, True)
        self.assertIn("$ 8.000", texto)
        self.assertNotIn("10.000", texto)
        self.assertIn("podés pagarla con Mercado Pago", texto)

    def test_aviso_sin_descuento_igual_que_antes(self):
        cuota = _cuota(self.g, self.a1, "10000", estado=Cuota.Estado.PENDIENTE)
        self.assertIn("$ 10.000", _con_mercadopago(cuota, True))

    def test_aviso_no_ofrece_mp_si_el_neto_es_cero(self):
        # En memoria: la constraint de la base no permite neto 0.
        cuota = Cuota(monto=Decimal("1000"), descuento=Decimal("1000"))
        self.assertEqual(_con_mercadopago(cuota, True), "")

    def test_exportacion_trae_las_dos_columnas_al_final(self):
        _cuota(self.g, self.a1, "10000", "2000", motivo="Premio")
        hoja = next(h for h in exportacion.HOJAS if h.nombre == "cuotas")
        self.assertEqual(
            [c for c, _ in hoja.columnas][-2:], ["descuento", "descuento_motivo"]
        )
        destino = io.BytesIO()
        exportacion.exportar_gimnasio(gimnasio=self.g, destino=destino)
        zf = zipfile.ZipFile(destino)
        ruta = next(n for n in zf.namelist() if n.endswith("/cuotas.csv") and "excel" not in n.lower())
        filas = list(csv.DictReader(io.StringIO(zf.read(ruta).decode("utf-8-sig"))))
        self.assertEqual(list(filas[0].keys())[-2:], ["descuento", "descuento_motivo"])
        self.assertEqual(Decimal(filas[0]["descuento"]), Decimal("2000"))
        self.assertEqual(filas[0]["descuento_motivo"], "Premio")
