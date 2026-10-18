"""Conectar / desconectar / refrescar la cuenta de Mercado Pago del gimnasio
(B3): configuración, OAuth con PKCE, refresco de tokens, comando.

Sin red: se parchean las funciones de `tienda.mercadopago_oauth`."""

import base64
import hashlib
from datetime import timedelta
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from suscripcion.mercadopago import ErrorDeMercadoPago
from tenants.models import Gimnasio, Perfil
from tienda import cuenta_mp
from tienda.forms import ConfiguracionTiendaForm
from tienda.models import (
    ConfiguracionTienda,
    CuentaMercadoPago,
    Pedido,
    obtener_configuracion,
)

CLAVE = Fernet.generate_key().decode()
MP = dict(
    TIENDA_MP_ENABLED=True,
    MP_TOKEN_ENCRYPTION_KEY=CLAVE,
    MERCADOPAGO_CLIENT_ID="cid-123",
    MERCADOPAGO_CLIENT_SECRET="csecret",
    MERCADOPAGO_OAUTH_REDIRECT_URI="https://www.tugimapp.com/tienda/mercadopago/callback/",
)
E = CuentaMercadoPago.Estado
CANJE = "tienda.mercadopago_oauth.canjear_codigo"
REFRESCO = "tienda.mercadopago_oauth.refrescar"
PUSH = "notificaciones.services.notificar_a_gimnasio"


def tokens(user_id=111, live_mode=True, refresh="REF-nuevo", **extra):
    datos = {
        "access_token": "APP_USR-acceso-nuevo",
        "refresh_token": refresh,
        "expires_in": 15552000,
        "user_id": user_id,
        "live_mode": live_mode,
    }
    datos.update(extra)
    if refresh is None:
        del datos["refresh_token"]
    return datos


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a", tienda_habilitada=True)
        self.otro = Gimnasio.objects.create(nombre="B", slug="b", tienda_habilitada=True)
        self.staff = User.objects.create_user("staff", password="c-123456", first_name="Ana")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)
        self.client.force_login(self.staff)

    def crear_cuenta(self, gimnasio=None, **extra):
        datos = dict(
            gimnasio=gimnasio or self.gimnasio, access_token="APP_USR-viejo",
            refresh_token="REF-viejo", mp_user_id="111",
            token_expira=timezone.now() + timedelta(days=10), estado=E.CONECTADA,
        )
        datos.update(extra)
        with override_settings(**MP):
            return CuentaMercadoPago.objects.create(**datos)

    def crear_pedido(self, gimnasio=None, **extra):
        gimnasio = gimnasio or self.gimnasio
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Eva", apellido="R")
        datos = dict(gimnasio=gimnasio, alumno=alumno, total=1000)
        datos.update(extra)
        return Pedido.objects.create(**datos)


# --- Form y tarjeta ---------------------------------------------------------


class BanderaApagadaTests(_Base):
    """Apagada (el estado de la suite y de producción hoy) la configuración es
    exactamente la de siempre."""

    def test_el_form_no_tiene_los_campos_nuevos(self):
        form = ConfiguracionTiendaForm(gimnasio=self.gimnasio, instance=obtener_configuracion(self.gimnasio))
        for nombre in ConfiguracionTiendaForm.CAMPOS_MP:
            self.assertNotIn(nombre, form.fields)

    def test_el_html_no_menciona_mercado_pago_ni_los_campos(self):
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertNotContains(r, "acepta_")
        self.assertNotContains(r, "minutos_reserva_mp")
        self.assertNotContains(r, "Mercado Pago")
        self.assertNotContains(r, reverse("tienda:mp_conectar"))

    def test_guardar_no_toca_los_campos_nuevos(self):
        self.client.post(reverse("tienda:configuracion"), {
            "horas_reserva_transferencia": "48", "max_pedidos_activos": "3",
            "acepta_mercadopago": "on", "acepta_transferencia": "",
        })
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.acepta_transferencia, c.acepta_mercadopago), (True, False))

    def test_no_carga_instancias_de_la_cuenta(self):
        self.crear_cuenta()
        with mock.patch("tienda.views.CuentaMercadoPago") as modelo:
            self.client.get(reverse("tienda:configuracion"))
        modelo.objects.for_gimnasio.assert_not_called()

    def test_las_vistas_de_conexion_avisan_y_no_hacen_nada(self):
        r = self.client.get(reverse("tienda:mp_conectar"), follow=True)
        self.assertRedirects(r, reverse("tienda:configuracion"))
        self.assertContains(r, "no está disponible")
        self.assertNotIn("tienda_mp_nonce", self.client.session)
        self.crear_cuenta()
        self.client.post(reverse("tienda:mp_desconectar"))
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)


