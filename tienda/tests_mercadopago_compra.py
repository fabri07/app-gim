"""Compra con Mercado Pago del lado del alumno (B4): medios disponibles con
respaldo, carrito con elección, checkout con preferencia, falla de Mercado
Pago, «Mi pedido», cancelar con el link vigente, staff, vencimiento y retorno.

Sin red: se parchea `suscripcion.mercadopago.crear_preferencia` (y
`tienda.pagos_mp.acreditar_pedido`, que llega en B5)."""

import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from suscripcion.mercadopago import ErrorDeMercadoPago, Secreto
from tenants.models import Gimnasio, Perfil
from tienda import pagos_mp, services
from tienda.models import (
    MARGEN_PREFERENCIA,
    CuentaMercadoPago,
    Pedido,
    Producto,
    Variante,
    obtener_configuracion,
)

CLAVE = Fernet.generate_key().decode()
MP = dict(TIENDA_MP_ENABLED=True, MP_TOKEN_ENCRYPTION_KEY=CLAVE)
E = CuentaMercadoPago.Estado
M = Pedido.Medio
CREAR = "suscripcion.mercadopago.crear_preferencia"
ACREDITAR = "tienda.pagos_mp.acreditar_pedido"
CHECKOUT = "/tienda/carrito/confirmar/"
PREFERENCIA = {"id": "pref-1", "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=pref-1"}


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym A", slug="a", tienda_habilitada=True)
        self.config = obtener_configuracion(self.gimnasio)
        self.config.publicada = True
        self.config.save()
        usuario = User.objects.create_user("alu", password="c-123456", email="ana@example.com")
        perfil = Perfil.objects.create(usuario=usuario, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.alumno = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Ana", apellido="G", perfil=perfil)
        self.usuario = usuario
        self.client.force_login(usuario)
        self.producto = Producto.objects.create(gimnasio=self.gimnasio, nombre="Remera", precio=Decimal("15000"))
        self.variante = Variante.objects.create(producto=self.producto, nombre="M", stock=2)

    def crear_cuenta(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, access_token="APP_USR-acceso",
            refresh_token="REF", mp_user_id="111",
            token_expira=timezone.now() + timedelta(days=100), estado=E.CONECTADA,
        )
        datos.update(extra)
        with override_settings(**MP):
            return CuentaMercadoPago.objects.create(**datos)

    def aceptar_mp(self, transferencia=True):
        self.config.acepta_mercadopago = True
        self.config.acepta_transferencia = transferencia
        self.config.save()

    def agregar(self, cantidad=1):
        return self.client.post(reverse("tienda:carrito"), {
            "accion": "agregar", "variante": self.variante.pk, "cantidad": cantidad,
        })

    def pedido_mp(self, **extra):
        ahora = timezone.now()
        datos = dict(
            gimnasio=self.gimnasio, alumno=self.alumno, total=Decimal("15000"), medio=M.MERCADOPAGO,
            reservado_hasta=ahora + timedelta(minutes=30), preference_id="pref-1",
            init_point=PREFERENCIA["init_point"],
        )
        datos.update(extra)
        pedido = Pedido.objects.create(**datos)
        pedido.items.create(variante=self.variante, nombre="Remera · M", precio_unitario=Decimal("15000"), cantidad=1)
        return pedido


# --- medios_disponibles -----------------------------------------------------


class MediosDisponiblesTests(_Base):
    def medios(self):
        return services.medios_disponibles(self.gimnasio, obtener_configuracion(self.gimnasio))

    def test_bandera_apagada_solo_transferencia_aunque_haya_cuenta(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.assertEqual(self.medios(), [M.TRANSFERENCIA])

    @override_settings(**MP)
    def test_con_cuenta_conectada_y_los_dos_medios(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.assertEqual(self.medios(), [M.TRANSFERENCIA, M.MERCADOPAGO])

    @override_settings(**MP)
    def test_solo_mercadopago(self):
        self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        self.assertEqual(self.medios(), [M.MERCADOPAGO])

    @override_settings(**MP)
    def test_respaldo_con_la_cuenta_a_reconectar(self):
        """Una cuenta en RECONECTAR nunca deja la tienda sin poder vender."""
        self.crear_cuenta(estado=E.RECONECTAR)
        self.aceptar_mp(transferencia=False)
        self.assertEqual(self.medios(), [M.TRANSFERENCIA])

    @override_settings(**MP)
    def test_sin_cuenta_o_con_la_de_otro_gimnasio(self):
        otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.crear_cuenta(gimnasio=otro)
        self.aceptar_mp(transferencia=False)
        self.assertEqual(self.medios(), [M.TRANSFERENCIA])

    @override_settings(**MP)
    def test_demo_solo_transferencia(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.gimnasio.refresh_from_db()
        self.crear_cuenta()
        self.aceptar_mp()
        self.assertEqual(self.medios(), [M.TRANSFERENCIA])

    @override_settings(**MP)
    def test_sin_aceptar_mercadopago(self):
        self.crear_cuenta()
        self.assertEqual(self.medios(), [M.TRANSFERENCIA])


# --- crear_pedido(medio) ------------------------------------------------------


class CrearPedidoMedioTests(_Base):
    def crear(self, medio, ahora=None):
        return services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno, carrito={self.variante.pk: 1},
            suplantando=False, medio=medio, ahora=ahora,
        )

    def test_medio_no_disponible_no_crea_ni_reserva(self):
        with self.assertRaises(services.ErrorDeTienda) as e:
            self.crear(M.MERCADOPAGO)
        self.assertEqual(e.exception.motivo, "medio_no_disponible")
        self.assertFalse(Pedido.objects.exists())
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)

    def test_medio_basura(self):
        with self.assertRaises(services.ErrorDeTienda) as e:
            self.crear("bitcoin")
        self.assertEqual(e.exception.motivo, "medio_no_disponible")

    @override_settings(**MP)
    def test_transferencia_cuando_solo_se_acepta_mercadopago(self):
        self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        with self.assertRaises(services.ErrorDeTienda) as e:
            self.crear(M.TRANSFERENCIA)
        self.assertEqual(e.exception.motivo, "medio_no_disponible")

    @override_settings(**MP)
    def test_mercadopago_reserva_por_minutos(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.config.minutos_reserva_mp = 45
        self.config.save()
        ahora = timezone.now()
        pedido = self.crear(M.MERCADOPAGO, ahora=ahora)
        self.assertEqual(pedido.medio, M.MERCADOPAGO)
        self.assertEqual(pedido.reservado_hasta, ahora + timedelta(minutes=45))
        self.assertIsNotNone(pedido.referencia_externa)

    def test_por_defecto_es_transferencia_como_hoy(self):
        ahora = timezone.now()
        pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno, carrito={self.variante.pk: 1},
            suplantando=False, ahora=ahora,
        )
        self.assertEqual(pedido.medio, M.TRANSFERENCIA)
        self.assertEqual(pedido.reservado_hasta, ahora + timedelta(hours=24))


class TopeConMercadoPagoTests(_Base):
    """El tope cuenta un pedido MP sólo mientras su link sigue pagable."""

    def setUp(self):
        super().setUp()
        self.config.max_pedidos_activos = 1
        self.config.save()

    def crear(self):
        return services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno, carrito={self.variante.pk: 1}, suplantando=False,
        )

    def test_mp_con_el_link_vigente_cuenta(self):
        self.pedido_mp()
        with self.assertRaises(services.ErrorDeTienda) as e:
            self.crear()
        self.assertEqual(e.exception.motivo, "tope_pedidos")
        self.assertNotIn("cancelalos", e.exception.mensaje)

    def test_mp_con_el_link_vencido_no_cuenta(self):
        # Pasó `vence_preferencia` pero no la reserva: el alumno ya no puede pagar.
        self.pedido_mp(reservado_hasta=timezone.now() + MARGEN_PREFERENCIA - timedelta(minutes=1))
        self.assertEqual(self.crear().medio, M.TRANSFERENCIA)


# --- cancelar con el link vigente --------------------------------------------


class CancelarConPreferenciaTests(_Base):
    def test_con_el_link_vigente_no_se_cancela(self):
        pedido = self.pedido_mp()
        with self.assertRaises(services.ErrorDeTienda) as e:
            services.cancelar_por_alumno(pedido, alumno=self.alumno)
        self.assertEqual(e.exception.motivo, "pago_en_curso")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_sin_link_se_cancela_ya(self):
        pedido = self.pedido_mp(preference_id="", init_point="")
        services.cancelar_por_alumno(pedido, alumno=self.alumno)
        self.assertEqual(pedido.estado, Pedido.Estado.CANCELADO)

    def test_pasado_el_vencimiento_del_link_se_cancela(self):
        pedido = self.pedido_mp(reservado_hasta=timezone.now() + timedelta(minutes=5))
        services.cancelar_por_alumno(pedido, alumno=self.alumno)
        self.assertEqual(pedido.estado, Pedido.Estado.CANCELADO)

    def test_la_vista_muestra_el_mensaje(self):
        pedido = self.pedido_mp()
        r = self.client.post(reverse("tienda:mi_pedido_cancelar", args=[pedido.pk]), follow=True)
        self.assertContains(r, "Mercado Pago")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)


