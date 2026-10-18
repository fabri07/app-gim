"""Acreditar pagos de Mercado Pago de la tienda (B5): la máquina de estados de
`pagos_mp.acreditar_pedido` combinación por combinación, las carreras con
`vencer_pedidos`, el webhook, `reconciliar_pedidos` y su comando.

Sin red: se parchean `suscripcion.mercadopago.consultar_pago` y
`buscar_pagos`. Los avisos se miran parcheando las dos funciones de
`notificaciones.services` y corriendo los `on_commit`."""

import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from alumnos.models import Alumno
from suscripcion.mercadopago import ErrorDeMercadoPago
from tenants.models import Gimnasio, Perfil
from tienda import pagos_mp, services
from tienda.models import CuentaMercadoPago, Pedido, Producto, Variante

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
E = Pedido.Estado
CONSULTAR = "suscripcion.mercadopago.consultar_pago"
BUSCAR = "suscripcion.mercadopago.buscar_pagos"
AL_STAFF = "notificaciones.services.notificar_pedido_al_staff"
AL_ALUMNO = "notificaciones.services.notificar_pedido_al_alumno"


@override_settings(**MP)
class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym A", slug="a", tienda_habilitada=True)
        usuario = User.objects.create_user("alu", password="c-123456")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil
        )
        self.producto = Producto.objects.create(
            gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000")
        )
        self.variante = Variante.objects.create(producto=self.producto, nombre="M", stock=5)
        self.cuenta = self.crear_cuenta()

    def crear_cuenta(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, access_token="APP_USR-acceso",
            refresh_token="REF", mp_user_id="111",
            token_expira=timezone.now() + timedelta(days=100),
            estado=CuentaMercadoPago.Estado.CONECTADA,
        )
        datos.update(extra)
        return CuentaMercadoPago.objects.create(**datos)

    def pedido(self, estado=E.PENDIENTE_PAGO, **extra):
        ahora = timezone.now()
        datos = dict(
            gimnasio=self.gimnasio, alumno=self.alumno, total=Decimal("15000"),
            medio=Pedido.Medio.MERCADOPAGO, estado=estado,
            reservado_hasta=ahora + timedelta(minutes=30), preference_id="pref-1",
            init_point="https://mp/pref-1",
        )
        datos.update(extra)
        pedido = Pedido.objects.create(**datos)
        pedido.items.create(
            variante=self.variante, nombre="Remera · M",
            precio_unitario=Decimal("15000"), cantidad=1,
        )
        return pedido

    def pago(self, pedido, id="P1", status="approved", monto="15000", moneda="ARS"):
        """Una respuesta de MP con datos del pagador (nunca se guardan)."""
        return {
            "id": int(id[1:]) if id.startswith("P") and id[1:].isdigit() else id,
            "status": status,
            "transaction_amount": float(monto),
            "currency_id": moneda,
            "external_reference": str(pedido.referencia_externa),
            "payer": {"email": "nunca@se.guarda", "identification": {"number": "20111222"}},
        }

    def acreditar(self, pago, cuenta=None):
        """Corre `acreditar_pedido` con el pago parcheado y los avisos
        capturados. Devuelve (pedido, staff, alumno): las claves de aviso."""
        with mock.patch(CONSULTAR, return_value=pago) as consultar, \
                mock.patch(AL_STAFF) as staff, mock.patch(AL_ALUMNO) as alumno, \
                self.captureOnCommitCallbacks(execute=True):
            resultado = pagos_mp.acreditar_pedido(cuenta or self.cuenta, str(pago["id"]))
        self.consultar = consultar
        return (
            resultado,
            [c.args[1] for c in staff.call_args_list],
            [c.args[1] for c in alumno.call_args_list],
        )

    def stock(self):
        self.variante.refresh_from_db()
        return self.variante.stock

    def estado(self, pedido):
        pedido.refresh_from_db()
        return pedido.estado


# --- approved -----------------------------------------------------------------