@override_settings(**MP)
class FormYTarjetaTests(_Base):
    def guardar(self, **extra):
        datos = {"horas_reserva_transferencia": "24", "max_pedidos_activos": "2",
                 "minutos_reserva_mp": "30", "acepta_transferencia": "on"}
        datos.update(extra)
        datos = {k: v for k, v in datos.items() if v is not None}
        return self.client.post(reverse("tienda:configuracion"), datos)

    def test_con_la_bandera_el_form_tiene_los_tres_campos(self):
        r = self.client.get(reverse("tienda:configuracion"))
        for nombre in ConfiguracionTiendaForm.CAMPOS_MP:
            self.assertContains(r, f'name="{nombre}"')

    def test_prender_mp_sin_cuenta_es_error_visible(self):
        r = self.guardar(acepta_mercadopago="on")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Primero conectá tu cuenta de Mercado Pago.")
        self.assertContains(r, "config-error")
        self.assertFalse(obtener_configuracion(self.gimnasio).acepta_mercadopago)

    def test_prender_mp_con_cuenta_en_reconectar_es_error(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        r = self.guardar(acepta_mercadopago="on")
        self.assertContains(r, "Primero conectá tu cuenta")

    def test_prender_mp_con_cuenta_conectada_guarda(self):
        self.crear_cuenta()
        r = self.guardar(acepta_mercadopago="on", minutos_reserva_mp="45")
        self.assertRedirects(r, reverse("tienda:configuracion"))
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.acepta_mercadopago, c.minutos_reserva_mp), (True, 45))

    def test_cuenta_de_otro_gimnasio_no_sirve(self):
        self.crear_cuenta(gimnasio=self.otro)
        r = self.guardar(acepta_mercadopago="on")
        self.assertContains(r, "Primero conectá tu cuenta")

    def test_sin_ningun_medio_es_error(self):
        r = self.guardar(acepta_transferencia=None)
        self.assertContains(r, "Elegí al menos un medio de pago.")
        self.assertTrue(obtener_configuracion(self.gimnasio).acepta_transferencia)

    def test_ya_prendido_y_en_reconectar_guardar_otro_campo_no_se_bloquea(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"acepta_mercadopago": True}
        )
        r = self.guardar(acepta_mercadopago="on", max_pedidos_activos="4")
        self.assertRedirects(r, reverse("tienda:configuracion"))
        self.assertEqual(obtener_configuracion(self.gimnasio).max_pedidos_activos, 4)

    def test_solo_mp_sin_transferencia_con_cuenta_conectada(self):
        self.crear_cuenta()
        r = self.guardar(acepta_mercadopago="on", acepta_transferencia=None)
        self.assertRedirects(r, reverse("tienda:configuracion"))

    def test_minutos_fuera_de_rango_es_error_visible(self):
        r = self.guardar(minutos_reserva_mp="10")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "config-error")

    def test_demo_no_tiene_campos_ni_boton(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertNotContains(r, "acepta_")
        self.assertContains(r, "En la cuenta de demostración no se conecta Mercado Pago")
        self.assertNotContains(r, reverse("tienda:mp_conectar"))

    def test_sin_cuenta_el_boton_conectar_lleva_hx_boost_false(self):
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertContains(r, "Conectar Mercado Pago")
        self.assertContains(
            r, f'href="{reverse("tienda:mp_conectar")}" hx-boost="false"'
        )
        self.assertNotContains(r, "Desconectar")

    def test_conectada_muestra_quien_cuando_y_desconectar(self):
        ahora = timezone.now()
        self.crear_cuenta(es_de_prueba=True, conectada_por=self.staff, conectada_en=ahora)
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertContains(r, "Conectada")
        self.assertContains(r, "cuenta de prueba")
        self.assertContains(r, "La conectó Ana")
        self.assertContains(r, reverse("tienda:mp_desconectar"))
        self.assertNotContains(r, "Conectar Mercado Pago")

    def test_reconectar_muestra_aviso_y_boton(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertContains(r, "hay que volver a conectarla")
        self.assertContains(r, 'hx-boost="false"')

    def test_aviso_de_no_disponible_cuando_acepta_mp_sin_cuenta_conectada(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"acepta_mercadopago": True}
        )
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertContains(
            r, "Mercado Pago no está disponible: tus alumnos ven sólo transferencia."
        )

    def test_sin_aviso_cuando_esta_conectada(self):
        self.crear_cuenta()
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio, defaults={"acepta_mercadopago": True}
        )
        r = self.client.get(reverse("tienda:configuracion"))
        self.assertNotContains(r, "no está disponible")