# --- vencimiento ---------------------------------------------------------------


class VencerPedidoMPTests(_Base):
    def test_vence_perezoso_sin_red(self):
        pedido = self.pedido_mp(reservado_hasta=timezone.now() - timedelta(hours=1))
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)
        prohibido = mock.Mock(side_effect=AssertionError("no puede salir a la red"))
        with mock.patch(CREAR, prohibido), \
                mock.patch("suscripcion.mercadopago.consultar_pago", prohibido), \
                mock.patch("suscripcion.mercadopago.buscar_pagos", prohibido), \
                mock.patch("notificaciones.services.notificar_a_usuario", prohibido), \
                mock.patch("notificaciones.services.notificar_a_gimnasio", prohibido):
            self.client.get(reverse("tienda:mis_pedidos"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.VENCIDO)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)

    @mock.patch("notificaciones.services.notificar_a_usuario")
    def test_checkout_abandonado_vence_sin_aviso(self, push):
        self.pedido_mp(reservado_hasta=timezone.now() - timedelta(hours=1))
        call_command("vencer_pedidos", stdout=StringIO())
        push.assert_not_called()

    @mock.patch("notificaciones.services.notificar_a_usuario")
    def test_con_un_pago_registrado_si_avisa(self, push):
        # P. ej. un rechazado: el alumno intentó pagar y tiene que enterarse.
        self.pedido_mp(reservado_hasta=timezone.now() - timedelta(hours=1), payment_id="999",
                       estado_mp="rejected")
        call_command("vencer_pedidos", stdout=StringIO())
        self.assertEqual(push.call_count, 1)


