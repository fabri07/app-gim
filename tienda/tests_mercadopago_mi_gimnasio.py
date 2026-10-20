"""Conectar Mercado Pago desde «Mi gimnasio», sin la tienda (T4 de «cuotas
por Mercado Pago»).

La misma cuenta cobra la tienda y las cuotas: las vistas OAuth ya no exigen el
add-on, vuelven a donde arrancó el flujo (`_destino`), desconectar cancela los
links de cuotas vivos y el cambio de cuenta se frena también por cobros de
cuotas. En «Mi gimnasio» viven la tarjeta `#mercadopago` y el interruptor
`cuotas_con_mercadopago`. Sin red: se parchea el canje de
`tienda.mercadopago_oauth`."""

from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from alumnos.models import Alumno
from notificaciones import services as avisos
from pagos.models import CobroOnlineCuota
from pagos.testing import crear_cuota
from tenants.models import Gimnasio, Perfil
from tienda.models import ConfiguracionTienda, CuentaMercadoPago, Pedido
from tienda.tests_mercadopago_cuenta import CANJE, MP, PUSH, E, _Base, tokens

C = CobroOnlineCuota.Estado
MI_GIMNASIO = reverse("gimnasio_editar")
MI_GIMNASIO_MP = MI_GIMNASIO + "#mercadopago"