# --- Conectar y callback ----------------------------------------------------


@override_settings(**MP)
class ConectarTests(_Base):
    def test_redirige_a_mercado_pago_con_pkce_y_state_firmado(self):
        r = self.client.get(reverse("tienda:mp_conectar"))
        self.assertEqual(r.status_code, 302)
        destino = urlparse(r.url)
        self.assertEqual(destino.netloc, "auth.mercadopago.com.ar")
        q = parse_qs(destino.query)
        verifier = self.client.session["tienda_mp_verifier"]
        esperado = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], [esperado])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        datos = signing.loads(q["state"][0], salt="tienda.mercadopago.oauth")
        self.assertEqual((datos["g"], datos["u"]), (self.gimnasio.pk, self.staff.pk))
        self.assertEqual(datos["n"], self.client.session["tienda_mp_nonce"])

    def test_demo_avisa_y_no_arranca(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        r = self.client.get(reverse("tienda:mp_conectar"), follow=True)
        self.assertRedirects(r, reverse("tienda:configuracion"))
        self.assertContains(r, "En la cuenta de demostración no se conecta Mercado Pago")
        self.assertNotIn("tienda_mp_nonce", self.client.session)

    def test_sin_el_add_on_cartel_200(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)
        r = self.client.get(reverse("tienda:mp_conectar"))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("tienda_mp_nonce", self.client.session)

    def test_un_alumno_no_puede(self):
        alumno = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(alumno)
        self.assertEqual(self.client.get(reverse("tienda:mp_conectar")).status_code, 403)


@override_settings(**MP)
class CallbackTests(_Base):
    def iniciar(self):
        r = self.client.get(reverse("tienda:mp_conectar"))
        return parse_qs(urlparse(r.url).query)["state"][0]

    def callback(self, state=None, **extra):
        params = {"code": "TG-codigo", "state": state}
        params.update(extra)
        params = {k: v for k, v in params.items() if v is not None}
        return self.client.get(reverse("tienda:mp_callback"), params, follow=True)

    def test_canje_feliz_guarda_cifrado_y_avisa_al_staff(self):
        state = self.iniciar()
        verifier = self.client.session["tienda_mp_verifier"]
        with mock.patch(CANJE, return_value=tokens(live_mode=False)) as canje, \
                mock.patch(PUSH) as push, self.captureOnCommitCallbacks(execute=True):
            r = self.callback(state)
        self.assertRedirects(r, reverse("tienda:configuracion"))
        self.assertContains(r, "Mercado Pago quedó conectado.")
        canje.assert_called_once_with("TG-codigo", verifier)
        cuenta = CuentaMercadoPago.objects.get(gimnasio=self.gimnasio)
        self.assertEqual(
            (cuenta.estado, cuenta.mp_user_id, cuenta.es_de_prueba, cuenta.conectada_por),
            (E.CONECTADA, "111", True, self.staff),
        )
        self.assertEqual((cuenta.access_token, cuenta.refresh_token), ("APP_USR-acceso-nuevo", "REF-nuevo"))
        self.assertGreater(cuenta.token_expira, timezone.now() + timedelta(days=170))
        self.assertIsNotNone(cuenta.conectada_en)
        # Cifrado en reposo.
        with connection.cursor() as c:
            c.execute("select access_token from tienda_cuentamercadopago")
            self.assertNotIn("APP_USR", c.fetchone()[0])
        # Push a TODO el staff, nunca a los alumnos.
        push.assert_called_once()
        self.assertEqual(push.call_args.kwargs["rol"], Perfil.Rol.STAFF)
        self.assertEqual(push.call_args.args[0], self.gimnasio)
        self.assertIn("Ana conectó la cuenta de Mercado Pago", push.call_args.args[1]["body"])

    def test_el_verifier_y_el_state_valen_una_sola_vez(self):
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens()):
            self.callback(state)
            r = self.callback(state)
        self.assertContains(r, "expiró o es inválida")

    def test_state_vencido(self):
        state = self.iniciar()
        sesion = self.client.session
        sesion["tienda_mp_ts"] = (timezone.now() - timedelta(minutes=11)).isoformat()
        sesion.save()
        with mock.patch(CANJE) as canje:
            r = self.callback(state)
        self.assertContains(r, "expiró o es inválida")
        canje.assert_not_called()
        self.assertFalse(CuentaMercadoPago.objects.exists())

    def test_state_firmado_vencido_aunque_la_sesion_diga_que_no(self):
        state = self.iniciar()
        with mock.patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 700), \
                mock.patch(CANJE) as canje:
            r = self.callback(state)
        self.assertContains(r, "expiró o es inválida")
        canje.assert_not_called()

    def test_state_de_otro_usuario(self):
        self.iniciar()
        nonce = self.client.session["tienda_mp_nonce"]
        ajeno = signing.dumps({"g": self.gimnasio.pk, "u": self.staff.pk + 99, "n": nonce},
                              salt="tienda.mercadopago.oauth")
        with mock.patch(CANJE) as canje:
            r = self.callback(ajeno)
        self.assertContains(r, "expiró o es inválida")
        canje.assert_not_called()

    def test_state_de_otro_gimnasio(self):
        self.iniciar()
        nonce = self.client.session["tienda_mp_nonce"]
        ajeno = signing.dumps({"g": self.otro.pk, "u": self.staff.pk, "n": nonce},
                              salt="tienda.mercadopago.oauth")
        with mock.patch(CANJE) as canje:
            r = self.callback(ajeno)
        self.assertContains(r, "expiró o es inválida")
        canje.assert_not_called()

    def test_state_con_otro_nonce_o_sin_firma(self):
        self.iniciar()
        for state in (
            signing.dumps({"g": self.gimnasio.pk, "u": self.staff.pk, "n": "otro"},
                          salt="tienda.mercadopago.oauth"),
            "basura",
            None,
        ):
            with self.subTest(state=state), mock.patch(CANJE) as canje:
                self.iniciar()
                r = self.callback(state)
                self.assertContains(r, "expiró o es inválida")
                canje.assert_not_called()

    def test_sin_haber_empezado(self):
        with mock.patch(CANJE) as canje:
            r = self.callback("lo-que-sea")
        self.assertContains(r, "expiró o es inválida")
        canje.assert_not_called()

    def test_cancelo_la_autorizacion(self):
        self.iniciar()
        with mock.patch(CANJE) as canje:
            r = self.client.get(reverse("tienda:mp_callback"), {"error": "access_denied"}, follow=True)
        self.assertContains(r, "No se conectó Mercado Pago")
        canje.assert_not_called()
        self.assertNotIn("tienda_mp_verifier", self.client.session)

    def test_sin_refresh_token_se_rechaza_y_no_guarda(self):
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(refresh=None)):
            r = self.callback(state)
        self.assertContains(r, "no otorgó acceso sin conexión")
        self.assertFalse(CuentaMercadoPago.objects.exists())

    def test_canje_con_error_mensaje_y_log_sin_secretos(self):
        state = self.iniciar()
        with mock.patch(CANJE, side_effect=ErrorDeMercadoPago("HTTP 400", codigo=400)), \
                self.assertLogs("tienda.views_mercadopago", "WARNING") as log:
            r = self.callback(state)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se pudo conectar Mercado Pago")
        self.assertFalse(CuentaMercadoPago.objects.exists())
        self.assertNotIn("TG-codigo", "\n".join(log.output))

    def test_demo_no_conecta(self):
        state = self.iniciar()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        with mock.patch(CANJE) as canje:
            r = self.callback(state)
        self.assertContains(r, "En la cuenta de demostración no se conecta Mercado Pago")
        canje.assert_not_called()

    def test_reconectar_la_misma_cuenta_con_pedidos_vivos_se_permite(self):
        self.crear_cuenta(estado=E.RECONECTAR, mp_user_id="111")
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=111)):
            r = self.callback(state)
        self.assertContains(r, "quedó conectado")
        cuenta = CuentaMercadoPago.objects.get(gimnasio=self.gimnasio)
        self.assertEqual((cuenta.estado, cuenta.refresh_token), (E.CONECTADA, "REF-nuevo"))
        self.assertEqual(CuentaMercadoPago.objects.count(), 1)

    def test_cambio_de_cuenta_con_pedido_mp_vivo_se_rechaza(self):
        self.crear_cuenta(mp_user_id="111")
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "Terminá o cancelá esos pedidos")
        cuenta = CuentaMercadoPago.objects.get(gimnasio=self.gimnasio)
        self.assertEqual((cuenta.mp_user_id, cuenta.refresh_token), ("111", "REF-viejo"))

    def test_cambio_de_cuenta_con_pedido_mp_reciente_ya_cerrado_se_rechaza(self):
        self.crear_cuenta(mp_user_id="111")
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO, estado=Pedido.Estado.ENTREGADO)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "Terminá o cancelá esos pedidos")

    def test_cambio_de_cuenta_con_pedido_mp_pagado_sin_retirar_de_hace_mas_de_7_dias_se_rechaza(self):
        self.crear_cuenta(mp_user_id="111")
        pedido = self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO, estado=Pedido.Estado.PAGADO)
        Pedido.objects.filter(pk=pedido.pk).update(creado=timezone.now() - timedelta(days=20))
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "Terminá o cancelá esos pedidos")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).mp_user_id, "111")

    def test_cambio_de_cuenta_con_pedido_mp_viejo_cerrado_se_permite(self):
        self.crear_cuenta(mp_user_id="111")
        pedido = self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO, estado=Pedido.Estado.ENTREGADO)
        Pedido.objects.filter(pk=pedido.pk).update(creado=timezone.now() - timedelta(days=8))
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "quedó conectado")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).mp_user_id, "222")

    def test_los_pedidos_de_transferencia_no_frenan_el_cambio(self):
        self.crear_cuenta(mp_user_id="111")
        self.crear_pedido()
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "quedó conectado")

    def test_los_pedidos_de_otro_gimnasio_no_frenan_el_cambio(self):
        self.crear_cuenta(mp_user_id="111")
        self.crear_pedido(gimnasio=self.otro, medio=Pedido.Medio.MERCADOPAGO)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertContains(r, "quedó conectado")