# --- carrito -----------------------------------------------------------------------


class CarritoTests(_Base):
    def test_bandera_apagada_el_carrito_es_el_de_hoy(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertContains(r, "24 h para transferir")
        self.assertNotContains(r, 'type="radio" name="medio"')  # sin elección (el oculto es de la Ronda 1)
        self.assertNotContains(r, "Mercado Pago")
        self.assertContains(r, f'<form method="post" action="{CHECKOUT}">')

    @override_settings(**MP)
    def test_un_solo_medio_transferencia_es_el_de_hoy(self):
        self.crear_cuenta()
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertContains(r, "24 h para transferir")
        self.assertNotContains(r, 'type="radio" name="medio"')  # sin elección (el oculto es de la Ronda 1)
        self.assertNotContains(r, "Mercado Pago")
        self.assertContains(r, f'<form method="post" action="{CHECKOUT}">')

    @override_settings(**MP)
    def test_dos_medios_muestra_la_eleccion(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertContains(r, 'name="medio" value="transferencia"')
        self.assertContains(r, 'name="medio" value="mercadopago"')
        self.assertContains(r, "<fieldset")
        self.assertContains(r, f'action="{CHECKOUT}" hx-boost="false"')

    @override_settings(**MP)
    def test_solo_mercadopago_sin_texto_de_horas(self):
        self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertNotContains(r, "h para transferir")
        self.assertNotContains(r, 'type="radio" name="medio"')  # sin elección (el oculto es de la Ronda 1)
        self.assertContains(r, "Mercado Pago")
        self.assertContains(r, f'action="{CHECKOUT}" hx-boost="false"')


# --- checkout ------------------------------------------------------------------------


@override_settings(**MP)
class CheckoutMPTests(_Base):
    def setUp(self):
        super().setUp()
        self.cuenta = self.crear_cuenta(es_de_prueba=True)
        self.aceptar_mp()
        self.agregar(2)

    def checkout(self, medio="mercadopago", **kwargs):
        return self.client.post(reverse("tienda:checkout"), {"medio": medio}, **kwargs)

    def test_feliz_redirige_a_mercadopago(self):
        with mock.patch(CREAR, return_value=PREFERENCIA) as crear, \
                mock.patch("notificaciones.services.notificar_pedido_al_staff") as push, \
                self.captureOnCommitCallbacks(execute=True):
            r = self.checkout()
        pedido = Pedido.objects.get()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], PREFERENCIA["init_point"])
        self.assertEqual(pedido.medio, M.MERCADOPAGO)
        self.assertEqual(pedido.preference_id, "pref-1")
        self.assertEqual(pedido.init_point, PREFERENCIA["init_point"])
        # Sin push «Nuevo pedido»: llegaría por cada intento abandonado.
        push.assert_not_called()
        kw = crear.call_args.kwargs
        self.assertIsInstance(kw["token"], Secreto)
        self.assertEqual(kw["token"].valor, "APP_USR-acceso")
        self.assertIs(kw["sandbox"], True)
        self.assertIsNone(kw["statement_descriptor"])
        self.assertEqual(kw["expira"], pedido.vence_preferencia)
        self.assertEqual(kw["monto_ars"], Decimal("30000"))
        self.assertEqual(kw["referencia_externa"], str(pedido.referencia_externa))
        self.assertEqual(kw["titulo"], f"Pedido #{pedido.pk} — Gym A")
        self.assertTrue(kw["url_retorno"].startswith("http"))
        self.assertIn(str(pedido.referencia_externa), kw["url_retorno"])
        self.assertTrue(kw["url_notificacion"].startswith("http"))
        self.assertIn(str(self.cuenta.uuid_webhook), kw["url_notificacion"])
        # El carrito quedó vacío.
        self.assertNotContains(self.client.get(reverse("tienda:carrito")), "30.000")

    def test_eligiendo_transferencia_es_el_flujo_de_hoy(self):
        with mock.patch(CREAR) as crear, \
                mock.patch("notificaciones.services.notificar_pedido_al_staff") as push, \
                self.captureOnCommitCallbacks(execute=True):
            r = self.checkout("transferencia")
        pedido = Pedido.objects.get()
        crear.assert_not_called()
        self.assertRedirects(r, reverse("tienda:mi_pedido", args=[pedido.pk]), fetch_redirect_response=False)
        self.assertEqual(pedido.medio, M.TRANSFERENCIA)
        push.assert_called_once()

    def test_falla_de_mp_borra_el_pedido_y_devuelve_el_stock(self):
        with mock.patch(CREAR, side_effect=ErrorDeMercadoPago("HTTP 500", codigo=500)):
            r = self.checkout(follow=True)
        self.assertFalse(Pedido.objects.exists())
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)
        self.assertContains(r, "No pudimos conectar con Mercado Pago")
        self.assertContains(r, "elegí transferencia")
        # El carrito quedó intacto.
        self.assertContains(self.client.get(reverse("tienda:carrito")), "30.000")
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, E.CONECTADA)

    def test_falla_sin_transferencia_no_la_ofrece(self):
        self.aceptar_mp(transferencia=False)
        with mock.patch(CREAR, side_effect=ErrorDeMercadoPago("HTTP 500", codigo=500)):
            r = self.checkout(follow=True)
        self.assertContains(r, "No pudimos conectar con Mercado Pago")
        self.assertNotContains(r, "elegí transferencia")

    def test_401_pasa_la_cuenta_a_reconectar(self):
        self.aceptar_mp(transferencia=False)
        with mock.patch(CREAR, side_effect=ErrorDeMercadoPago("HTTP 401", codigo=401)), \
                mock.patch("notificaciones.services.notificar_a_gimnasio"):
            r = self.checkout(follow=True)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, E.RECONECTAR)
        self.assertFalse(Pedido.objects.exists())
        # Respaldo: sin MP, la transferencia vuelve a estar disponible.
        self.assertContains(r, "elegí transferencia")

    def test_token_ilegible_no_llama_a_mp(self):
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()), \
                mock.patch(CREAR) as crear, \
                mock.patch("notificaciones.services.notificar_a_gimnasio"):
            self.checkout(follow=True)
        crear.assert_not_called()
        self.assertFalse(Pedido.objects.exists())
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, E.RECONECTAR)

    def test_medio_basura_no_crea_pedido(self):
        with mock.patch(CREAR) as crear:
            r = self.checkout("bitcoin", follow=True)
        crear.assert_not_called()
        self.assertFalse(Pedido.objects.exists())
        self.assertContains(r, "Elegí cómo pagar")

    def test_sin_medio_con_dos_disponibles_pide_elegir(self):
        with mock.patch(CREAR) as crear:
            r = self.client.post(reverse("tienda:checkout"), follow=True)
        crear.assert_not_called()
        self.assertFalse(Pedido.objects.exists())
        self.assertContains(r, "Elegí cómo pagar")

    def test_sin_medio_con_uno_solo_usa_ese(self):
        self.aceptar_mp(transferencia=False)
        with mock.patch(CREAR, return_value=PREFERENCIA):
            r = self.client.post(reverse("tienda:checkout"))
        self.assertEqual(r["Location"], PREFERENCIA["init_point"])
        self.assertEqual(Pedido.objects.get().medio, M.MERCADOPAGO)