class AprobadoTests(_Base):
    def test_pendiente_queda_pagado_con_pagado_en_y_avisos(self):
        p = self.pedido()
        final, staff, alumno = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)
        self.assertEqual(final.payment_id, "1")
        self.assertEqual(final.estado_mp, "approved")
        self.assertIsNotNone(final.pagado_en)
        self.assertEqual(self.stock(), 5)  # ya estaba descontado: no se toca
        self.assertEqual(staff, ["pago_mp"])
        self.assertEqual(alumno, ["confirmado"])

    def test_el_token_del_gimnasio_va_envuelto_y_no_se_guarda_el_pagador(self):
        p = self.pedido()
        final, _, _ = self.acreditar(self.pago(p))
        token = self.consultar.call_args.kwargs["token"]
        self.assertEqual(repr(token), "Secreto(***)")
        self.assertEqual(token.valor, "APP_USR-acceso")
        self.assertNotIn("nunca@se.guarda", str(final.respuesta_mp))
        self.assertNotIn("payer", final.respuesta_mp)

    def test_pendiente_con_un_rechazado_anterior_lo_reemplaza(self):
        p = self.pedido(payment_id="9", estado_mp="rejected")
        final, _, _ = self.acreditar(self.pago(p, id="P1"))
        self.assertEqual((final.estado, final.payment_id), (E.PAGADO, "1"))

    def test_pendiente_con_monto_distinto_va_a_revisar_y_devuelve_el_stock(self):
        p = self.pedido()
        final, staff, alumno = self.acreditar(self.pago(p, monto="100"))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(final.payment_id, "1")
        self.assertIsNone(final.pagado_en)
        self.assertEqual(self.stock(), 6)
        self.assertEqual(staff, ["revisar_mp"])
        self.assertEqual(alumno, ["revisar_mp"])

    def test_un_centavo_de_diferencia_se_tolera(self):
        p = self.pedido()
        final, _, _ = self.acreditar(self.pago(p, monto="15000.01"))
        self.assertEqual(final.estado, E.PAGADO)

    def test_pendiente_en_otra_moneda_va_a_revisar(self):
        p = self.pedido()
        final, _, _ = self.acreditar(self.pago(p, moneda="USD"))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(self.stock(), 6)

    def test_vencido_con_stock_se_re_reserva_y_queda_pagado(self):
        p = self.pedido(E.VENCIDO)
        final, staff, alumno = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)
        self.assertIsNotNone(final.pagado_en)
        self.assertEqual(self.stock(), 4)
        self.assertEqual((staff, alumno), (["pago_mp"], ["confirmado"]))

    def test_vencido_sin_stock_va_a_revisar_sin_tocar_el_stock(self):
        p = self.pedido(E.VENCIDO)
        Variante.objects.filter(pk=self.variante.pk).update(stock=0)
        final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(final.payment_id, "1")
        self.assertIsNone(final.pagado_en)
        self.assertEqual(self.stock(), 0)
        self.assertEqual(staff, ["revisar_mp"])

    def test_re_reserva_todo_o_nada_con_dos_variantes(self):
        p = self.pedido(E.VENCIDO)
        otra = Variante.objects.create(producto=self.producto, nombre="L", stock=0)
        p.items.create(variante=otra, nombre="Remera · L", precio_unitario=Decimal("0"), cantidad=1)
        final, _, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(self.stock(), 5)  # la M no quedó descontada a medias
        otra.refresh_from_db()
        self.assertEqual(otra.stock, 0)

    def test_cancelado_por_el_alumno_se_re_reserva(self):
        p = self.pedido(E.CANCELADO, motivo="")
        final, _, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)
        self.assertEqual(self.stock(), 4)

    def test_cancelado_por_el_staff_va_a_revisar_y_no_se_revive(self):
        p = self.pedido(E.CANCELADO, motivo="No hay más talles")
        final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(self.stock(), 5)
        self.assertEqual(staff, ["revisar_mp"])

    def test_vencido_con_monto_distinto_va_a_revisar_sin_tocar_el_stock(self):
        p = self.pedido(E.VENCIDO)
        final, _, _ = self.acreditar(self.pago(p, monto="1"))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(self.stock(), 5)

    def test_cancelado_con_monto_distinto_va_a_revisar(self):
        p = self.pedido(E.CANCELADO, motivo="")
        final, _, _ = self.acreditar(self.pago(p, moneda="BRL"))
        self.assertEqual(final.estado, E.REVISAR)
        self.assertEqual(self.stock(), 5)

    def test_pagado_a_mano_sin_payment_id_adjunta_el_pago_sin_alarma(self):
        antes = timezone.now() - timedelta(hours=1)
        p = self.pedido(E.PAGADO, pagado_en=antes)
        final, staff, alumno = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)
        self.assertEqual(final.payment_id, "1")
        self.assertEqual(final.pagado_en, antes)
        self.assertFalse(final.revision_mp_pendiente)
        self.assertEqual((staff, alumno), ([], []))

    def test_entregado_a_mano_sin_payment_id_adjunta_el_pago(self):
        p = self.pedido(E.ENTREGADO)
        final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual((final.estado, final.payment_id), (E.ENTREGADO, "1"))
        self.assertEqual(staff, [])

    def _assert_adicional(self, estado, **extra):
        p = self.pedido(estado, payment_id=extra.pop("payment_id", "9"), estado_mp="approved", **extra)
        final, staff, alumno = self.acreditar(self.pago(p, id="P2"))
        self.assertEqual(final.estado, estado)
        self.assertNotEqual(final.payment_id, "2")
        self.assertTrue(final.revision_mp_pendiente)
        adicionales = final.respuesta_mp["pagos_adicionales"]
        self.assertEqual([a["payment_id"] for a in adicionales], ["2"])
        self.assertEqual(staff, ["pago_duplicado"])
        self.assertEqual(alumno, [])
        return p

    def test_pagado_con_otro_payment_id_es_pago_adicional(self):
        p = self._assert_adicional(E.PAGADO)
        self.assertEqual(self.stock(), 5)
        # Repetido (webhook + retorno + cron): el aviso sale una sola vez.
        final, staff, _ = self.acreditar(self.pago(p, id="P2"))
        self.assertEqual(staff, [])
        self.assertEqual(len(final.respuesta_mp["pagos_adicionales"]), 1)

    def test_entregado_con_otro_payment_id_es_pago_adicional(self):
        self._assert_adicional(E.ENTREGADO)

    def test_revisar_con_otro_payment_id_es_pago_adicional(self):
        self._assert_adicional(E.REVISAR)

    def test_devuelto_con_otro_payment_id_es_pago_adicional(self):
        self._assert_adicional(E.DEVUELTO)

    def test_devuelto_sin_payment_id_es_pago_adicional(self):
        self._assert_adicional(E.DEVUELTO, payment_id="")

    def test_el_mismo_aprobado_dos_veces_no_hace_nada_la_segunda(self):
        p = self.pedido()
        self.acreditar(self.pago(p))
        final, staff, alumno = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)
        self.assertEqual((staff, alumno), ([], []))
        self.assertEqual(self.stock(), 5)

    def test_la_re_reserva_no_descuenta_dos_veces(self):
        p = self.pedido(E.VENCIDO)
        self.acreditar(self.pago(p))
        self.acreditar(self.pago(p))
        self.assertEqual(self.stock(), 4)

    def test_un_aprobado_viejo_no_revive_un_pago_ya_devuelto(self):
        p = self.pedido(E.DEVUELTO, payment_id="1", estado_mp="refunded")
        final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual((final.estado, final.estado_mp), (E.DEVUELTO, "refunded"))
        self.assertFalse(final.revision_mp_pendiente)
        self.assertEqual(staff, [])

    def test_un_aprobado_viejo_no_acredita_un_pedido_vencido_con_ese_pago_devuelto(self):
        p = self.pedido(E.VENCIDO, payment_id="1", estado_mp="refunded")
        final, _, _ = self.acreditar(self.pago(p))
        self.assertEqual((final.estado, final.estado_mp), (E.VENCIDO, "refunded"))
        self.assertEqual(self.stock(), 5)

    def test_es_permisivo_con_la_tienda_apagada_sin_publicar_y_la_cuenta_congelada(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            tienda_habilitada=False, estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA
        )
        p = self.pedido()
        final, _, _ = self.acreditar(self.pago(p))
        self.assertEqual(final.estado, E.PAGADO)

    def test_conserva_los_pagos_adicionales_al_reescribir_la_respuesta(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        self.acreditar(self.pago(p, id="P2"))
        final, _, _ = self.acreditar(self.pago(p, id="P1", status="refunded"))
        self.assertEqual(final.estado, E.DEVUELTO)
        self.assertEqual(final.respuesta_mp["pagos_adicionales"][0]["payment_id"], "2")
        self.assertTrue(final.revision_mp_pendiente)


# --- rejected / cancelled ---------------------------------------------------------


class RechazadoTests(_Base):
    def test_sobre_pendiente_vencido_o_cancelado_guarda_el_pago(self):
        for estado in (E.PENDIENTE_PAGO, E.VENCIDO, E.CANCELADO):
            with self.subTest(estado=estado):
                p = self.pedido(estado)
                final, staff, alumno = self.acreditar(self.pago(p, status="rejected"))
                self.assertEqual(final.estado, estado)
                self.assertEqual((final.payment_id, final.estado_mp), ("1", "rejected"))
                self.assertEqual((staff, alumno), ([], []))
                Pedido.objects.filter(pk=p.pk).update(payment_id="")

    def test_cancelled_se_trata_como_rechazado(self):
        p = self.pedido()
        final, _, _ = self.acreditar(self.pago(p, status="cancelled"))
        self.assertEqual((final.estado, final.estado_mp), (E.PENDIENTE_PAGO, "cancelled"))

    def test_rechazado_de_otro_pago_sobre_un_pedido_pagado_no_toca_nada(self):
        p = self.pedido(E.PAGADO, payment_id="9", estado_mp="approved")
        antes = p.modificado
        final, staff, _ = self.acreditar(self.pago(p, id="P2", status="rejected"))
        self.assertEqual((final.estado, final.payment_id, final.estado_mp), (E.PAGADO, "9", "approved"))
        self.assertEqual(final.modificado, antes)
        self.assertFalse(final.revision_mp_pendiente)
        self.assertEqual(staff, [])

    def test_un_rechazado_viejo_del_mismo_pago_no_pisa_el_aprobado(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        final, _, _ = self.acreditar(self.pago(p, status="rejected"))
        self.assertEqual((final.estado, final.estado_mp), (E.PAGADO, "approved"))

    def test_rechazado_y_despues_aprobado_con_otra_tarjeta(self):
        p = self.pedido()
        self.acreditar(self.pago(p, id="P1", status="rejected"))
        final, _, _ = self.acreditar(self.pago(p, id="P2"))
        self.assertEqual((final.estado, final.payment_id), (E.PAGADO, "2"))

    def test_rechazado_de_otro_pago_no_pisa_la_marca_de_un_devuelto(self):
        p = self.pedido(E.VENCIDO, payment_id="1", estado_mp="refunded")
        final, _, _ = self.acreditar(self.pago(p, id="P2", status="rejected"))
        self.assertEqual((final.payment_id, final.estado_mp), ("1", "refunded"))


# --- refunded / charged_back ---------------------------------------------------------


class DevueltoTests(_Base):
    def test_pagado_pasa_a_devuelto_y_devuelve_el_stock(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        final, staff, alumno = self.acreditar(self.pago(p, status="refunded"))
        self.assertEqual((final.estado, final.estado_mp), (E.DEVUELTO, "refunded"))
        self.assertEqual(self.stock(), 6)
        self.assertEqual((staff, alumno), (["devuelto_mp"], ["devuelto_mp"]))

    def test_charged_back_igual_que_refunded(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        final, _, _ = self.acreditar(self.pago(p, status="charged_back"))
        self.assertEqual(final.estado, E.DEVUELTO)
        self.assertEqual(self.stock(), 6)

    def test_resuelve_un_arrepentimiento_pendiente(self):
        p = self.pedido(
            E.PAGADO, payment_id="1", estado_mp="approved",
            cancelacion_pedida_en=timezone.now(),
        )
        final, _, _ = self.acreditar(self.pago(p, status="refunded"))
        self.assertIsNotNone(final.cancelacion_resuelta_en)

    def test_entregado_y_revisar_pasan_a_devuelto_sin_stock(self):
        for estado in (E.ENTREGADO, E.REVISAR):
            with self.subTest(estado=estado):
                p = self.pedido(estado, payment_id=f"1{estado}", estado_mp="approved")
                pago = self.pago(p, status="refunded")
                pago["id"] = f"1{estado}"
                final, staff, _ = self.acreditar(pago)
                self.assertEqual(final.estado, E.DEVUELTO)
                self.assertEqual(self.stock(), 5)
                self.assertEqual(staff, ["devuelto_mp"])

    def test_ya_devuelto_es_no_op_explicito(self):
        p = self.pedido(E.DEVUELTO, payment_id="1", estado_mp="approved")
        final, staff, alumno = self.acreditar(self.pago(p, status="refunded"))
        self.assertEqual(final.estado, E.DEVUELTO)
        self.assertEqual(final.estado_mp, "refunded")  # sólo se anota
        self.assertEqual(self.stock(), 5)
        self.assertEqual((staff, alumno), ([], []))

    def test_devuelto_de_otro_pago_sobre_un_pedido_pagado_no_toca_nada(self):
        p = self.pedido(E.PAGADO, payment_id="9", estado_mp="approved")
        final, staff, _ = self.acreditar(self.pago(p, id="P2", status="refunded"))
        self.assertEqual((final.estado, final.payment_id), (E.PAGADO, "9"))
        self.assertEqual(self.stock(), 5)
        self.assertEqual(staff, [])

    def test_devolver_el_duplicado_deja_el_pedido_bueno_pagado_y_vacia_la_pestana(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        self.acreditar(self.pago(p, id="P2"))
        self.assertTrue(Pedido.objects.filter(services.Q_REVISAR_MP, pk=p.pk).exists())
        final, staff, alumno = self.acreditar(self.pago(p, id="P2", status="refunded"))
        self.assertEqual((final.estado, final.payment_id, final.estado_mp), (E.PAGADO, "1", "approved"))
        self.assertFalse(final.revision_mp_pendiente)
        self.assertTrue(final.respuesta_mp["pagos_adicionales"][0]["resuelto"])
        self.assertFalse(Pedido.objects.filter(services.Q_REVISAR_MP, pk=p.pk).exists())
        self.assertEqual(self.stock(), 5)
        self.assertEqual((staff, alumno), ([], []))

    def test_con_dos_duplicados_la_primera_devolucion_no_vacia_la_pestana(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        self.acreditar(self.pago(p, id="P2"))
        self.acreditar(self.pago(p, id="P3"))
        final, _, _ = self.acreditar(self.pago(p, id="P2", status="refunded"))
        self.assertTrue(final.revision_mp_pendiente)
        final, _, _ = self.acreditar(self.pago(p, id="P3", status="refunded"))
        self.assertFalse(final.revision_mp_pendiente)
        self.assertEqual(final.estado, E.PAGADO)

    def test_devuelto_sobre_un_pedido_no_pagado_se_anota_y_frena_un_aprobado_viejo(self):
        p = self.pedido(E.VENCIDO)
        final, staff, _ = self.acreditar(self.pago(p, status="refunded"))
        self.assertEqual((final.estado, final.payment_id, final.estado_mp), (E.VENCIDO, "1", "refunded"))
        self.assertEqual(staff, [])
        final, _, _ = self.acreditar(self.pago(p, status="approved"))
        self.assertEqual(final.estado, E.VENCIDO)
        self.assertEqual(self.stock(), 5)


# --- otros status ------------------------------------------------------------------


class OtrosStatusTests(_Base):
    def test_no_cambian_nada(self):
        for status in ("in_process", "pending", "authorized", "in_mediation"):
            for estado in (E.PENDIENTE_PAGO, E.PAGADO):
                with self.subTest(status=status, estado=estado):
                    p = self.pedido(estado)
                    final, staff, alumno = self.acreditar(self.pago(p, status=status))
                    self.assertEqual((final.estado, final.payment_id, final.estado_mp), (estado, "", ""))
                    self.assertEqual((staff, alumno), ([], []))

    def test_un_in_process_viejo_no_pisa_el_aprobado(self):
        p = self.pedido(E.PAGADO, payment_id="1", estado_mp="approved")
        final, _, _ = self.acreditar(self.pago(p, status="in_process"))
        self.assertEqual(final.estado_mp, "approved")


# --- de quién es el pago --------------------------------------------------------------


class DeQuienEsElPagoTests(_Base):
    def test_un_pedido_de_otro_gimnasio_no_se_encuentra(self):
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b", tienda_habilitada=True)
        cuenta_b = self.crear_cuenta(gimnasio=otro, mp_user_id="222")
        p = self.pedido()
        resultado, _, _ = self.acreditar(self.pago(p), cuenta=cuenta_b)
        self.assertIsNone(resultado)
        self.assertEqual(self.estado(p), E.PENDIENTE_PAGO)

    def test_un_pedido_de_transferencia_no_se_toca(self):
        p = self.pedido(medio=Pedido.Medio.TRANSFERENCIA)
        resultado, _, _ = self.acreditar(self.pago(p))
        self.assertIsNone(resultado)
        self.assertEqual(self.estado(p), E.PENDIENTE_PAGO)

    def test_referencia_invalida_o_respuesta_vacia(self):
        p = self.pedido()
        pago = self.pago(p)
        pago["external_reference"] = "no-es-un-uuid"
        self.assertIsNone(self.acreditar(pago)[0])
        with mock.patch(CONSULTAR, return_value=[]):
            self.assertIsNone(pagos_mp.acreditar_pedido(self.cuenta, "1"))

    def test_el_error_de_mercado_pago_se_propaga(self):
        with mock.patch(CONSULTAR, side_effect=ErrorDeMercadoPago("HTTP 500", codigo=500)):
            with self.assertRaises(ErrorDeMercadoPago):
                pagos_mp.acreditar_pedido(self.cuenta, "1")


# --- carreras con vencer_pedidos ---------------------------------------------------------


class CarrerasTests(_Base):
    def test_update_que_no_toca_filas_relee_y_decide(self):
        """Se leyó PENDIENTE, pero `vencer_pedidos` lo venció (y devolvió el
        stock) antes del UPDATE: se relee VENCIDO y se re-reserva."""
        p = self.pedido(reservado_hasta=timezone.now() - timedelta(hours=1))
        leer = pagos_mp._leer_pedido
        llamadas = []

        def vieja_la_primera_vez(pk):
            pedido = leer(pk)
            if not llamadas:
                services.vencer_pedidos(gimnasio=self.gimnasio)
            llamadas.append(pk)
            return pedido

        with mock.patch("tienda.pagos_mp._leer_pedido", side_effect=vieja_la_primera_vez):
            final, staff, _ = self.acreditar(self.pago(p))
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(final.estado, E.PAGADO)
        self.assertEqual(self.stock(), 5)  # vencer devolvió 1 y la re-reserva lo tomó
        self.assertEqual(staff, ["pago_mp"])

    def test_dos_veces_sin_tocar_filas_es_un_error_reintentable(self):
        p = self.pedido(E.VENCIDO)
        vieja = Pedido.objects.get(pk=p.pk)
        vieja.estado = E.PENDIENTE_PAGO
        with mock.patch("tienda.pagos_mp._leer_pedido", return_value=vieja), \
                mock.patch(CONSULTAR, return_value=self.pago(p)):
            with self.assertRaises(pagos_mp.AcreditacionReintentable):
                pagos_mp.acreditar_pedido(self.cuenta, "1")
        self.assertEqual(self.estado(p), E.VENCIDO)
        self.assertEqual(self.stock(), 5)


# --- webhook ---------------------------------------------------------------------------


class WebhookTests(_Base):
    def url(self, cuenta=None):
        return f"/tienda/v1/webhook/mercadopago/{(cuenta or self.cuenta).uuid_webhook}/"

    def notificar(self, cuenta=None, cuerpo=None, **consultar):
        cuerpo = cuerpo if cuerpo is not None else {"type": "payment", "data": {"id": "1"}}
        with mock.patch(CONSULTAR, **consultar) as llamada, mock.patch(AL_STAFF), \
                mock.patch(AL_ALUMNO), self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                self.url(cuenta), data=cuerpo, content_type="application/json"
            )
        self.llamada = llamada
        return respuesta

    def test_bandera_apagada_503(self):
        with override_settings(TIENDA_MP_ENABLED=False):
            respuesta = self.notificar()
        self.assertEqual(respuesta.status_code, 503)
        self.llamada.assert_not_called()

    def test_cuenta_inexistente_200_ignorado(self):
        respuesta = self.client.post(
            f"/tienda/v1/webhook/mercadopago/{uuid.uuid4()}/",
            data={"type": "payment", "data": {"id": "1"}}, content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["accion"], "ignorado")

    def test_cuenta_sin_tokens_200_sin_llamar_a_mercado_pago(self):
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).update(
            access_token="", refresh_token="", estado=CuentaMercadoPago.Estado.DESCONECTADA
        )
        respuesta = self.notificar()
        self.assertEqual(respuesta.status_code, 200)
        self.llamada.assert_not_called()

    def test_token_ilegible_200_sin_llamar(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            respuesta = self.notificar()
        self.assertEqual(respuesta.status_code, 200)
        self.llamada.assert_not_called()

    def test_otro_tipo_o_sin_data_id_200(self):
        for cuerpo in ({"type": "merchant_order", "data": {"id": "1"}}, {"type": "payment"}, {}):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.notificar(cuerpo=cuerpo)
                self.assertEqual(respuesta.status_code, 200)
                self.llamada.assert_not_called()

    def test_acredita_el_pago(self):
        p = self.pedido()
        respuesta = self.notificar(return_value=self.pago(p))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.estado(p), E.PAGADO)

    def test_acepta_el_formato_de_querystring(self):
        p = self.pedido()
        with mock.patch(CONSULTAR, return_value=self.pago(p)), \
                self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(self.url() + "?type=payment&data.id=1")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.estado(p), E.PAGADO)

    def test_404_y_403_responden_200(self):
        for codigo in (404, 403):
            with self.subTest(codigo=codigo):
                respuesta = self.notificar(side_effect=ErrorDeMercadoPago("x", codigo=codigo))
                self.assertEqual(respuesta.status_code, 200)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.CONECTADA)

    def test_401_pasa_la_cuenta_a_reconectar_y_responde_200(self):
        with mock.patch("notificaciones.services.notificar_cuenta_mp") as push:
            respuesta = self.notificar(side_effect=ErrorDeMercadoPago("x", codigo=401))
        self.assertEqual(respuesta.status_code, 200)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, CuentaMercadoPago.Estado.RECONECTAR)
        push.assert_called_once()

    def test_otro_error_500(self):
        for error in (ErrorDeMercadoPago("timeout"), ErrorDeMercadoPago("x", codigo=502)):
            with self.subTest(error=error):
                self.assertEqual(self.notificar(side_effect=error).status_code, 500)

    def test_carrera_repetida_500(self):
        with mock.patch(
            "tienda.pagos_mp.acreditar_pedido", side_effect=pagos_mp.AcreditacionReintentable
        ):
            respuesta = self.client.post(
                self.url(), data={"type": "payment", "data": {"id": "1"}},
                content_type="application/json",
            )
        self.assertEqual(respuesta.status_code, 500)

    def test_desconectada_hace_menos_de_7_dias_procesa_igual(self):
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).update(
            estado=CuentaMercadoPago.Estado.DESCONECTADA,
            desconectada_en=timezone.now() - timedelta(days=3),
        )
        p = self.pedido(E.VENCIDO)
        respuesta = self.notificar(return_value=self.pago(p))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.estado(p), E.PAGADO)

    def test_solo_post_y_sin_csrf(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        cliente = self.client_class(enforce_csrf_checks=True)
        respuesta = cliente.post(self.url(), data={}, content_type="application/json")
        self.assertEqual(respuesta.status_code, 200)

    def test_no_mira_request_user(self):
        """Logueado como staff de OTRO gimnasio da lo mismo: la cuenta sale
        del uuid de la URL y el pago, de la API."""
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b")
        staff = User.objects.create_user("staff-b", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=otro, rol=Perfil.Rol.STAFF)
        self.client.force_login(staff)
        p = self.pedido()
        respuesta = self.notificar(return_value=self.pago(p))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.estado(p), E.PAGADO)