# --- Desconectar ------------------------------------------------------------


@override_settings(**MP)
class DesconectarTests(_Base):
    def test_sin_pedidos_vivos_desconecta_y_conserva_los_tokens(self):
        cuenta = self.crear_cuenta()
        ConfiguracionTienda.objects.update_or_create(
            gimnasio=self.gimnasio,
            defaults={"acepta_mercadopago": True, "acepta_transferencia": False},
        )
        r = self.client.post(reverse("tienda:mp_desconectar"), follow=True)
        self.assertRedirects(r, reverse("tienda:configuracion"))
        self.assertContains(r, "quedó desconectado")
        cuenta = CuentaMercadoPago.objects.get(pk=cuenta.pk)
        self.assertEqual(cuenta.estado, E.DESCONECTADA)
        self.assertIsNotNone(cuenta.desconectada_en)
        self.assertEqual((cuenta.access_token, cuenta.refresh_token), ("APP_USR-viejo", "REF-viejo"))
        c = obtener_configuracion(self.gimnasio)
        self.assertEqual((c.acepta_mercadopago, c.acepta_transferencia), (False, True))

    def test_con_pedido_mp_esperando_el_pago_se_rechaza(self):
        self.crear_cuenta()
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO)
        r = self.client.post(reverse("tienda:mp_desconectar"), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hay pedidos esperando el pago por Mercado Pago")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)

    def test_pedidos_de_transferencia_o_ya_pagos_no_frenan(self):
        self.crear_cuenta()
        self.crear_pedido()
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO, estado=Pedido.Estado.PAGADO)
        self.client.post(reverse("tienda:mp_desconectar"))
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.DESCONECTADA)

    def test_no_toca_la_cuenta_de_otro_gimnasio(self):
        self.crear_cuenta()
        ajena = self.crear_cuenta(gimnasio=self.otro)
        self.client.post(reverse("tienda:mp_desconectar"))
        self.assertEqual(CuentaMercadoPago.objects.get(pk=ajena.pk).estado, E.CONECTADA)

    def test_demo_no_desconecta(self):
        self.crear_cuenta()
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.client.post(reverse("tienda:mp_desconectar"))
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)

    def test_con_el_token_ilegible_desconecta_sin_romper(self):
        self.crear_cuenta()
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.client.post(reverse("tienda:mp_desconectar"))
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.DESCONECTADA)

    def test_solo_post(self):
        self.assertEqual(self.client.get(reverse("tienda:mp_desconectar")).status_code, 405)