@override_settings(**MP)
class CrearLinkTests(_Base):
    def test_referencia_nula_se_genera_antes_del_link(self):
        """Pedidos del deploy viejo pueden tener `referencia_externa` NULL."""
        cuenta = self.crear_cuenta()
        pedido = self.pedido_mp(preference_id="", init_point="")
        Pedido.objects.filter(pk=pedido.pk).update(referencia_externa=None)
        pedido.refresh_from_db()
        with mock.patch(CREAR, return_value=PREFERENCIA) as crear:
            pagos_mp.crear_link_de_pago(
                pedido, url_retorno_para=lambda ref: f"https://x/{ref}/",
                url_notificacion_para=lambda u: f"https://x/w/{u}/",
            )
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.referencia_externa)
        self.assertEqual(crear.call_args.kwargs["referencia_externa"], str(pedido.referencia_externa))
        self.assertEqual(crear.call_args.kwargs["url_notificacion"], f"https://x/w/{cuenta.uuid_webhook}/")

    def test_si_el_staff_lo_cancelo_en_el_medio_no_devuelve_dos_veces(self):
        self.crear_cuenta()
        pedido = self.pedido_mp(preference_id="", init_point="")
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)
        services.cancelar_por_staff(pedido, motivo="")  # devuelve 1 -> stock 2

        def cancelado_y_falla(**kwargs):
            raise ErrorDeMercadoPago("HTTP 500", codigo=500)

        with mock.patch(CREAR, side_effect=cancelado_y_falla), self.assertRaises(services.ErrorDeTienda):
            pagos_mp.crear_link_de_pago(
                pedido, url_retorno_para=lambda ref: "https://x/", url_notificacion_para=lambda u: "https://x/",
            )
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)
        self.assertTrue(Pedido.objects.filter(pk=pedido.pk).exists())


