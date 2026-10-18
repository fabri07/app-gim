"""La tienda (USD 20/mes) en el cobro online de la suscripción.

Se cobra si `gimnasio.tienda_habilitada` (no `puede_usar_tienda`); el monto
sale del toggle del día en que se genera el link o se paga, y el intento y el
pago guardan `monto_tienda_usd` para que nunca se recalcule.

Los servicios y los mails van con `TransactionTestCase` (los mails salen por
`transaction.on_commit`); las pantallas con `TestCase`. Nada sale a la red.
"""

from decimal import Decimal

from django.core import mail
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from plataforma import facturacion, precios
from suscripcion import avisos
from suscripcion.models import IntentoDeCobro
from suscripcion.tests_avisos import _gimnasio as _gimnasio_aviso
from suscripcion.tests_avisos import _usuario as _usuario_aviso
from suscripcion.tests_services import (
    COTIZACION,
    BaseServicios,
    _crear,
    _pago,
)
from plataforma.tests_facturacion import _gimnasio as _gimnasio_plataforma
from suscripcion.tests_vistas import PRENDIDO, _Base, _intento
from tenants.models import Gimnasio

Estado = IntentoDeCobro.Estado
Plan = IntentoDeCobro.Plan


def _tienda(gimnasio, valor):
    Gimnasio.objects.filter(pk=gimnasio.pk).update(tienda_habilitada=valor)


class MontosConTiendaTests(BaseServicios):
    def test_mensual_sin_tienda_es_el_de_hoy(self):
        intento = _crear(self.gimnasio)
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd(5)))
        self.assertEqual(intento.monto_tienda_usd, 0)

    def test_mensual_con_tienda_suma_20(self):
        _tienda(self.gimnasio, True)
        intento = _crear(self.gimnasio)
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd(5, tienda=True)))
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd(5)) + 20)
        self.assertEqual(intento.monto_tienda_usd, 20)
        self.assertEqual(intento.monto_ars, intento.monto_usd * COTIZACION["venta"])

    def test_anual_sin_tienda_es_el_de_hoy(self):
        intento = _crear(self.gimnasio, Plan.ANUAL)
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd_anual(5)))
        self.assertEqual(intento.monto_tienda_usd, 0)

    def test_anual_con_tienda_suma_200(self):
        _tienda(self.gimnasio, True)
        intento = _crear(self.gimnasio, Plan.ANUAL)
        self.assertEqual(
            intento.monto_usd, Decimal(precios.precio_usd_anual(5, tienda=True))
        )
        self.assertEqual(intento.monto_usd, Decimal(precios.precio_usd_anual(5)) + 200)
        self.assertEqual(intento.monto_tienda_usd, 200)
        self.assertEqual(intento.monto_ars, intento.monto_usd * COTIZACION["venta"])

    def test_el_titulo_de_la_preferencia_dice_tienda_solo_si_la_incluye(self):
        _crear(self.gimnasio)
        self.assertNotIn("tienda", self.mock_preferencia.call_args.kwargs["titulo"])
        IntentoDeCobro.objects.all().delete()
        _tienda(self.gimnasio, True)
        _crear(self.gimnasio)
        self.assertIn("+ tienda", self.mock_preferencia.call_args.kwargs["titulo"])


class ReusoDelLinkTests(BaseServicios):
    def test_mismo_estado_se_reusa(self):
        _tienda(self.gimnasio, True)
        primero = _crear(self.gimnasio)
        segundo = _crear(self.gimnasio)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(self.mock_preferencia.call_count, 1)

    def test_link_sin_tienda_y_se_prende_la_tienda_genera_uno_nuevo(self):
        viejo = _crear(self.gimnasio)
        _tienda(self.gimnasio, True)

        nuevo = _crear(self.gimnasio)

        self.assertNotEqual(viejo.pk, nuevo.pk)
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.CANCELADO)
        self.assertEqual(nuevo.estado, Estado.CREADO)
        self.assertEqual(nuevo.monto_tienda_usd, 20)
        self.assertEqual(nuevo.monto_usd, viejo.monto_usd + 20)

    def test_link_con_tienda_y_se_apaga_la_tienda_genera_uno_nuevo(self):
        _tienda(self.gimnasio, True)
        viejo = _crear(self.gimnasio)
        _tienda(self.gimnasio, False)

        nuevo = _crear(self.gimnasio)

        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.CANCELADO)
        self.assertEqual(nuevo.monto_tienda_usd, 0)
        self.assertEqual(nuevo.monto_usd, viejo.monto_usd - 20)