# --- reconciliar_pedidos -----------------------------------------------------------------


class ReconciliarTests(_Base):
    def correr(self, pagos_por_id, buscar=None, **kwargs):
        def consultar(payment_id, token=None):
            return pagos_por_id[str(payment_id)]

        with mock.patch(CONSULTAR, side_effect=consultar), \
                mock.patch(BUSCAR, side_effect=buscar) as buscar_mock, \
                mock.patch(AL_STAFF), mock.patch(AL_ALUMNO), \
                self.captureOnCommitCallbacks(execute=True):
            resultado = pagos_mp.reconciliar_pedidos(**kwargs)
        self.buscar = buscar_mock
        return resultado

    def test_rechazado_y_despues_aprobado_se_acredita(self):
        """El pedido ya tiene el id del rechazado: filtrar por `payment_id`
        vacío lo perdería."""
        p = self.pedido(E.VENCIDO, payment_id="1", estado_mp="rejected")
        pagos = {"2": self.pago(p, id="P2"), "1": self.pago(p, id="P1", status="rejected")}
        resultado = self.correr(pagos, buscar=lambda ref, token=None: [{"id": 2}, {"id": 1}])
        self.assertEqual(self.estado(p), E.PAGADO)
        p.refresh_from_db()
        self.assertEqual(p.payment_id, "2")
        self.assertEqual(resultado, {"revisados": 1, "errores": 0})
        self.assertEqual(self.buscar.call_args.kwargs["token"].valor, "APP_USR-acceso")

    def test_filtra_por_estado_fecha_link_y_medio(self):
        candidato = self.pedido(E.CANCELADO)
        self.pedido(E.PAGADO, payment_id="7", estado_mp="approved")
        self.pedido(E.PENDIENTE_PAGO, preference_id="")
        self.pedido(medio=Pedido.Medio.TRANSFERENCIA)
        viejo = self.pedido(E.VENCIDO)
        Pedido.objects.filter(pk=viejo.pk).update(creado=timezone.now() - timedelta(days=8))
        refs = []
        self.correr({}, buscar=lambda ref, token=None: refs.append(ref) or [])
        self.assertEqual(refs, [str(candidato.referencia_externa)])

    def test_del_mas_nuevo_al_mas_viejo_con_tope(self):
        viejo = self.pedido()
        Pedido.objects.filter(pk=viejo.pk).update(creado=timezone.now() - timedelta(days=2))
        nuevo = self.pedido()
        refs = []
        self.correr({}, buscar=lambda ref, token=None: refs.append(ref) or [], limite=1)
        self.assertEqual(refs, [str(nuevo.referencia_externa)])

    def test_un_error_se_loguea_y_sigue_y_401_reconectar(self):
        a = self.pedido()
        otro = Gimnasio.objects.create(nombre="Gym B", slug="b", tienda_habilitada=True)
        cuenta_b = self.crear_cuenta(gimnasio=otro, mp_user_id="222", access_token="TOK-B")
        alumno_b = Alumno.objects.create(gimnasio=otro, nombre="B", apellido="B")
        b = Pedido.objects.create(
            gimnasio=otro, alumno=alumno_b, total=Decimal("15000"), medio=Pedido.Medio.MERCADOPAGO,
            reservado_hasta=timezone.now(), preference_id="pref-b",
        )
        Pedido.objects.filter(pk=b.pk).update(creado=timezone.now() + timedelta(seconds=5))

        def buscar(ref, token=None):
            if token.valor == "TOK-B":
                raise ErrorDeMercadoPago("x", codigo=401)
            return [{"id": 1}]

        with mock.patch("notificaciones.services.notificar_cuenta_mp"):
            resultado = self.correr({"1": self.pago(a)}, buscar=buscar)
        self.assertEqual(resultado, {"revisados": 2, "errores": 1})
        self.assertEqual(self.estado(a), E.PAGADO)
        cuenta_b.refresh_from_db()
        self.assertEqual(cuenta_b.estado, CuentaMercadoPago.Estado.RECONECTAR)

    def test_cuenta_sin_tokens_se_saltea(self):
        self.pedido()
        CuentaMercadoPago.objects.filter(pk=self.cuenta.pk).update(access_token="")
        resultado = self.correr({}, buscar=lambda ref, token=None: [])
        self.buscar.assert_not_called()
        self.assertEqual(resultado, {"revisados": 0, "errores": 0})

    def test_las_cuentas_se_leen_en_una_query(self):
        for _ in range(3):
            self.pedido()
        with mock.patch(BUSCAR, return_value=[]), self.assertNumQueries(2):
            pagos_mp.reconciliar_pedidos()