# --- Mi pedido -----------------------------------------------------------------------


class MiPedidoMPTests(_Base):
    def ver(self, pedido):
        return self.client.get(reverse("tienda:mi_pedido", args=[pedido.pk]))

    def test_pendiente_con_link_vigente(self):
        pedido = self.pedido_mp()
        r = self.ver(pedido)
        self.assertContains(r, "Pagar con Mercado Pago")
        self.assertContains(r, f'href="{PREFERENCIA["init_point"]}" hx-boost="false"')
        hora = timezone.localtime(pedido.vence_preferencia).strftime("%H:%M")
        self.assertContains(r, f"antes de las {hora}")
        self.assertNotContains(r, "Subir comprobante")
        self.assertNotContains(r, "Alias:")
        self.assertNotContains(r, "Cancelar pedido")

    def test_pendiente_sin_link_solo_cancelar(self):
        pedido = self.pedido_mp(preference_id="", init_point="")
        r = self.ver(pedido)
        self.assertNotContains(r, "Pagar con Mercado Pago")
        self.assertNotContains(r, "Subir comprobante")
        self.assertContains(r, "Cancelar pedido")

    def test_pendiente_con_el_link_vencido(self):
        pedido = self.pedido_mp(reservado_hasta=timezone.now() + timedelta(minutes=5))
        r = self.ver(pedido)
        self.assertNotContains(r, "Pagar con Mercado Pago")
        self.assertContains(r, "Cancelar pedido")

    def test_revisar(self):
        pedido = self.pedido_mp(estado=Pedido.Estado.REVISAR)
        self.assertContains(self.ver(pedido), "Recibimos tu pago; el gimnasio te va a contactar")

    def test_devuelto_por_mercadopago(self):
        pedido = self.pedido_mp(estado=Pedido.Estado.DEVUELTO, payment_id="1", estado_mp="refunded")
        self.assertContains(self.ver(pedido), "Mercado Pago te devolvió el pago")

    def test_pagado_como_hoy(self):
        pedido = self.pedido_mp(estado=Pedido.Estado.PAGADO, payment_id="1", estado_mp="approved")
        self.assertContains(self.ver(pedido), "Retiralo en el gimnasio")

    def test_la_transferencia_sigue_igual(self):
        pedido = Pedido.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno, total=1, reservado_hasta=timezone.now() + timedelta(hours=2)
        )
        r = self.ver(pedido)
        self.assertContains(r, "Subir comprobante")
        self.assertNotContains(r, "Pagar con Mercado Pago")