class _BaseMiGimnasio(_Base):
    def sin_tienda(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(tienda_habilitada=False)

    def crear_intento(self, gimnasio=None, **extra):
        gimnasio = gimnasio or self.gimnasio
        alumno = Alumno.objects.create(gimnasio=gimnasio, nombre="Eva", apellido="R")
        cuota = crear_cuota(
            gimnasio=gimnasio, alumno=alumno, inicio=date(2026, 3, 1),
            monto=Decimal("28000"),
        )
        return CobroOnlineCuota.objects.create(
            gimnasio=gimnasio, cuota=cuota, monto=Decimal("28000"), mp_user_id="111", **extra
        )

    def iniciar(self, desde_gimnasio=False):
        url = reverse("tienda:mp_conectar")
        if desde_gimnasio:
            url += "?desde=gimnasio"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        return parse_qs(urlparse(r.url).query)["state"][0]

    def callback(self, state, **extra):
        params = {"code": "TG-codigo", "state": state}
        params.update(extra)
        return self.client.get(reverse("tienda:mp_callback"), params)


# --- Ida y vuelta del OAuth ---------------------------------------------------


@override_settings(**MP)
class DestinoTests(_BaseMiGimnasio):
    def test_conectar_desde_mi_gimnasio_vuelve_a_mi_gimnasio(self):
        state = self.iniciar(desde_gimnasio=True)
        with mock.patch(CANJE, return_value=tokens()):
            r = self.callback(state)
        self.assertRedirects(r, MI_GIMNASIO_MP, fetch_redirect_response=False)
        self.assertNotIn("tienda_mp_desde", self.client.session)
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)

    def test_conectar_desde_la_tienda_sigue_volviendo_a_la_tienda(self):
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens()):
            r = self.callback(state)
        self.assertRedirects(r, reverse("tienda:configuracion"), fetch_redirect_response=False)

    def test_un_arranque_abandonado_desde_mi_gimnasio_no_desvia_el_siguiente(self):
        """Arrancó desde «Mi gimnasio», no volvió nunca, y después conectó
        desde la tienda: vuelve a la tienda."""
        self.iniciar(desde_gimnasio=True)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens()):
            r = self.callback(state)
        self.assertRedirects(r, reverse("tienda:configuracion"), fetch_redirect_response=False)

    def test_callback_sin_el_add_on_vuelve_a_mi_gimnasio(self):
        self.sin_tienda()
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens()):
            r = self.callback(state)
        self.assertRedirects(r, MI_GIMNASIO_MP, fetch_redirect_response=False)

    def test_cancelar_la_autorizacion_desde_mi_gimnasio_vuelve_a_mi_gimnasio(self):
        self.iniciar(desde_gimnasio=True)
        r = self.client.get(reverse("tienda:mp_callback"), {"error": "access_denied"}, follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "cancelaste la autorización")

    def test_state_invalido_sin_el_add_on_vuelve_a_mi_gimnasio_con_mensaje(self):
        self.sin_tienda()
        r = self.client.get(reverse("tienda:mp_callback"), {"code": "x", "state": "y"}, follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "expiró o es inválida")

    def test_demo_desde_mi_gimnasio_avisa_y_vuelve_ahi(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        r = self.client.get(reverse("tienda:mp_conectar") + "?desde=gimnasio", follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "En la cuenta de demostración no se conecta Mercado Pago")
        self.assertNotIn("tienda_mp_nonce", self.client.session)

    def test_un_alumno_no_puede_conectar_ni_desconectar(self):
        alumno = User.objects.create_user("alu", password="c-123456")
        Perfil.objects.create(usuario=alumno, gimnasio=self.gimnasio, rol=Perfil.Rol.ALUMNO)
        self.client.force_login(alumno)
        url = reverse("tienda:mp_conectar") + "?desde=gimnasio"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.crear_cuenta()
        r = self.client.post(reverse("tienda:mp_desconectar"), {"desde": "gimnasio"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)

    def test_anonimo_va_al_login(self):
        self.client.logout()
        r = self.client.get(reverse("tienda:mp_conectar") + "?desde=gimnasio")
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r.url)


class BanderaApagadaDestinoTests(_BaseMiGimnasio):
    def test_desde_mi_gimnasio_o_sin_el_add_on_avisa_en_mi_gimnasio(self):
        r = self.client.get(reverse("tienda:mp_conectar") + "?desde=gimnasio", follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "no está disponible")
        self.sin_tienda()
        r = self.client.get(reverse("tienda:mp_conectar"), follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertNotIn("tienda_mp_nonce", self.client.session)


# --- Desconectar ------------------------------------------------------------------


@override_settings(**MP)
class DesconectarDesdeMiGimnasioTests(_BaseMiGimnasio):
    def test_desde_mi_gimnasio_desconecta_y_vuelve_ahi(self):
        self.crear_cuenta()
        r = self.client.post(reverse("tienda:mp_desconectar"), {"desde": "gimnasio"}, follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "quedó desconectado")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.DESCONECTADA)

    def test_sin_el_add_on_desconecta_sin_romper_y_vuelve_a_mi_gimnasio(self):
        self.sin_tienda()
        self.crear_cuenta()
        r = self.client.post(reverse("tienda:mp_desconectar"))
        self.assertRedirects(r, MI_GIMNASIO_MP, fetch_redirect_response=False)
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.DESCONECTADA)

    def test_cancela_los_links_de_cuotas_vivos_y_no_toca_lo_demas(self):
        self.crear_cuenta()
        vivo = self.crear_intento(expira=timezone.now() + timedelta(hours=1))
        pagado = self.crear_intento(estado=C.PAGADO, acreditado_en=timezone.now())
        ajeno = self.crear_intento(gimnasio=self.otro)
        r = self.client.post(reverse("tienda:mp_desconectar"), {"desde": "gimnasio"})
        self.assertRedirects(r, MI_GIMNASIO_MP, fetch_redirect_response=False)
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.DESCONECTADA)
        estados = dict(CobroOnlineCuota.objects.values_list("pk", "estado"))
        self.assertEqual(
            (estados[vivo.pk], estados[pagado.pk], estados[ajeno.pk]),
            (C.CANCELADO, C.PAGADO, C.CREADO),
        )

    def test_con_pedido_esperando_el_pago_rechaza_y_vuelve_a_mi_gimnasio(self):
        self.crear_cuenta()
        self.crear_pedido(medio=Pedido.Medio.MERCADOPAGO)
        intento = self.crear_intento()
        r = self.client.post(reverse("tienda:mp_desconectar"), {"desde": "gimnasio"}, follow=True)
        self.assertRedirects(r, MI_GIMNASIO_MP)
        self.assertContains(r, "Hay pedidos esperando el pago por Mercado Pago")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)
        intento.refresh_from_db()
        self.assertEqual(intento.estado, C.CREADO)