class AcreditacionConTiendaTests(BaseServicios):
    def test_el_pago_copia_monto_tienda_usd_del_intento(self):
        _tienda(self.gimnasio, True)
        intento = _crear(self.gimnasio)

        self._acreditar(_pago(intento))

        intento.refresh_from_db()
        self.assertEqual(intento.estado, Estado.PAGADO)
        self.assertEqual(intento.pago.monto_tienda_usd, 20)
        self.assertEqual(intento.pago.monto_usd, intento.monto_usd)

    def test_sin_tienda_el_pago_queda_en_cero(self):
        intento = _crear(self.gimnasio)
        self._acreditar(_pago(intento))
        intento.refresh_from_db()
        self.assertEqual(intento.pago.monto_tienda_usd, 0)

    def test_link_cancelado_sin_tienda_pagado_despues_de_prenderla_se_acredita_con_su_monto(
        self,
    ):
        """Se cobra lo que se mostró: el link viejo (sin tienda) que igual se
        paga se acredita con `monto_tienda_usd = 0`, nunca se recalcula."""
        viejo = _crear(self.gimnasio)
        _tienda(self.gimnasio, True)
        _crear(self.gimnasio)  # cancela el viejo
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.CANCELADO)

        self._acreditar(_pago(viejo))

        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, Estado.PAGADO)
        self.assertEqual(viejo.pago.monto_tienda_usd, 0)
        self.assertEqual(viejo.pago.monto_usd, viejo.monto_usd)


class MailsAlSuperadminConTiendaTests(BaseServicios):
    def test_cobro_acreditado_incluye_la_tienda(self):
        _tienda(self.gimnasio, True)
        intento = _crear(self.gimnasio)
        self._acreditar(_pago(intento))
        self.assertIn("incluye tienda USD 20", mail.outbox[0].body)

    def test_cobro_acreditado_sin_tienda_no_la_menciona(self):
        intento = _crear(self.gimnasio)
        self._acreditar(_pago(intento))
        self.assertNotIn("tienda", mail.outbox[0].body)

    def test_cobro_con_problema_incluye_la_tienda(self):
        _tienda(self.gimnasio, True)
        intento = _crear(self.gimnasio)
        # Monto distinto al del intento: queda en «a revisar» y avisa.
        self._acreditar(_pago(intento, transaction_amount=1.0))
        self.assertIn("incluye tienda USD 20", mail.outbox[0].body)