class ReconciliarComandoTests(_Base):
    def correr(self):
        salida = StringIO()
        call_command("reconciliar_pedidos", stdout=salida)
        return salida.getvalue()

    def test_bandera_apagada_sin_cuentas_conectadas_sale_0(self):
        CuentaMercadoPago.objects.update(estado=CuentaMercadoPago.Estado.DESCONECTADA)
        with override_settings(TIENDA_MP_ENABLED=False):
            self.assertIn("apagado", self.correr())

    def test_bandera_apagada_con_una_cuenta_conectada_sale_1(self):
        with override_settings(TIENDA_MP_ENABLED=False), self.assertRaises(CommandError):
            self.correr()

    def test_prendido_reconcilia(self):
        p = self.pedido()
        with mock.patch(BUSCAR, return_value=[{"id": 1}]), \
                mock.patch(CONSULTAR, return_value=self.pago(p)), \
                mock.patch(AL_STAFF), mock.patch(AL_ALUMNO):
            salida = self.correr()
        self.assertIn("Pedidos revisados: 1", salida)
        self.assertEqual(self.estado(p), E.PAGADO)

    def test_el_paso_del_workflow_va_antes_del_ping_con_su_env_completo(self):
        texto = (Path(settings.BASE_DIR) / ".github/workflows/reconciliar-cobros.yml").read_text()
        paso = texto.index("name: Reconciliar pedidos de Mercado Pago de la tienda")
        ping = texto.index("name: Avisar a Healthchecks")
        self.assertLess(paso, ping)
        bloque = texto[paso:ping]
        self.assertIn("if: ${{ !cancelled() }}", bloque)
        self.assertIn("run: python manage.py reconciliar_pedidos", bloque)
        for variable in (
            "DATABASE_URL", "DJANGO_DEBUG", "MERCADOPAGO_CLIENT_ID",
            "MERCADOPAGO_CLIENT_SECRET", "MERCADOPAGO_OAUTH_REDIRECT_URI",
            "MP_TOKEN_ENCRYPTION_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY",
            "VAPID_ADMIN_EMAIL",
        ):
            self.assertIn(f"{variable}: ", bloque)