# --- Cambio de cuenta frenado por cuotas -------------------------------------


@override_settings(**MP)
class CambioDeCuentaConCuotasTests(_BaseMiGimnasio):
    FRENO_LINK = "todavía se puede pagar"
    FRENO_COBROS = "todavía hay cobros de cuotas para revisar"

    def cambiar(self):
        state = self.iniciar(desde_gimnasio=True)
        with mock.patch(CANJE, return_value=tokens(user_id=222)):
            r = self.callback(state)
        self.assertRedirects(r, MI_GIMNASIO_MP, fetch_redirect_response=False)
        return self.client.get(MI_GIMNASIO)

    def assertFrena(self, freno=FRENO_COBROS):
        r = self.cambiar()
        self.assertContains(r, freno)
        cuenta = CuentaMercadoPago.objects.get(gimnasio=self.gimnasio)
        self.assertEqual((cuenta.mp_user_id, cuenta.refresh_token), ("111", "REF-viejo"))

    def assertPermite(self):
        r = self.cambiar()
        self.assertContains(r, "quedó conectado")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).mp_user_id, "222")

    def setUp(self):
        super().setUp()
        self.crear_cuenta(mp_user_id="111")
        self.ahora = timezone.now()

    def test_link_vivo_frena_y_dice_hasta_que_hora(self):
        expira = self.ahora + timedelta(hours=1)
        self.crear_intento(preference_id="pref-1", expira=expira)
        self.assertFrena(self.FRENO_LINK)
        self.assertContains(
            self.cambiar(), f"esperá hasta las {timezone.localtime(expira):%H:%M}"
        )

    def test_link_sin_vencimiento_frena(self):
        self.crear_intento(preference_id="pref-1", expira=None)
        self.assertFrena(self.FRENO_LINK)

    def test_link_creado_sin_preferencia_no_frena(self):
        """Un intento cuya preferencia nunca se creó no tiene link que pagar."""
        self.crear_intento(preference_id="", expira=self.ahora + timedelta(hours=1))
        self.assertPermite()

    def test_link_ya_vencido_aunque_siga_creado_no_frena(self):
        self.crear_intento(preference_id="pref-1", expira=self.ahora - timedelta(minutes=1))
        self.assertPermite()

    def test_revision_pendiente_frena_en_cada_estado(self):
        for estado in (C.REVISAR, C.DUPLICADO, C.DEVUELTO):
            with self.subTest(estado=estado):
                intento = self.crear_intento(estado=estado, revision_pendiente=True)
                self.assertFrena()
                intento.delete()

    def test_revision_ya_resuelta_y_vieja_no_frena(self):
        self.crear_intento(
            estado=C.REVISAR, revision_pendiente=False,
            acreditado_en=self.ahora - timedelta(days=8),
        )
        self.assertPermite()

    def test_cobro_reciente_frena(self):
        for estado in (C.PAGADO, C.DUPLICADO):
            with self.subTest(estado=estado):
                intento = self.crear_intento(
                    estado=estado, acreditado_en=self.ahora - timedelta(days=2)
                )
                self.assertFrena()
                intento.delete()

    def test_cobro_de_hace_8_dias_no_frena(self):
        self.crear_intento(estado=C.PAGADO, acreditado_en=self.ahora - timedelta(days=8))
        self.assertPermite()

    def test_desconectar_no_destraba_un_link_cancelado_que_sigue_pagable(self):
        """Cancelar un intento no vence la preferencia en MP: el alumno que
        ya tenía el checkout abierto puede pagarle a la cuenta vieja hasta
        `expira`. Con otra cuenta conectada ese pago no lo vería nadie."""
        vivo = self.crear_intento(
            preference_id="pref-1", expira=self.ahora + timedelta(hours=1)
        )
        self.client.post(reverse("tienda:mp_desconectar"), {"desde": "gimnasio"})
        vivo.refresh_from_db()
        self.assertEqual(vivo.estado, C.CANCELADO)
        r = self.cambiar()
        self.assertContains(r, self.FRENO_LINK)
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).mp_user_id, "111")

    def test_link_cancelado_o_rechazado_vivo_frena(self):
        for estado in (C.CANCELADO, C.RECHAZADO):
            with self.subTest(estado=estado):
                intento = self.crear_intento(
                    estado=estado, preference_id="pref-1",
                    expira=self.ahora + timedelta(hours=1),
                )
                self.assertFrena(self.FRENO_LINK)
                intento.delete()

    def test_link_cancelado_vencido_o_sin_preferencia_no_frena(self):
        self.crear_intento(
            estado=C.CANCELADO, preference_id="pref-1",
            expira=self.ahora - timedelta(minutes=1),
        )
        self.crear_intento(
            estado=C.CANCELADO, preference_id="", expira=self.ahora + timedelta(hours=1)
        )
        self.crear_intento(
            estado=C.RECHAZADO, preference_id="", expira=self.ahora + timedelta(hours=1)
        )
        self.assertPermite()

    def test_cuotas_de_otro_gimnasio_no_frenan(self):
        self.crear_intento(gimnasio=self.otro, expira=None)
        self.assertPermite()

    def test_reconectar_la_misma_cuenta_con_link_vivo_se_permite(self):
        CuentaMercadoPago.objects.filter(gimnasio=self.gimnasio).update(estado=E.RECONECTAR)
        self.crear_intento(expira=None)
        state = self.iniciar()
        with mock.patch(CANJE, return_value=tokens(user_id=111)):
            r = self.client.get(
                reverse("tienda:mp_callback"), {"code": "c", "state": state}, follow=True
            )
        self.assertContains(r, "quedó conectado")
        self.assertEqual(CuentaMercadoPago.objects.get(gimnasio=self.gimnasio).estado, E.CONECTADA)