# --- Refresco ---------------------------------------------------------------


def _crudo(columna="access_token"):
    with connection.cursor() as c:
        c.execute(f"select {columna} from tienda_cuentamercadopago order by id")
        return [fila[0] for fila in c.fetchall()]


@override_settings(**MP)
class RefrescoTests(_Base):
    def refrescar(self):
        with mock.patch(PUSH) as push, self.captureOnCommitCallbacks(execute=True):
            resultado = cuenta_mp.refrescar_cuentas()
        return resultado, push

    def test_refresca_las_que_vencen_en_menos_de_30_dias(self):
        cuenta = self.crear_cuenta()
        with mock.patch(REFRESCO, return_value=tokens(refresh="REF-2")) as refresco:
            resultado, _ = self.refrescar()
        self.assertEqual(resultado, {"refrescadas": 1, "reconectar": 0, "errores": 0})
        self.assertEqual(refresco.call_args.args[0].valor, "REF-viejo")
        cuenta.refresh_from_db()
        self.assertEqual((cuenta.access_token, cuenta.refresh_token), ("APP_USR-acceso-nuevo", "REF-2"))
        self.assertGreater(cuenta.token_expira, timezone.now() + timedelta(days=170))
        self.assertEqual(cuenta.estado, E.CONECTADA)

    def test_no_toca_las_que_les_queda_mucho_ni_las_que_no_estan_conectadas(self):
        self.crear_cuenta(token_expira=timezone.now() + timedelta(days=60))
        self.crear_cuenta(gimnasio=self.otro, estado=E.DESCONECTADA)
        with mock.patch(REFRESCO) as refresco:
            resultado, _ = self.refrescar()
        refresco.assert_not_called()
        self.assertEqual(resultado["refrescadas"], 0)

    def test_sin_token_expira_se_refresca(self):
        self.crear_cuenta(token_expira=None)
        with mock.patch(REFRESCO, return_value=tokens()) as refresco:
            self.refrescar()
        refresco.assert_called_once()

    def test_relee_tras_el_lock_y_no_gasta_el_refresh_token_dos_veces(self):
        """Otro proceso refrescó la segunda cuenta mientras se procesaba la primera."""
        a = self.crear_cuenta()
        b = self.crear_cuenta(gimnasio=self.otro, refresh_token="REF-b")

        def otro_proceso(_refresh):
            CuentaMercadoPago.objects.filter(pk=b.pk).update(
                token_expira=timezone.now() + timedelta(days=170)
            )
            return tokens()

        with mock.patch(REFRESCO, side_effect=otro_proceso) as refresco:
            resultado, _ = self.refrescar()
        self.assertEqual(refresco.call_count, 1)
        self.assertEqual(resultado["refrescadas"], 1)
        a.refresh_from_db()
        self.assertEqual(a.refresh_token, "REF-nuevo")

    def test_401_y_400_pasan_a_reconectar_y_avisan_al_staff(self):
        for codigo in (400, 401):
            with self.subTest(codigo=codigo):
                CuentaMercadoPago.objects.all().delete()
                cuenta = self.crear_cuenta()
                with mock.patch(REFRESCO, side_effect=ErrorDeMercadoPago("x", codigo=codigo)):
                    resultado, push = self.refrescar()
                self.assertEqual(resultado["reconectar"], 1)
                cuenta.refresh_from_db()
                self.assertEqual(cuenta.estado, E.RECONECTAR)
                push.assert_called_once()
                self.assertEqual(push.call_args.kwargs["rol"], Perfil.Rol.STAFF)
                self.assertIn("reconect", push.call_args.args[1]["body"])

    def test_error_de_red_o_500_no_cambia_el_estado_y_se_reintenta(self):
        for codigo in (None, 500):
            with self.subTest(codigo=codigo):
                CuentaMercadoPago.objects.all().delete()
                cuenta = self.crear_cuenta()
                with mock.patch(REFRESCO, side_effect=ErrorDeMercadoPago("x", codigo=codigo)):
                    resultado, push = self.refrescar()
                self.assertEqual(resultado, {"refrescadas": 0, "reconectar": 0, "errores": 1})
                cuenta.refresh_from_db()
                self.assertEqual((cuenta.estado, cuenta.refresh_token), (E.CONECTADA, "REF-viejo"))
                push.assert_not_called()

    def test_respuesta_sin_refresh_token_no_pisa_lo_que_hay(self):
        cuenta = self.crear_cuenta()
        with mock.patch(REFRESCO, return_value=tokens(refresh=None)):
            resultado, _ = self.refrescar()
        self.assertEqual(resultado["errores"], 1)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.refresh_token, "REF-viejo")

    def test_un_error_en_una_cuenta_no_frena_a_las_otras(self):
        self.crear_cuenta()
        b = self.crear_cuenta(gimnasio=self.otro)
        respuestas = [ErrorDeMercadoPago("x", codigo=500), tokens(refresh="REF-b")]
        with mock.patch(REFRESCO, side_effect=respuestas):
            resultado, _ = self.refrescar()
        self.assertEqual((resultado["refrescadas"], resultado["errores"]), (1, 1))
        b.refresh_from_db()
        self.assertEqual(b.refresh_token, "REF-b")

    def test_token_que_no_se_descifra_va_a_reconectar_sin_llamar_a_mp_ni_reescribirlo(self):
        cuenta = self.crear_cuenta()
        antes = (_crudo("access_token"), _crudo("refresh_token"))
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with mock.patch(REFRESCO) as refresco:
                resultado, push = self.refrescar()
        refresco.assert_not_called()
        self.assertEqual(resultado["reconectar"], 1)
        self.assertEqual((_crudo("access_token"), _crudo("refresh_token")), antes)
        self.assertEqual(CuentaMercadoPago.objects.get(pk=cuenta.pk).estado, E.RECONECTAR)
        push.assert_called_once()

    def test_reconectar_condicional_no_pisa_una_cuenta_recien_refrescada(self):
        cuenta = self.crear_cuenta()
        leido = cuenta.token_expira
        CuentaMercadoPago.objects.filter(pk=cuenta.pk).update(
            token_expira=timezone.now() + timedelta(days=170)
        )
        with mock.patch(PUSH) as push, self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(cuenta_mp.marcar_reconectar(cuenta.pk, leido))
        self.assertEqual(CuentaMercadoPago.objects.get(pk=cuenta.pk).estado, E.CONECTADA)
        push.assert_not_called()

    def test_reconectar_condicional_si_coincide(self):
        cuenta = self.crear_cuenta()
        with mock.patch(PUSH), self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira))
        self.assertEqual(CuentaMercadoPago.objects.get(pk=cuenta.pk).estado, E.RECONECTAR)

    def test_un_push_caido_no_deshace_el_paso_a_reconectar(self):
        cuenta = self.crear_cuenta()
        with mock.patch(PUSH, side_effect=RuntimeError("push caído")), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(cuenta_mp.marcar_reconectar(cuenta.pk, cuenta.token_expira))
        self.assertEqual(CuentaMercadoPago.objects.get(pk=cuenta.pk).estado, E.RECONECTAR)

    def test_el_log_no_trae_tokens(self):
        self.crear_cuenta()
        with mock.patch(REFRESCO, side_effect=ErrorDeMercadoPago("REF-viejo", codigo=500)), \
                self.assertLogs("tienda.cuenta_mp", "WARNING") as log:
            self.refrescar()
        self.assertNotIn("REF-viejo", "\n".join(log.output))