@PRENDIDO
class MiSuscripcionConTiendaTests(_Base):
    def _periodo(self):
        return facturacion.periodo_a_cobrar(
            facturacion.gimnasio_anotado(self.gimnasio.pk)
        )

    def _get(self):
        self._login()
        return self.client.get(reverse("suscripcion:mi_suscripcion"))

    def test_prender_la_tienda_con_un_link_vivo_sin_tienda_oculta_el_banner(self):
        desde, hasta = self._periodo()
        _intento(self.gimnasio, periodo_desde=desde, periodo_hasta=hasta)
        self.assertContains(self._get(), "Continuar al pago")

        _tienda(self.gimnasio, True)
        respuesta = self._get()

        self.assertNotContains(respuesta, "Continuar al pago")
        planes = {p["valor"]: p for p in respuesta.context["planes"]}
        self.assertEqual(planes["mensual"]["monto_usd"], precios.precio_usd(0, tienda=True))

    def test_apagar_la_tienda_con_un_link_vivo_con_tienda_oculta_el_banner(self):
        _tienda(self.gimnasio, True)
        desde, hasta = self._periodo()
        _intento(
            self.gimnasio,
            periodo_desde=desde,
            periodo_hasta=hasta,
            monto_tienda_usd=Decimal("20"),
        )
        self.assertContains(self._get(), "Continuar al pago")

        _tienda(self.gimnasio, False)

        self.assertNotContains(self._get(), "Continuar al pago")

    def test_link_anual_con_tienda_se_ofrece_si_coincide_el_anual(self):
        _tienda(self.gimnasio, True)
        desde, hasta = facturacion.periodo_a_cobrar(
            facturacion.gimnasio_anotado(self.gimnasio.pk), dias=precios.DIAS_ANIO
        )
        _intento(
            self.gimnasio,
            plan=Plan.ANUAL,
            periodo_desde=desde,
            periodo_hasta=hasta,
            monto_tienda_usd=Decimal(precios.precio_tienda_usd_anual(True)),
        )
        self.assertContains(self._get(), "Continuar al pago")

    def test_los_planes_muestran_el_total_con_tienda(self):
        _tienda(self.gimnasio, True)
        respuesta = self._get()
        planes = {p["valor"]: p for p in respuesta.context["planes"]}
        self.assertEqual(planes["mensual"]["monto_usd"], precios.precio_usd(0, tienda=True))
        self.assertEqual(
            planes["anual"]["monto_usd"], precios.precio_usd_anual(0, tienda=True)
        )
        self.assertEqual(
            planes["anual"]["sin_descuento_usd"],
            precios.precio_usd_anual_sin_descuento(0, tienda=True),
        )

    def test_la_linea_de_la_tienda_solo_con_tienda(self):
        self.assertNotContains(self._get(), "Incluye la tienda")
        _tienda(self.gimnasio, True)
        self.assertContains(
            self._get(),
            f"Incluye la tienda: USD {precios.PRECIO_TIENDA_USD} por mes "
            f"(USD {precios.precio_tienda_usd_anual(True)} en el anual).",
        )

    def test_la_frase_del_tramo_menciona_la_tienda(self):
        self.assertContains(
            self._get(),
            "si cambiás de tramo o se activa o desactiva la tienda; el anual "
            "queda fijo los 12 meses, con la tienda incluida si la tenías",
        )

    def test_la_demo_dice_que_la_tienda_es_un_adicional(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.assertContains(
            self._get(),
            f"La tienda es un adicional de USD {precios.PRECIO_TIENDA_USD} por mes.",
        )

    def test_la_demo_con_la_tienda_prendida_no_dice_incluye(self):
        """La demo no factura: «Incluye la tienda» sería un cobro que no hay."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            es_demo=True, tienda_habilitada=True
        )
        respuesta = self._get()
        self.assertNotContains(respuesta, "Incluye la tienda")
        self.assertContains(respuesta, "La tienda es un adicional")

    def test_un_gimnasio_no_demo_no_ve_la_linea_de_la_demo(self):
        self.assertNotContains(self._get(), "La tienda es un adicional")


class AvisoPorMailConTiendaTests(TransactionTestCase):
    def test_con_y_sin_tienda_en_la_misma_corrida(self):
        """El precio viaja como default del lambda: si no, los dos mails
        llevarían el valor de la última fila."""
        con = _gimnasio_aviso(
            "con-tienda", vence_en=precios.DIAS_AVISO_COBRO, tienda_habilitada=True
        )
        sin = _gimnasio_aviso("sin-tienda", vence_en=precios.DIAS_AVISO_COBRO)
        _usuario_aviso(con, "con@x.com")
        _usuario_aviso(sin, "sin@x.com")

        self.assertEqual(avisos.avisar(), 2)

        cuerpos = {m.to[0]: m.body for m in mail.outbox}
        total = precios.precio_usd(2, tienda=True)
        self.assertIn(f"USD {total} (incluye la tienda, USD 20)", cuerpos["con@x.com"])
        self.assertIn(f"USD {precios.precio_usd(2)} (se paga", cuerpos["sin@x.com"])
        self.assertNotIn("tienda", cuerpos["sin@x.com"])


class InsertDelCodigoViejoTests(TestCase):
    """El código viejo sigue atendiendo durante el `migrate` (y tras un
    rollback): su INSERT del intento no nombra `monto_tienda_usd`."""

    def test_un_insert_sin_monto_tienda_usd_guarda_cero(self):
        gimnasio = _gimnasio_plataforma("Viejo", 45)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO suscripcion_intentodecobro (creado, modificado, plan, "
                "periodo_desde, periodo_hasta, monto_usd, monto_ars, tipo_cambio, "
                "alumnos_activos, referencia_externa, preference_id, init_point, expira, "
                "estado, payment_id, estado_mp, respuesta_mp, email_de_quien_pago, "
                "gimnasio_id) VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'mensual', "
                "'2026-06-01', '2026-06-30', 10, 10000, 1000, 5, "
                "'0123456789abcdef0123456789abcdef', '', '', CURRENT_TIMESTAMP, "
                "'creado', '', '', '{}', '', %s)",
                [gimnasio.pk],
            )
        self.assertEqual(IntentoDeCobro.objects.get().monto_tienda_usd, 0)