# --- Push ----------------------------------------------------------------------


class AvisoDeCuentaTests(_BaseMiGimnasio):
    def test_sin_el_add_on_el_push_lleva_a_mi_gimnasio_y_no_habla_de_la_tienda(self):
        self.sin_tienda()
        self.gimnasio.refresh_from_db()
        with mock.patch(PUSH) as push:
            avisos.notificar_cuenta_mp(self.gimnasio, "conectada", "Ana")
            avisos.notificar_cuenta_mp(self.gimnasio, "reconectar")
        for llamada in push.call_args_list:
            payload = llamada.args[1]
            self.assertEqual(payload["url"], MI_GIMNASIO_MP)
            self.assertNotIn("tienda", payload["body"])
            self.assertEqual(llamada.kwargs["rol"], Perfil.Rol.STAFF)
        self.assertIn(
            "Ana conectó la cuenta de Mercado Pago", push.call_args_list[0].args[1]["body"]
        )

    def test_con_el_add_on_el_push_sigue_llevando_a_la_tienda(self):
        with mock.patch(PUSH) as push:
            avisos.notificar_cuenta_mp(self.gimnasio, "reconectar")
        self.assertEqual(push.call_args.args[1]["url"], reverse("tienda:configuracion"))


# --- «Mi gimnasio»: tarjeta e interruptor -------------------------------------


class _Anidamiento(HTMLParser):
    """¿La sección `#mercadopago` está adentro de `<form id="form-gimnasio">`?"""

    def __init__(self):
        super().__init__()
        self.en_form = False
        self.profundidad = 0
        self.tarjeta_adentro = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "form-gimnasio":
            self.en_form = True
        elif tag == "form" and self.en_form:
            self.profundidad += 1
        if attrs.get("id") == "mercadopago":
            self.tarjeta_adentro = self.en_form

    def handle_endtag(self, tag):
        if tag == "form" and self.en_form:
            if self.profundidad:
                self.profundidad -= 1
            else:
                self.en_form = False