# --- Borrado de tokens y comando -------------------------------------------


class BorradoDeTokensTests(_Base):
    def desconectada(self, dias, gimnasio=None):
        return self.crear_cuenta(
            gimnasio=gimnasio, estado=E.DESCONECTADA,
            desconectada_en=timezone.now() - timedelta(days=dias),
        )

    def test_a_los_7_dias_se_borran_antes_se_conservan(self):
        vieja = self.desconectada(8)
        reciente = self.desconectada(6, gimnasio=self.otro)
        self.assertEqual(cuenta_mp.borrar_tokens_vencidos(), 1)
        with override_settings(**MP):
            vieja.refresh_from_db()
            reciente.refresh_from_db()
        self.assertEqual((vieja.access_token, vieja.refresh_token), ("", ""))
        self.assertEqual((reciente.access_token, reciente.refresh_token), ("APP_USR-viejo", "REF-viejo"))
        self.assertEqual(vieja.estado, E.DESCONECTADA)

    def test_es_idempotente_y_no_toca_a_las_conectadas(self):
        self.desconectada(30)
        conectada = self.crear_cuenta(gimnasio=self.otro)
        cuenta_mp.borrar_tokens_vencidos()
        self.assertEqual(cuenta_mp.borrar_tokens_vencidos(), 0)
        with override_settings(**MP):
            conectada.refresh_from_db()
        self.assertEqual(conectada.refresh_token, "REF-viejo")


