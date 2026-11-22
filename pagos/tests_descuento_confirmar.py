"""El cobro manual respeta el tope del descuento (fix de la pieza 3).

`ConfirmarPagoForm.clean_monto` suma el descuento al cobrado; sin validar el
tope del 90% la base rechazaba con `IntegrityError` (500) al confirmar con un
monto bajo sobre una cuota con descuento.
"""

from decimal import Decimal

from django.urls import reverse

from pagos.models import Cuota
from pagos.tests_cobro import _Base

C = Cuota.Estado


class ConfirmarConDescuentoTests(_Base):
    def setUp(self):
        super().setUp()
        self._login_staff()
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("10000"), descuento=Decimal("2000"), descuento_motivo="Premio"
        )
        self.url = reverse("pagos:confirmar", args=[self.cuota.pk])

    def post(self, monto, **extra):
        datos = {
            "monto": monto, "fecha_pago": "2026-03-05",
            "medio_pago_texto": "Efectivo", "comprobante": "",
        }
        datos.update(extra)
        return self.client.post(self.url, datos)

    def test_monto_bajo_cero_o_negativo_da_error_en_el_campo_y_no_500(self):
        for monto in ("100", "0", "-5"):
            with self.subTest(monto=monto):
                r = self.post(monto)
                self.assertEqual(r.status_code, 200)
                self.assertIn("monto", r.context["form"].errors)
                if monto != "-5":  # el negativo tiene su propio mensaje
                    self.assertContains(r, "Corregir monto")
                self.cuota.refresh_from_db()
                self.assertNotEqual(self.cuota.estado, C.PAGADO)
                self.assertEqual(self.cuota.monto, Decimal("10000"))

    def test_el_error_dice_el_minimo_cobrable(self):
        # descuento 2000: la lista mínima es 2222.23 (90% = 2000.00), o sea
        # un cobrado mínimo de 222.23.
        r = self.post("100")
        self.assertContains(r, "222.23")

    def test_el_borde_exacto_es_valido(self):
        # lista 2222.22 -> tope 1999.99 (rechaza); 2222.23 -> 2000.00 (acepta)
        r = self.post("222.22")
        self.assertEqual(r.status_code, 200)
        r = self.post("222.23")
        self.assertRedirects(r, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.monto - self.cuota.descuento, Decimal("222.23"))

    def test_editar_una_pagada_con_descuento_y_monto_bajo_da_error(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(estado=C.PAGADO)
        r = self.post("100", estado_al_abrir="pagado")
        self.assertEqual(r.status_code, 200)
        self.assertIn("monto", r.context["form"].errors)
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("10000"))

    def test_sin_descuento_todo_sigue_igual(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("0"), descuento_motivo=""
        )
        r = self.post("100")
        self.assertRedirects(r, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("100"))


class MontoCobradoNuncaNegativoTests(_Base):
    def test_un_cobro_negativo_se_rechaza_con_y_sin_descuento(self):
        self._login_staff()
        url = reverse("pagos:confirmar", args=[self.cuota.pk])
        for descuento in ("0", "2000"):
            Cuota.objects.filter(pk=self.cuota.pk).update(
                monto=Decimal("10000"), descuento=Decimal(descuento),
                descuento_motivo="x" if descuento != "0" else "",
            )
            with self.subTest(descuento=descuento):
                r = self.client.post(url, {
                    "monto": "-5", "fecha_pago": "2026-03-05",
                    "medio_pago_texto": "Efectivo", "comprobante": "",
                })
                self.assertEqual(r.status_code, 200)
                self.assertIn("monto", r.context["form"].errors)
                self.cuota.refresh_from_db()
                self.assertNotEqual(self.cuota.estado, C.PAGADO)


class MontoMuestraElNetoGuardadoTests(_Base):
    def test_tras_un_post_invalido_la_linea_muestra_el_neto_guardado(self):
        self._login_staff()
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("10000"), descuento=Decimal("2000"), descuento_motivo="Premio"
        )
        url = reverse("pagos:monto", args=[self.cuota.pk])
        r = self.client.post(
            url, {"monto": "50000", "descuento": "49000", "descuento_motivo": "x"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "va a pagar <strong>$ 8000")


class AplicarDescuentoCentavosTests(_Base):
    def test_mas_de_dos_decimales_se_trunca_a_centavos(self):
        from pagos.descuentos import aplicar_descuento

        self.assertTrue(
            aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("100.009"), "x")
        )
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.descuento, Decimal("100.00"))