def _datos_gimnasio(gimnasio, **extra):
    datos = {
        "nombre": gimnasio.nombre,
        "tipo_publico": "mixto",
        "paleta": gimnasio.paleta,
        "tipografia": gimnasio.tipografia,
        "fondo_tipo": "color",
        "precio_cuota": "",
    }
    datos.update(extra)
    return {k: v for k, v in datos.items() if v is not None}


def _queries_a_la_cuenta(contexto):
    return [q for q in contexto.captured_queries if "tienda_cuentamercadopago" in q["sql"]]


class TarjetaBanderaApagadaTests(_BaseMiGimnasio):
    def test_sin_tarjeta_sin_interruptor_y_sin_queries_a_la_cuenta(self):
        self.crear_cuenta()
        self.client.get(MI_GIMNASIO)  # calienta sesión y cachés: se comparan dos GET iguales
        with CaptureQueriesContext(connection) as apagada:
            r = self.client.get(MI_GIMNASIO)
        self.assertNotContains(r, 'id="mercadopago"')
        self.assertNotContains(r, "cuotas_con_mercadopago")
        self.assertNotContains(r, reverse("tienda:mp_conectar"))
        self.assertEqual(_queries_a_la_cuenta(apagada), [])
        with override_settings(**MP), CaptureQueriesContext(connection) as prendida:
            self.client.get(MI_GIMNASIO)
        self.assertEqual(len(_queries_a_la_cuenta(prendida)), 1)
        # La única diferencia de costo entre los dos es esa query.
        self.assertEqual(len(apagada), len(prendida) - 1)

    def test_un_post_viejo_no_prende_el_cobro_de_cuotas(self):
        self.crear_cuenta()
        r = self.client.post(
            MI_GIMNASIO, _datos_gimnasio(self.gimnasio, cuotas_con_mercadopago="on")
        )
        self.assertRedirects(r, MI_GIMNASIO)
        self.gimnasio.refresh_from_db()
        self.assertFalse(self.gimnasio.cuotas_con_mercadopago)