class ComandoTests(_Base):
    def correr(self):
        salida = StringIO()
        call_command("refrescar_cuentas_mp", stdout=salida)
        return salida.getvalue()

    def test_bandera_apagada_sin_cuentas_conectadas_sale_0_apagado(self):
        self.assertIn("apagado", self.correr())

    def test_bandera_apagada_con_cuenta_conectada_falla(self):
        self.crear_cuenta()
        with self.assertRaises(CommandError):
            self.correr()

    def test_bandera_apagada_con_cuenta_solo_desconectada_o_reconectar_sale_0(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        self.crear_cuenta(gimnasio=self.otro, estado=E.DESCONECTADA, desconectada_en=timezone.now())
        self.assertIn("apagado", self.correr())

    def test_el_borrado_corre_antes_del_chequeo_de_la_bandera_y_sin_clave(self):
        self.crear_cuenta(
            estado=E.DESCONECTADA, desconectada_en=timezone.now() - timedelta(days=9)
        )
        self.crear_cuenta(gimnasio=self.otro)  # conectada: el comando falla DESPUÉS de borrar
        with self.assertRaises(CommandError):
            with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
                self.correr()
        self.assertEqual(_crudo("access_token")[0], "")

    @override_settings(**MP)
    def test_con_la_bandera_refresca(self):
        self.crear_cuenta()
        with mock.patch(REFRESCO, return_value=tokens()):
            salida = self.correr()
        self.assertIn("Cuentas refrescadas: 1", salida)


class WorkflowYRedirectTests(TestCase):
    def test_la_ruta_del_callback_es_la_de_la_redirect_uri_documentada(self):
        self.assertEqual(reverse("tienda:mp_callback"), "/tienda/mercadopago/callback/")
        from pathlib import Path

        ejemplo = (Path(settings.BASE_DIR) / ".env.example").read_text()
        self.assertIn("https://www.tugimapp.com/tienda/mercadopago/callback/", ejemplo)

    def test_el_paso_del_workflow_va_antes_del_ping_con_su_env_completo(self):
        from pathlib import Path

        texto = (Path(settings.BASE_DIR) / ".github/workflows/reconciliar-cobros.yml").read_text()
        paso = texto.index("name: Refrescar cuentas de Mercado Pago de la tienda")
        self.assertLess(paso, texto.index("name: Avisar a Healthchecks"))
        bloque = texto[paso:texto.index("name: Avisar a Healthchecks")]
        self.assertIn("if: ${{ !cancelled() }}", bloque)
        self.assertIn("run: python manage.py refrescar_cuentas_mp", bloque)
        for variable in (
            "DATABASE_URL", "DJANGO_DEBUG", "MERCADOPAGO_CLIENT_ID",
            "MERCADOPAGO_CLIENT_SECRET", "MERCADOPAGO_OAUTH_REDIRECT_URI",
            "MP_TOKEN_ENCRYPTION_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY",
            "VAPID_ADMIN_EMAIL",
        ):
            self.assertIn(f"{variable}: ", bloque)
        runbook = (Path(settings.BASE_DIR) / "docs/runbook-respaldos.md").read_text()
        for secret in ("MERCADOPAGO_CLIENT_ID", "MERCADOPAGO_CLIENT_SECRET",
                       "MERCADOPAGO_OAUTH_REDIRECT_URI", "MP_TOKEN_ENCRYPTION_KEY"):
            self.assertIn(f"`{secret}`", runbook)
