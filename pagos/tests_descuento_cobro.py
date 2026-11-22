"""El cobro respeta el monto NETO (pieza 3 de reconocimientos, T3).

Invariante de una cuota PAGADA: `monto - descuento == lo realmente cobrado`.
Se fija en los tres caminos que escriben plata: el link de Mercado Pago
(`cobro_mp.link_de_pago`), la acreditación (`cobro_mp._aprobado`) y
«Confirmar pago» del staff (`ConfirmarPagoForm` + `cobro.marcar_pagada`).
La suite no sale a la red: se reusan las bases con MP simulado.
"""

from decimal import Decimal
from unittest import mock

from django.urls import reverse

from pagos import cobro, cobro_mp
from pagos.descuentos import aplicar_descuento
from pagos.forms import ConfirmarPagoForm
from pagos.models import CobroOnlineCuota, Cuota
from pagos.tests_cobro import _Base as _BaseConfirmar
from pagos.tests_cobro import _mensajes
from pagos.tests_cobro_mp import _Base as _BaseAcreditar
from pagos.tests_pagar_mp import _Base as _BasePagar
from pagos.tests_pagar_mp import preferencia

I = CobroOnlineCuota.Estado
M = CobroOnlineCuota.MotivoRevision
C = Cuota.Estado


def _invariante(test, cuota, cobrado):
    cuota.refresh_from_db()
    test.assertEqual(cuota.monto - cuota.descuento, Decimal(cobrado))


# --- El link de pago ------------------------------------------------------------