@override_settings(**MP)
class TarjetaTests(_BaseMiGimnasio):
    def test_sin_cuenta_conectar_con_desde_y_hx_boost_false(self):
        self.sin_tienda()
        r = self.client.get(MI_GIMNASIO)
        self.assertContains(r, 'id="mercadopago"')
        self.assertContains(r, "Desconectada")
        self.assertContains(r, "sirve para la tienda y para las cuotas")
        self.assertContains(
            r, f'href="{reverse("tienda:mp_conectar")}?desde=gimnasio" hx-boost="false"'
        )
        self.assertNotContains(r, reverse("tienda:mp_desconectar"))

    def test_conectada_muestra_quien_cuando_prueba_y_desconectar_desde_gimnasio(self):
        self.crear_cuenta(es_de_prueba=True, conectada_por=self.staff, conectada_en=timezone.now())
        r = self.client.get(MI_GIMNASIO)
        self.assertContains(r, "Conectada")
        self.assertContains(r, "cuenta de prueba")
        self.assertContains(r, "La conectó Ana")
        self.assertContains(r, f'action="{reverse("tienda:mp_desconectar")}"')
        self.assertContains(r, '<input type="hidden" name="desde" value="gimnasio">', html=True)
        self.assertNotContains(r, "Conectar Mercado Pago")

    def test_reconectar_muestra_aviso_y_boton(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        r = self.client.get(MI_GIMNASIO)
        self.assertContains(r, "hay que volver a conectarla")
        self.assertContains(r, "Reconectar Mercado Pago")
        self.assertContains(r, "?desde=gimnasio\" hx-boost=\"false\"")

    def test_demo_texto_fijo_sin_botones_ni_interruptor_ni_query(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.crear_cuenta()
        with CaptureQueriesContext(connection) as contexto:
            r = self.client.get(MI_GIMNASIO)
        self.assertContains(r, "En la cuenta de demostración no se conecta Mercado Pago")
        self.assertNotContains(r, reverse("tienda:mp_conectar"))
        self.assertNotContains(r, reverse("tienda:mp_desconectar"))
        self.assertNotContains(r, "cuotas_con_mercadopago")
        self.assertEqual(_queries_a_la_cuenta(contexto), [])

    def test_la_tarjeta_es_hermana_del_form_principal(self):
        self.crear_cuenta()
        r = self.client.get(MI_GIMNASIO)
        parser = _Anidamiento()
        parser.feed(r.content.decode())
        self.assertIs(parser.tarjeta_adentro, False)

    def test_el_interruptor_va_en_el_form_principal(self):
        r = self.client.get(MI_GIMNASIO)
        html = r.content.decode()
        inicio = html.index('id="form-gimnasio"')
        fin = html.index("</form>", inicio)
        self.assertIn('name="cuotas_con_mercadopago"', html[inicio:fin])
        self.assertIn("Mercado Pago te cobra su comisión", html[inicio:fin])


@override_settings(**MP)
class InterruptorTests(_BaseMiGimnasio):
    def guardar(self, **extra):
        return self.client.post(MI_GIMNASIO, _datos_gimnasio(self.gimnasio, **extra))

    def prendido(self):
        self.gimnasio.refresh_from_db()
        return self.gimnasio.cuotas_con_mercadopago

    def test_prender_sin_cuenta_es_error_visible(self):
        r = self.guardar(cuotas_con_mercadopago="on")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<p class="config-error">Primero conectá tu cuenta de Mercado Pago.</p>', html=True)
        self.assertFalse(self.prendido())

    def test_prender_con_la_cuenta_en_reconectar_es_error(self):
        self.crear_cuenta(estado=E.RECONECTAR)
        r = self.guardar(cuotas_con_mercadopago="on")
        self.assertContains(r, "Primero conectá tu cuenta de Mercado Pago.")
        self.assertFalse(self.prendido())

    def test_la_cuenta_de_otro_gimnasio_no_sirve(self):
        self.crear_cuenta(gimnasio=self.otro)
        r = self.guardar(cuotas_con_mercadopago="on")
        self.assertContains(r, "Primero conectá tu cuenta de Mercado Pago.")

    def test_prender_con_la_cuenta_conectada_guarda(self):
        self.crear_cuenta()
        r = self.guardar(cuotas_con_mercadopago="on")
        self.assertRedirects(r, MI_GIMNASIO)
        self.assertTrue(self.prendido())

    def test_ya_prendido_y_en_reconectar_guardar_otro_campo_no_se_bloquea(self):
        """Calcado de `FormYTarjetaTests.test_ya_prendido_y_en_reconectar_guardar_otro_campo_no_se_bloquea`
        de la tienda: la validación es sólo en la transición apagado→prendido."""
        self.crear_cuenta(estado=E.RECONECTAR)
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=True)
        self.gimnasio.refresh_from_db()
        r = self.guardar(cuotas_con_mercadopago="on", nombre="Otro nombre")
        self.assertRedirects(r, MI_GIMNASIO)
        self.gimnasio.refresh_from_db()
        self.assertEqual((self.gimnasio.nombre, self.gimnasio.cuotas_con_mercadopago), ("Otro nombre", True))

    def test_apagarlo_sin_cuenta_se_puede(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(cuotas_con_mercadopago=True)
        self.gimnasio.refresh_from_db()
        r = self.guardar()
        self.assertRedirects(r, MI_GIMNASIO)
        self.assertFalse(self.prendido())

    def test_en_la_demo_un_post_no_lo_prende(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self.gimnasio.refresh_from_db()
        self.crear_cuenta()
        self.guardar(cuotas_con_mercadopago="on")
        self.assertFalse(self.prendido())

    def test_sin_el_add_on_tambien_se_prende(self):
        self.sin_tienda()
        self.crear_cuenta()
        self.assertRedirects(self.guardar(cuotas_con_mercadopago="on"), MI_GIMNASIO)
        self.assertTrue(self.prendido())
        self.assertFalse(ConfiguracionTienda.objects.filter(gimnasio=self.gimnasio).exists())