# --- Tarjeta del inicio del alumno --------------------------------------------------


class TarjetaDelInicioTests(_Base):
    def inicio(self):
        return self.client.get(reverse("home"))

    def test_bandera_apagada_dice_transferencia_sin_consultar_la_config(self):
        self.crear_cuenta()
        self.aceptar_mp()
        con_bandera = override_settings(TIENDA_MP_ENABLED=False)
        with con_bandera:
            self.inicio()  # calienta cachés de la sesión
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as queries:
                r = self.inicio()
        self.assertContains(r, "Pagás por transferencia y retirás acá.")
        sqls = " ".join(q["sql"] for q in queries.captured_queries)
        self.assertNotIn("tienda_cuentamercadopago", sqls)
        self.assertNotIn("acepta_transferencia", sqls)

    @override_settings(**MP)
    def test_con_los_dos_medios(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.assertContains(self.inicio(), "Pagás por transferencia o con Mercado Pago y retirás acá.")

    @override_settings(**MP)
    def test_solo_mercado_pago(self):
        self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        r = self.inicio()
        self.assertContains(r, "Pagás con Mercado Pago y retirás acá.")
        self.assertNotContains(r, "transferencia")

    @override_settings(**MP)
    def test_cuenta_en_reconectar_vuelve_a_transferencia(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        self.aceptar_mp(transferencia=False)
        self.assertContains(self.inicio(), "Pagás por transferencia y retirás acá.")


# --- Staff ---------------------------------------------------------------------------


class StaffDetalleMPTests(_Base):
    def setUp(self):
        super().setUp()
        staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(staff)

    def ver(self, pedido):
        return self.client.get(reverse("tienda:pedido_detalle", args=[pedido.pk]))

    def test_con_el_link_vigente_no_hay_marcar_pagado_ni_cancelar(self):
        r = self.ver(self.pedido_mp())
        self.assertNotContains(r, "Marcar pagado")
        self.assertNotContains(r, 'value="cancelar"')
        self.assertContains(r, "Mercado Pago")

    def test_pasado_el_link_vuelven(self):
        r = self.ver(self.pedido_mp(reservado_hasta=timezone.now() + timedelta(minutes=5)))
        self.assertContains(r, "Marcar pagado")
        self.assertContains(r, 'value="cancelar"')

    def test_el_estado_de_mp_se_muestra_en_castellano(self):
        r = self.ver(self.pedido_mp(payment_id="999", estado_mp="approved"))
        self.assertContains(r, "999 (aprobado)")
        self.assertNotContains(r, "approved")

    def test_estado_de_mp_legible(self):
        casos = {
            "approved": "aprobado", "rejected": "rechazado", "refunded": "devuelto",
            "charged_back": "contracargo", "cancelled": "cancelado",
            "in_process": "en proceso", "pending": "pendiente",
            "algo_nuevo": "algo_nuevo", "": "",
        }
        for crudo, legible in casos.items():
            with self.subTest(crudo=crudo):
                self.assertEqual(Pedido(estado_mp=crudo).estado_mp_legible, legible)

    def test_sin_link_vuelven(self):
        r = self.ver(self.pedido_mp(preference_id="", init_point=""))
        self.assertContains(r, "Marcar pagado")


# --- Retorno -------------------------------------------------------------------------


@override_settings(**MP)
class RetornoTests(_Base):
    def setUp(self):
        super().setUp()
        self.cuenta = self.crear_cuenta()
        self.pedido = self.pedido_mp()

    def url(self, pedido=None):
        return reverse("tienda:mp_retorno", args=[(pedido or self.pedido).referencia_externa])

    def test_acredita_con_payment_id_y_vuelve_al_pedido(self):
        with mock.patch(ACREDITAR) as acreditar:
            r = self.client.get(self.url(), {"payment_id": "123", "status": "approved"})
        self.assertRedirects(r, reverse("tienda:mi_pedido", args=[self.pedido.pk]), fetch_redirect_response=False)
        acreditar.assert_called_once()
        cuenta, payment_id = acreditar.call_args.args
        self.assertEqual(cuenta.pk, self.cuenta.pk)
        self.assertEqual(payment_id, "123")
        self.assertIn("no-cache", r.get("Cache-Control", ""))

    def test_usa_collection_id_si_no_hay_payment_id(self):
        with mock.patch(ACREDITAR) as acreditar:
            self.client.get(self.url(), {"payment_id": "null", "collection_id": "456"})
        self.assertEqual(acreditar.call_args.args[1], "456")

    def test_null_no_acredita(self):
        with mock.patch(ACREDITAR) as acreditar:
            r = self.client.get(self.url(), {"payment_id": "null", "collection_id": "null"})
        acreditar.assert_not_called()
        self.assertEqual(r.status_code, 302)

    def test_error_al_acreditar_es_un_cartel(self):
        with mock.patch(ACREDITAR, side_effect=ErrorDeMercadoPago("HTTP 500", codigo=500)):
            r = self.client.get(self.url(), {"payment_id": "123"}, follow=True)
        self.assertContains(r, "Estamos confirmando tu pago")

    def test_pedido_de_otro_alumno_es_404(self):
        otro = Alumno.objects.create(gimnasio=self.gimnasio, nombre="Eva", apellido="R")
        ajeno = self.pedido_mp(alumno=otro, preference_id="pref-2")
        with mock.patch(ACREDITAR) as acreditar:
            r = self.client.get(self.url(ajeno), {"payment_id": "123"})
        self.assertEqual(r.status_code, 404)
        acreditar.assert_not_called()

    def test_referencia_inexistente_es_404(self):
        r = self.client.get(reverse("tienda:mp_retorno", args=[uuid.uuid4()]))
        self.assertEqual(r.status_code, 404)

    def test_bandera_apagada_no_acredita(self):
        with override_settings(TIENDA_MP_ENABLED=False), mock.patch(ACREDITAR) as acreditar:
            r = self.client.get(self.url(), {"payment_id": "123"})
        acreditar.assert_not_called()
        self.assertEqual(r.status_code, 302)


# --- Ronda 1 de endurecimiento ---------------------------------------------------


class StaffAccionConPreferenciaTests(_Base):
    """Cinturón del lado del servidor: un POST desde una pantalla vieja no
    marca pagado ni cancela un pedido con el link de MP pagable."""

    def setUp(self):
        super().setUp()
        staff = User.objects.create_user("staff", password="c-123456")
        Perfil.objects.create(usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(staff)

    def accion(self, pedido, accion):
        return self.client.post(
            reverse("tienda:pedido_accion", args=[pedido.pk]), {"accion": accion}, follow=True
        )

    def test_marcar_pagado_con_el_link_vigente_se_rechaza(self):
        pedido = self.pedido_mp()
        r = self.accion(pedido, "pagado")
        self.assertContains(r, "pago con Mercado Pago está en curso")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)

    def test_cancelar_con_el_link_vigente_se_rechaza(self):
        pedido = self.pedido_mp()
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)
        self.accion(pedido, "cancelar")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PENDIENTE_PAGO)
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 1)

    def test_pasado_el_link_se_puede(self):
        pedido = self.pedido_mp(reservado_hasta=timezone.now() + timedelta(minutes=5))
        self.accion(pedido, "pagado")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.PAGADO)

    def test_servicios_directos(self):
        pedido = self.pedido_mp()
        for llamada in (lambda: services.marcar_pagado(pedido),
                        lambda: services.cancelar_por_staff(pedido, motivo="")):
            with self.assertRaises(services.ErrorDeTienda) as e:
                llamada()
            self.assertEqual(e.exception.motivo, "pago_en_curso")