class LinkPorElNetoTests(_BasePagar):
    def setUp(self):
        super().setUp()
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("10000"), descuento=Decimal("2000"), descuento_motivo="Premio"
        )

    def test_el_link_cobra_el_neto(self):
        r = self.pagar()
        self.assertEqual(r.status_code, 302)
        intento = CobroOnlineCuota.objects.get()
        self.assertEqual(intento.monto, Decimal("8000"))
        self.assertEqual(self.crear.call_args.kwargs["monto_ars"], Decimal("8000"))

    def test_un_link_vivo_por_el_neto_se_reusa(self):
        self.pagar()
        r = self.pagar()
        self.assertEqual(r["Location"], "https://mp.test/checkout/1")
        self.crear.assert_not_called()
        self.assertEqual(CobroOnlineCuota.objects.count(), 1)

    def test_otro_descuento_no_reusa_el_link_viejo(self):
        """Aunque el descuento cambie sin pasar por el servicio (que ya
        cancela), el link por el neto viejo no se reusa."""
        self.pagar()
        viejo = CobroOnlineCuota.objects.get()
        Cuota.objects.filter(pk=self.cuota.pk).update(descuento=Decimal("3000"))
        r = self.pagar(devuelve=preferencia(2))
        self.assertEqual(r["Location"], "https://mp.test/checkout/2")
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, I.CANCELADO)
        nuevo = CobroOnlineCuota.objects.get(estado=I.CREADO)
        self.assertEqual(nuevo.monto, Decimal("7000"))

    def test_aplicar_descuento_cancela_y_el_nuevo_link_va_por_el_neto_nuevo(self):
        self.pagar()
        viejo = CobroOnlineCuota.objects.get()
        self.assertTrue(
            aplicar_descuento(self.cuota.pk, self.gimnasio, Decimal("3000"), "Premio")
        )
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, I.CANCELADO)
        self.pagar(devuelve=preferencia(2))
        nuevo = CobroOnlineCuota.objects.get(estado=I.CREADO)
        self.assertEqual(nuevo.monto, Decimal("7000"))
        self.assertEqual(self.crear.call_args.kwargs["monto_ars"], Decimal("7000"))

    def test_monto_cero_sigue_sin_monto(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(monto=0, descuento=0)
        r = self.pagar()
        self.assertIn(cobro_mp.MENSAJES_PAGAR["sin_monto"], self.mensajes(r))
        self.crear.assert_not_called()

    def test_descuento_del_90_por_ciento_deja_un_neto_cobrable(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(descuento=Decimal("9000"))
        self.pagar()
        self.assertEqual(CobroOnlineCuota.objects.get().monto, Decimal("1000"))
        self.assertEqual(self.crear.call_args.kwargs["monto_ars"], Decimal("1000"))


# --- La acreditación ---------------------------------------------------------------


class AcreditarElNetoTests(_BaseAcreditar):
    def setUp(self):
        super().setUp()
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("10000"), descuento=Decimal("2000"), descuento_motivo="Premio"
        )
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(monto=Decimal("8000"))
        self.refrescar()

    def test_pago_del_neto_deja_la_cuota_pagada_sin_revision(self):
        _, staff, alumno = self.acreditar(self.pago(monto="8000"))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.monto, Decimal("10000"))
        self.assertEqual(self.cuota.descuento, Decimal("2000"))
        self.assertEqual(self.cuota.monto_a_cobrar, Decimal("8000"))
        self.assertEqual(self.intento.estado, I.PAGADO)
        self.assertEqual(self.intento.motivo_revision, "")
        self.assertFalse(self.intento.revision_pendiente)
        self.assertEqual(staff, ["pago_mp"])
        self.assertEqual(alumno, ["pago_mp"])

    def test_carrera_descuento_cambiado_despues_de_leer_la_cuota(self):
        """Se leyó la cuota con descuento 2000; antes del UPDATE el staff le
        pone 3000. El monto lo resuelve la base (`F("descuento")`): la
        invariante se cumple con el descuento que de verdad quedó."""
        leer = cobro_mp._leer
        llamadas = []

        def descuento_cambia_tras_leer(cuota_pk, intento_pk):
            cuota, intento = leer(cuota_pk, intento_pk)
            if not llamadas:
                Cuota.objects.filter(pk=cuota_pk).update(descuento=Decimal("3000"))
            llamadas.append(cuota_pk)
            return cuota, intento

        with mock.patch("pagos.cobro_mp._leer", side_effect=descuento_cambia_tras_leer):
            self.acreditar(self.pago(monto="8000"))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.descuento, Decimal("3000"))
        _invariante(self, self.cuota, "8000")

    def test_pago_distinto_del_intento_queda_a_revisar_sin_tocar_la_cuota(self):
        _, staff, _ = self.acreditar(self.pago(monto="10000"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.REVISAR)
        self.assertEqual(self.intento.motivo_revision, M.MONTO_DISTINTO)
        self.assertEqual(self.cuota.estado, C.PENDIENTE)
        self.assertEqual(
            (self.cuota.monto, self.cuota.descuento), (Decimal("10000"), Decimal("2000"))
        )

    def test_descuento_corregido_con_el_link_abierto_es_monto_corregido(self):
        """El alumno paga el link viejo (8000) después de que el staff subió
        el descuento a 3000 (neto 7000): se compara contra el neto."""
        Cuota.objects.filter(pk=self.cuota.pk).update(descuento=Decimal("3000"))
        _, staff, _ = self.acreditar(self.pago(monto="8000"))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.intento.motivo_revision, M.MONTO_CORREGIDO)
        self.assertTrue(self.intento.revision_pendiente)
        self.assertEqual(staff, ["revisar_mp"])
        _invariante(self, self.cuota, "8000")

    def test_descuento_enorme_frente_a_lo_cobrado_no_rompe_el_tope(self):
        """Link viejo de 500 y, en el medio, lista 20000 con descuento 18000:
        `18000 + 500` pasaría del tope del 90% y la base rechazaría el pago.
        La plata manda: se registra lo cobrado, sin descuento, y a revisar."""
        CobroOnlineCuota.objects.filter(pk=self.intento.pk).update(monto=Decimal("500"))
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("20000"), descuento=Decimal("18000")
        )
        self.refrescar()
        self.acreditar(self.pago(monto="500"))
        self.refrescar()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.intento.motivo_revision, M.MONTO_CORREGIDO)
        self.assertEqual(self.cuota.descuento, Decimal("0"))
        self.assertEqual(self.cuota.descuento_motivo, "")
        _invariante(self, self.cuota, "500")

    def test_volver_a_impaga_tras_un_cobro_devuelto_vuelve_al_neto(self):
        self.acreditar(self.pago(monto="8000"))
        self.acreditar(self.pago(monto="8000", status="refunded"))
        self.refrescar()
        self.assertEqual(self.intento.estado, I.DEVUELTO)
        self.assertTrue(cobro.volver_a_impaga(self.cuota))
        self.refrescar()
        self.assertIn(self.cuota.estado, Cuota.ESTADOS_IMPAGOS)
        self.assertEqual(self.cuota.monto, Decimal("10000"))
        self.assertEqual(self.cuota.monto_a_cobrar, Decimal("8000"))


# --- «Confirmar pago» del staff --------------------------------------------------------


class ConfirmarPagoNetoTests(_BaseConfirmar):
    """Cuota de 28000 con descuento 2000: el neto es 26000."""

    def setUp(self):
        super().setUp()
        self._login_staff()
        Cuota.objects.filter(pk=self.cuota.pk).update(
            descuento=Decimal("2000"), descuento_motivo="Premio"
        )
        self.cuota.refresh_from_db()
        self.url = reverse("pagos:confirmar", args=[self.cuota.pk])

    def _post(self, **datos):
        datos = {
            "monto": "26000", "fecha_pago": self.hoy.isoformat(),
            "medio_pago_texto": "Efectivo", "estado_al_abrir": "pendiente", **datos,
        }
        return self.client.post(self.url, datos)

    def test_el_form_no_expone_el_descuento(self):
        self.assertNotIn("descuento", ConfirmarPagoForm.base_fields)
        self.assertNotIn("descuento_motivo", ConfirmarPagoForm.base_fields)

    def test_el_valor_inicial_del_monto_es_el_neto(self):
        form = self.client.get(self.url).context["form"]
        self.assertEqual(Decimal(form["monto"].value()), Decimal("26000"))

    def test_guardar_sin_tocar_deja_el_neto(self):
        response = self._post()
        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PAGADO)
        self.assertEqual(self.cuota.descuento, Decimal("2000"))
        _invariante(self, self.cuota, "26000")

    def test_lo_cobrado_se_suma_al_descuento(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            monto=Decimal("10000"), descuento=Decimal("2000")
        )
        self._post(monto="7000")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("9000"))
        self.assertEqual(self.cuota.descuento, Decimal("2000"))

    def test_editar_una_pagada_por_mp_no_cambia_el_monto(self):
        Cuota.objects.filter(pk=self.cuota.pk).update(
            estado=C.PAGADO, fecha_pago=self.hoy, medio_pago_texto="Mercado Pago"
        )
        self._intento(estado=CobroOnlineCuota.Estado.PAGADO, payment_id="999")
        self._post(estado_al_abrir="pagado", monto="1")
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.monto, Decimal("28000"))
        self.assertEqual(self.cuota.descuento, Decimal("2000"))

    def test_marcar_pagada_con_descuento_cambiado_no_escribe(self):
        cuota = Cuota.objects.get(pk=self.cuota.pk)
        cuota.monto = Decimal("28000")
        cuota.medio_pago_texto = "Efectivo"
        Cuota.objects.filter(pk=self.cuota.pk).update(descuento=Decimal("3000"))
        self.assertFalse(cobro.marcar_pagada(cuota))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)
        self.assertEqual(self.cuota.medio_pago_texto, "")
        self.assertEqual(self.cuota.descuento, Decimal("3000"))

    def test_descuento_cambiado_entre_leer_y_guardar_avisa_sin_mentir(self):
        """Entre que la vista leyó la cuota y toma el lock, el staff (otra
        pestaña) cambió el descuento: no se guarda y el cartel no dice que la
        cobró Mercado Pago."""
        subir = cobro._subir_comprobante

        def cambia_el_descuento(cuota):
            Cuota.objects.filter(pk=cuota.pk).update(descuento=Decimal("3000"))
            return subir(cuota)

        with mock.patch("pagos.cobro._subir_comprobante", side_effect=cambia_el_descuento):
            response = self._post()
        self.assertRedirects(response, reverse("pagos:listado"))
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.estado, C.PENDIENTE)
        mensajes = _mensajes(response)
        self.assertFalse(any("Mercado Pago" in m for m in mensajes), mensajes)
        self.assertTrue(any("descuento" in m for m in mensajes), mensajes)