class TopeSinLinkTests(_Base):
    def test_mp_sin_link_no_cuenta_para_el_tope(self):
        """Coincide con `preferencia_vigente`: sin link no hay pago en curso."""
        self.config.max_pedidos_activos = 1
        self.config.save()
        self.pedido_mp(preference_id="", init_point="")
        pedido = services.crear_pedido(
            gimnasio=self.gimnasio, alumno=self.alumno, carrito={self.variante.pk: 1}, suplantando=False,
        )
        self.assertEqual(pedido.medio, M.TRANSFERENCIA)


class CarritoEndurecidoTests(_Base):
    def test_un_solo_medio_manda_el_medio_oculto(self):
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertContains(r, '<input type="hidden" name="medio" value="transferencia">')

    @override_settings(**MP)
    def test_solo_mp_manda_el_medio_oculto_y_se_deshabilita_al_enviar(self):
        self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        self.agregar()
        r = self.client.get(reverse("tienda:carrito"))
        self.assertContains(r, '<input type="hidden" name="medio" value="mercadopago">')
        self.assertContains(r, ':disabled="enviando"')

    @override_settings(**MP)
    def test_dos_medios_se_deshabilita_al_enviar(self):
        self.crear_cuenta()
        self.aceptar_mp()
        self.agregar()
        self.assertContains(self.client.get(reverse("tienda:carrito")), ':disabled="enviando"')

    @override_settings(**MP)
    def test_el_medio_oculto_que_ya_no_esta_disponible_no_elige_otro(self):
        """El carrito se mostró con sólo MP y la cuenta se cayó antes de
        confirmar: nunca se arma un pedido por transferencia en silencio."""
        cuenta = self.crear_cuenta()
        self.aceptar_mp(transferencia=False)
        self.agregar()
        CuentaMercadoPago.objects.filter(pk=cuenta.pk).update(estado=E.RECONECTAR)
        with mock.patch(CREAR) as crear:
            r = self.client.post(reverse("tienda:checkout"), {"medio": "mercadopago"}, follow=True)
        crear.assert_not_called()
        self.assertFalse(Pedido.objects.exists())
        self.assertContains(r, "Elegí cómo pagar")


@override_settings(**MP)
class CarreraMPOkStaffCancelaTests(_Base):
    def test_mp_responde_ok_pero_el_staff_cancelo_en_el_medio(self):
        self.crear_cuenta()
        pedido = self.pedido_mp(preference_id="", init_point="")
        Variante.objects.filter(pk=self.variante.pk).update(stock=1)

        def staff_cancela_y_mp_ok(**kwargs):
            services.cancelar_por_staff(Pedido.objects.get(pk=pedido.pk), motivo="")  # stock -> 2
            return PREFERENCIA

        with mock.patch(CREAR, side_effect=staff_cancela_y_mp_ok), \
                self.assertRaises(services.ErrorDeTienda) as e:
            pagos_mp.crear_link_de_pago(
                pedido, url_retorno_para=lambda ref: "https://x/", url_notificacion_para=lambda u: "https://x/",
            )
        self.assertEqual(e.exception.motivo, "estado_invalido")
        self.variante.refresh_from_db()
        self.assertEqual(self.variante.stock, 2)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.CANCELADO)
        self.assertEqual(pedido.preference_id, "")
