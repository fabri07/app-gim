"""Las pantallas del cobro online, el webhook y el tercer nivel de allowlist
de `PlataformaMiddleware`.

Nada sale a la red: se parchean las funciones públicas de
`suscripcion.mercadopago` (o `services.crear_intento` entero, en la vista que
sólo lo llama). `COBRO_ONLINE_ENABLED` está apagado bajo `TESTING`, así que
los tests que necesitan la integración prendida lo dicen con
`override_settings`.
"""

import hashlib
import hmac
import inspect
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views import View

from plataforma import facturacion, precios
from plataforma.mixins import SuperadminRequiredMixin
from plataforma.models import ActividadDiaria, PagoPlataforma
from suscripcion import services, views
from suscripcion.mercadopago import ErrorDeMercadoPago
from suscripcion.models import IntentoDeCobro
from tenants.mixins import StaffRequiredMixin
from tenants.models import Gimnasio, Perfil

CLAVE = "clave-123456"
SECRETO = "secreto-de-prueba"
PRENDIDO = override_settings(
    COBRO_ONLINE_ENABLED=True, MERCADOPAGO_WEBHOOK_SECRET=SECRETO
)
Estado = IntentoDeCobro.Estado


def _usuario(username, gimnasio, rol):
    usuario = User.objects.create_user(
        username, email=f"{username}@gimnasio.test", password=CLAVE
    )
    Perfil.objects.create(usuario=usuario, gimnasio=gimnasio, rol=rol)
    return usuario


def _intento(gimnasio, **extra):
    hoy = timezone.localdate()
    datos = {
        "gimnasio": gimnasio,
        "plan": IntentoDeCobro.Plan.MENSUAL,
        "periodo_desde": hoy,
        "periodo_hasta": hoy + timedelta(days=29),
        "monto_usd": Decimal("10.00"),
        "monto_ars": Decimal("12000.00"),
        "tipo_cambio": Decimal("1200.00"),
        "alumnos_activos": 0,
        "expira": timezone.now() + timedelta(hours=48),
        "init_point": "https://mp.test/checkout/pref-1",
    }
    datos.update(extra)
    return IntentoDeCobro.objects.create(**datos)


class _Base(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio A",
            slug="gimnasio-a",
            facturacion_inicio=timezone.localdate() - timedelta(days=40),
        )
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.staff = _usuario("staff-a", self.gimnasio, Perfil.Rol.STAFF)
        self.alumno = _usuario("alumno-a", self.gimnasio, Perfil.Rol.ALUMNO)
        self._usuario_otro = _usuario("staff-b", self.otro, Perfil.Rol.STAFF)

    def _login(self, username="staff-a"):
        self.client.login(username=username, password=CLAVE)

    def _congelar(self, estado=Gimnasio.EstadoCuenta.SUSPENDIDA):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(estado_cuenta=estado)


# ---------------------------------------------------------------------------
# El middleware: la pantalla de pago abre con la cuenta congelada
# ---------------------------------------------------------------------------


class CuentaSuspendidaTests(_Base):
    def setUp(self):
        super().setUp()
        self._congelar()

    def test_el_staff_entra_a_su_suscripcion_y_el_dia_queda_registrado(self):
        """Los dos asserts juntos son el tercer nivel de la allowlist: NO
        bloquea (no es el cartel) pero SÍ registra (no es `APPS_SIN_BLOQUEO`,
        que se saltea también el registro)."""
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "suscripcion/mi_suscripcion.html")
        self.assertTemplateNotUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertTrue(
            ActividadDiaria.objects.filter(
                usuario=self.staff, gimnasio=self.gimnasio
            ).exists()
        )

    def test_el_resto_del_panel_sigue_mostrando_el_cartel(self):
        self._login()

        for nombre in ("home", "alumnos:listado"):
            with self.subTest(nombre):
                respuesta = self.client.get(reverse(nombre))
                self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")

    def test_un_alumno_del_gimnasio_bloqueado_ve_el_cartel_y_no_un_403(self):
        self._login("alumno-a")

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertTemplateNotUsed(respuesta, "suscripcion/mi_suscripcion.html")

    @PRENDIDO
    def test_el_cartel_del_staff_ofrece_pagar(self):
        self._login()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Pagar la suscripción")
        self.assertContains(respuesta, reverse("suscripcion:mi_suscripcion"))


# ---------------------------------------------------------------------------
# Con la integración apagada no se ofrece pagar online
# ---------------------------------------------------------------------------


@override_settings(COBRO_ONLINE_ENABLED=False)
class IntegracionApagadaTests(_Base):
    def test_el_banner_de_atraso_no_ofrece_pagar(self):
        self._congelar(Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS)
        self._login()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "banner-atraso")
        self.assertNotContains(respuesta, "Pagar ahora")

    def test_el_cartel_no_ofrece_pagar(self):
        self._congelar()
        self._login()

        respuesta = self.client.get(reverse("home"))

        self.assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")
        self.assertNotContains(respuesta, "Pagar la suscripción")

    def test_mi_suscripcion_responde_200_explicando(self):
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "todavía no está disponible")
        self.assertNotContains(respuesta, reverse("suscripcion:iniciar_pago"))


@PRENDIDO
class BannerConLaIntegracionPrendidaTests(_Base):
    def test_el_banner_de_atraso_ofrece_pagar(self):
        """El control positivo de `test_el_banner_de_atraso_no_ofrece_pagar`:
        sin él, ese test pasaría también con el link borrado del todo."""
        self._congelar(Gimnasio.EstadoCuenta.ALUMNOS_BLOQUEADOS)
        self._login()

        respuesta = self.client.get(reverse("home"))

        self.assertContains(respuesta, "Pagar ahora")


# ---------------------------------------------------------------------------
# MiSuscripcionView
# ---------------------------------------------------------------------------


@PRENDIDO
class MiSuscripcionTests(_Base):
    def test_con_un_pago_previo_propone_el_periodo_siguiente(self):
        """El gimnasio sale de `gimnasio_anotado`: con la instancia pelada de
        `request.user.perfil.gimnasio` no hay `cubierto_hasta` y la pantalla
        propondría otra vez el primer período después de la prueba."""
        inicio = self.gimnasio.facturacion_inicio
        primer_desde, primer_hasta = precios.periodo_siguiente(inicio, None)
        superadmin = User.objects.create_superuser("root", password=CLAVE)
        PagoPlataforma.objects.create(
            gimnasio=self.gimnasio,
            fecha_pago=timezone.localdate(),
            monto_usd=Decimal("10"),
            alumnos_activos=0,
            periodo_desde=primer_desde,
            periodo_hasta=primer_hasta,
            registrado_por=superadmin,
        )
        siguiente_desde, _ = facturacion.periodo_a_cobrar(
            facturacion.gimnasio_anotado(self.gimnasio.pk)
        )
        self.assertNotEqual(siguiente_desde, primer_desde)
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertContains(respuesta, f"del {siguiente_desde:%d/%m/%Y}")
        self.assertNotContains(respuesta, f"del {primer_desde:%d/%m/%Y}")

    def test_con_varios_periodos_de_atraso_avisa_que_un_pago_no_alcanza(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            facturacion_inicio=timezone.localdate() - timedelta(days=100)
        )
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        # Vence a los 30 días del inicio: 70 de atraso = 3 períodos.
        self.assertContains(respuesta, "70 días de atraso")
        self.assertContains(respuesta, "3 períodos mensuales")
        self.assertContains(respuesta, "un solo pago mensual no alcanza")

    def test_con_un_periodo_de_atraso_alcanza_con_uno(self):
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertContains(respuesta, "10 días de atraso")
        self.assertContains(respuesta, "Con el próximo pago mensual quedás al día")

    def test_sin_cotizacion_lo_dice_y_no_deja_pagar(self):
        """`crear_intento` no genera ningún link sin cotización: prometer «el
        monto lo vas a ver al generar el link» y después fallar al apretar es
        una pantalla que se contradice."""
        self._login()

        with patch("plataforma.cambio.cotizacion_dolar", return_value=None):
            respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertContains(respuesta, "Ahora mismo no podemos calcular el monto en pesos")
        self.assertRegex(respuesta.content.decode(), r'<button[^>]*id="pagar"[^>]*disabled')

    def test_con_cotizacion_muestra_los_pesos_agrupados_y_deja_pagar(self):
        self._login()

        with patch(
            "plataforma.cambio.cotizacion_dolar",
            return_value={"compra": Decimal("1500"), "venta": Decimal("1557")},
        ):
            respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        html = respuesta.content.decode()
        # 10 USD (escalón de hasta 100 alumnos) × 1557.
        self.assertIn("$ 15.570,00", html)
        self.assertNotIn("Ahora mismo no podemos calcular", html)
        self.assertNotRegex(html, r'<button[^>]*id="pagar"[^>]*disabled')

    def test_un_vencimiento_pasado_dice_vencio(self):
        # El gimnasio de `_Base` venció hace 10 días.
        vencimiento = timezone.localdate() - timedelta(days=10)
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertContains(respuesta, f"Venció el <strong>{vencimiento:%d/%m/%Y}</strong>")
        self.assertNotContains(respuesta, "Próximo vencimiento")

    def test_el_anual_tacha_los_doce_meses_y_el_mensual_no_tacha_nada(self):
        """El ahorro del anual es real (12 meses de a uno contra 10). El precio
        de lista de la portada no se tacha acá: es la pantalla donde se cobra."""
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        html = respuesta.content.decode()
        # 0 alumnos activos: escalón de hasta 100, mensual 10.
        self.assertIn(f">USD {precios.precio_usd_anual_sin_descuento(0)}</s>", html)
        self.assertIn(f"USD {precios.precio_usd_anual(0)} (2 meses gratis)", html)
        self.assertEqual(html.count("<s "), 1)
        self.assertIn("tramo hasta 100 alumnos", html)

    def test_el_form_de_pagar_sale_del_boost(self):
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        html = respuesta.content.decode()
        form = re.search(
            r"<form[^>]*action=\"%s\"[^>]*>" % re.escape(reverse("suscripcion:iniciar_pago")),
            html,
        )
        self.assertIsNotNone(form)
        self.assertIn('hx-boost="false"', form.group(0))

    def test_la_cuenta_demo_ve_los_planes_con_el_boton_deshabilitado(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(es_demo=True)
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "cuenta de demostración")
        self.assertRegex(respuesta.content.decode(), r'<button[^>]*id="pagar"[^>]*disabled')

    def test_un_gimnasio_exento_no_tiene_nada_que_pagar(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(facturacion_exenta=True)
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tu cuenta no tiene cobro")
        self.assertNotContains(respuesta, 'id="pagar"')

    def _periodo_adeudado(self):
        return facturacion.periodo_a_cobrar(
            facturacion.gimnasio_anotado(self.gimnasio.pk)
        )

    def test_muestra_el_link_vivo_para_retomarlo(self):
        desde, hasta = self._periodo_adeudado()
        _intento(self.gimnasio, periodo_desde=desde, periodo_hasta=hasta)
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertContains(respuesta, "Continuar al pago")
        self.assertContains(respuesta, "https://mp.test/checkout/pref-1")

    def test_despues_de_un_pago_manual_no_ofrece_el_link_viejo(self):
        """El gimnasio generó el link y después transfirió; el superadmin lo
        cargó a mano. `plataforma` no cancela intentos (no importa
        `suscripcion`), así que es la pantalla la que no lo ofrece: «al día» y
        «Continuar al pago» a la vez terminaba en un DUPLICADO."""
        desde, hasta = self._periodo_adeudado()
        _intento(self.gimnasio, periodo_desde=desde, periodo_hasta=hasta)
        PagoPlataforma.objects.create(
            gimnasio=self.gimnasio,
            fecha_pago=timezone.localdate(),
            monto_usd=Decimal("10"),
            alumnos_activos=0,
            periodo_desde=desde,
            periodo_hasta=hasta,
            registrado_por=User.objects.create_superuser("root-manual", password=CLAVE),
        )
        self._login()

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, "Continuar al pago")
        self.assertNotContains(respuesta, "https://mp.test/checkout/pref-1")

    def test_un_alumno_sin_bloqueo_recibe_403(self):
        self._login("alumno-a")

        respuesta = self.client.get(reverse("suscripcion:mi_suscripcion"))

        self.assertEqual(respuesta.status_code, 403)


# ---------------------------------------------------------------------------
# IniciarPagoView
# ---------------------------------------------------------------------------


@PRENDIDO
class IniciarPagoTests(_Base):
    def setUp(self):
        super().setUp()
        self._login()
        self.url = reverse("suscripcion:iniciar_pago")

    def test_get_da_405(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_redirige_al_init_point(self):
        falso = SimpleNamespace(init_point="https://mp.test/checkout/abc")
        with patch.object(services, "crear_intento", return_value=falso) as crear:
            respuesta = self.client.post(self.url, {"plan": "anual"})

        self.assertRedirects(
            respuesta, "https://mp.test/checkout/abc", fetch_redirect_response=False
        )
        args, kwargs = crear.call_args
        self.assertEqual(args, (self.gimnasio.pk, "anual"))
        self.assertTrue(
            kwargs["url_notificacion"].endswith(reverse("suscripcion:webhook"))
        )
        self.assertEqual(kwargs["email_de_quien_pago"], "staff-a@gimnasio.test")
        referencia = uuid.uuid4()
        self.assertTrue(
            kwargs["url_retorno_para"](referencia).endswith(
                f"/suscripcion/retorno/{referencia}/"
            )
        )

    def test_sin_link_responde_200_con_el_motivo(self):
        with patch.object(
            services,
            "crear_intento",
            side_effect=services.CobroNoDisponible("sin_cotizacion"),
        ):
            respuesta = self.client.post(self.url, {"plan": "mensual"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, services.MENSAJES["sin_cotizacion"])

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="TEST-token-muy-secreto")
    def test_si_mercado_pago_rechaza_la_preferencia_queda_en_el_log(self):
        """Sin el log el operador sólo ve un 200: el 401 de un token mal
        cargado sería invisible. Y el log no lleva el token."""
        with (
            patch(
                "plataforma.cambio.cotizacion_dolar",
                return_value={"compra": Decimal("1150"), "venta": Decimal("1200")},
            ),
            patch(
                "suscripcion.mercadopago.crear_preferencia",
                side_effect=ErrorDeMercadoPago(
                    "HTTP 401 en POST /checkout/preferences"
                ),
            ),
            self.assertLogs("suscripcion.services", level="WARNING") as logs,
        ):
            respuesta = self.client.post(self.url, {"plan": "mensual"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, services.MENSAJES["mercadopago"])
        salida = "\n".join(logs.output)
        self.assertIn("gimnasio-a", salida)
        self.assertIn("HTTP 401 en POST /checkout/preferences", salida)
        self.assertNotIn("TEST-token-muy-secreto", salida)
        self.assertFalse(IntentoDeCobro.objects.exists())

    def test_un_plan_invalido_se_ve_como_error(self):
        with patch.object(services, "crear_intento") as crear:
            respuesta = self.client.post(self.url, {"plan": "vitalicio"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "config-error")
        crear.assert_not_called()


# ---------------------------------------------------------------------------
# RetornoView
# ---------------------------------------------------------------------------


@PRENDIDO
class RetornoTests(_Base):
    def _url(self, intento, **query):
        url = reverse("suscripcion:retorno", args=[intento.referencia_externa])
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        return url

    def test_la_referencia_de_otro_gimnasio_da_404(self):
        ajeno = _intento(self.otro)
        self._login()

        self.assertEqual(self.client.get(self._url(ajeno)).status_code, 404)

    def test_si_mercado_pago_no_contesta_muestra_confirmando(self):
        intento = _intento(self.gimnasio)
        self._login()

        with patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=ErrorDeMercadoPago("caído"),
        ) as consultar:
            respuesta = self.client.get(
                self._url(intento, payment_id="123", status="approved")
            )

        consultar.assert_called_once_with("123")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Estamos confirmando tu pago")
        self.assertNotContains(respuesta, "¡Listo!")

    def _pagar_el_periodo_mas_viejo(self, facturacion_inicio):
        """Deja la cuenta SUSPENDIDA, genera el intento del primer período sin
        cubrir y vuelve de Mercado Pago con ese pago aprobado: pasa por el
        `acreditar` real, con sólo la API parcheada."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            facturacion_inicio=facturacion_inicio,
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
        )
        desde, hasta = precios.periodo_siguiente(facturacion_inicio, None)
        intento = _intento(self.gimnasio, periodo_desde=desde, periodo_hasta=hasta)
        pago = {
            "id": 777,
            "status": "approved",
            "transaction_amount": float(intento.monto_ars),
            "currency_id": "ARS",
            "external_reference": str(intento.referencia_externa),
            "date_approved": "2026-09-28T12:00:00.000-03:00",
        }
        self._login()
        with patch("suscripcion.mercadopago.consultar_pago", return_value=pago):
            respuesta = self.client.get(self._url(intento, payment_id="777"))
        intento.refresh_from_db()
        self.assertEqual(intento.estado, Estado.PAGADO)
        return intento, respuesta

    def test_pagar_el_unico_periodo_adeudado_dice_listo(self):
        intento, respuesta = self._pagar_el_periodo_mas_viejo(
            timezone.localdate() - timedelta(days=40)
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "¡Listo!")
        self.assertContains(respuesta, f"{intento.periodo_hasta:%d/%m/%Y}")
        self.assertNotContains(respuesta, "Todavía hay períodos sin pagar")

    def test_con_varios_periodos_adeudados_no_dice_listo(self):
        """Pagado no es «al día»: la cuenta sigue suspendida porque el pago
        cubrió el período más viejo. «¡Listo!» con una fecha pasada y un link
        al inicio que termina en el cartel rompería la confianza."""
        intento, respuesta = self._pagar_el_periodo_mas_viejo(
            timezone.localdate() - timedelta(days=100)
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, "¡Listo!")
        self.assertContains(respuesta, "Todavía hay períodos sin pagar")
        self.assertContains(respuesta, "reactivar la cuenta")
        self.assertContains(respuesta, reverse("suscripcion:mi_suscripcion"))
        self.assertEqual(
            Gimnasio.objects.get(pk=self.gimnasio.pk).estado_cuenta,
            Gimnasio.EstadoCuenta.SUSPENDIDA,
        )

    def test_el_status_del_querystring_se_ignora(self):
        intento = _intento(self.gimnasio)
        self._login()

        respuesta = self.client.get(self._url(intento, status="approved"))

        self.assertNotContains(respuesta, "¡Listo!")
        self.assertNotContains(respuesta, "Recibimos tu pago")
        intento.refresh_from_db()
        self.assertEqual(intento.estado, Estado.CREADO)

    def test_volver_sin_pagar_no_dice_confirmando(self):
        """«Volver al sitio» en Mercado Pago, sin pago: «si ya pagaste no
        vuelvas a pagar» sería falso."""
        intento = _intento(self.gimnasio)
        self._login()

        for query in ({}, {"payment_id": "null", "status": "null"}):
            with self.subTest(query=query):
                respuesta = self.client.get(self._url(intento, **query))

                self.assertContains(respuesta, "No se registró ningún pago")
                self.assertContains(respuesta, "Podés volver a intentarlo")
                self.assertContains(respuesta, reverse("suscripcion:mi_suscripcion"))
                self.assertNotContains(respuesta, "Estamos confirmando tu pago")

    def test_sin_query_pero_con_un_pago_anotado_sigue_confirmando(self):
        intento = _intento(self.gimnasio, payment_id="555", estado_mp="in_process")
        self._login()

        respuesta = self.client.get(self._url(intento))

        self.assertContains(respuesta, "Estamos confirmando tu pago")

    def test_un_rechazado_ofrece_volver_a_intentar(self):
        intento = _intento(self.gimnasio, estado=Estado.RECHAZADO)
        self._login()

        respuesta = self.client.get(self._url(intento))

        self.assertContains(respuesta, "El pago fue rechazado")


# ---------------------------------------------------------------------------
# WebhookView
# ---------------------------------------------------------------------------


def _firma(data_id, request_id, ts="1700000000", secreto=SECRETO):
    manifest = f"id:{str(data_id).lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secreto.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


@PRENDIDO
class WebhookTests(TestCase):
    """Con un cliente SIN login: Mercado Pago nunca tiene sesión, y logueado un
    test podría pasar por el motivo equivocado."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("suscripcion:webhook")
        self.gimnasio = Gimnasio.objects.create(nombre="G", slug="g")

    def _post(self, data_id="123", tipo="payment", cuerpo=None, firma=None, client=None):
        cuerpo = cuerpo if cuerpo is not None else json.dumps(
            {"type": tipo, "data": {"id": data_id}}
        )
        return (client or self.client).post(
            f"{self.url}?data.id={data_id}&type={tipo}",
            data=cuerpo,
            content_type="application/json",
            HTTP_X_REQUEST_ID="req-1",
            HTTP_X_SIGNATURE=firma if firma is not None else _firma(data_id, "req-1"),
        )

    def test_una_firma_mala_da_401_sin_tocar_nada(self):
        intento = _intento(self.gimnasio)
        antes = (intento.estado, intento.modificado)

        with patch("suscripcion.mercadopago.consultar_pago") as consultar:
            respuesta = self._post(firma=_firma("123", "req-1", secreto="otro"))

        self.assertEqual(respuesta.status_code, 401)
        consultar.assert_not_called()
        intento.refresh_from_db()
        self.assertEqual((intento.estado, intento.modificado), antes)

    def test_sin_firma_da_401(self):
        self.assertEqual(self._post(firma="").status_code, 401)

    def test_un_tipo_distinto_de_payment_da_200_sin_consultar(self):
        with patch("suscripcion.mercadopago.consultar_pago") as consultar:
            respuesta = self._post(tipo="merchant_order")

        self.assertEqual(respuesta.status_code, 200)
        consultar.assert_not_called()

    def test_un_tipo_distinto_de_payment_sin_firma_da_200(self):
        """Las IPN viejas y las de `merchant_order` pueden llegar sin firmar:
        con 401 MP las reintentaría para siempre."""
        with patch("suscripcion.mercadopago.consultar_pago") as consultar:
            for tipo in ("merchant_order", "topic_merchant_order_wh"):
                with self.subTest(tipo=tipo):
                    respuesta = self._post(tipo=tipo, firma="")
                    self.assertEqual(respuesta.status_code, 200)
            ipn = self.client.post(f"{self.url}?id=123&topic=merchant_order")
            self.assertEqual(ipn.status_code, 200)

        consultar.assert_not_called()

    def test_un_payment_sin_firma_sigue_dando_401(self):
        respuesta = self._post(tipo="payment", firma="")

        self.assertEqual(respuesta.status_code, 401)

    def test_un_pago_que_mercado_pago_no_encuentra_da_200(self):
        """«Simular notificación» del panel de MP manda un id que no existe:
        con 500 se reintentaría para siempre."""
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=ErrorDeMercadoPago("HTTP 404 en GET /v1/payments/123", codigo=404),
        ):
            with self.assertLogs("suscripcion.views", "WARNING"):
                respuesta = self._post()

        self.assertEqual(respuesta.status_code, 200)

    def test_un_pago_ajeno_401_o_403_da_200_con_log_de_error(self):
        """Un pago de un vendedor (tienda) que la app manda a este webhook: el
        token de la plataforma no lo lee; con 500 MP reintentaría siempre."""
        for codigo in (401, 403):
            with self.subTest(codigo=codigo):
                with patch(
                    "suscripcion.mercadopago.consultar_pago",
                    side_effect=ErrorDeMercadoPago("HTTP", codigo=codigo),
                ):
                    with self.assertLogs("suscripcion.views", "ERROR"):
                        respuesta = self._post()
                self.assertEqual(respuesta.status_code, 200)

    def test_otro_error_http_de_mercado_pago_da_500(self):
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=ErrorDeMercadoPago("HTTP 502 en GET /v1/payments/123", codigo=502),
        ):
            respuesta = self._post()

        self.assertEqual(respuesta.status_code, 500)

    def test_si_mercado_pago_no_contesta_da_500_para_que_reintente(self):
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            side_effect=ErrorDeMercadoPago("caído"),
        ):
            respuesta = self._post()

        self.assertEqual(respuesta.status_code, 500)

    def test_un_pago_desconocido_da_200(self):
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            return_value={"id": 123, "status": "approved", "external_reference": ""},
        ) as consultar:
            respuesta = self._post()

        self.assertEqual(respuesta.status_code, 200)
        consultar.assert_called_once_with("123")

    def test_un_cuerpo_que_no_es_json_nunca_da_500(self):
        # `b"\x80abc"` en bytes y no en str: el cliente de test codifica un str
        # a UTF-8 y nunca llegaría un `UnicodeDecodeError` (con `b"\xff\xfe"`
        # tampoco: `json` lo lee como un BOM de UTF-16 y da `JSONDecodeError`).
        casos = ["esto no es json", "[1, 2, 3]", "[" * 100000, b"\xff\xfe", b"\x80abc"]
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            return_value={"id": 123, "status": "approved"},
        ):
            for cuerpo in casos:
                with self.subTest(cuerpo=cuerpo[:10]):
                    respuesta = self._post(cuerpo=cuerpo)
                    self.assertNotEqual(respuesta.status_code, 500)

    def test_el_data_id_puede_venir_solo_en_el_cuerpo(self):
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            return_value={"id": 456, "status": "approved"},
        ) as consultar:
            respuesta = self.client.post(
                self.url,
                data=json.dumps({"type": "payment", "data": {"id": 456}}),
                content_type="application/json",
                HTTP_X_REQUEST_ID="req-1",
                HTTP_X_SIGNATURE=_firma("456", "req-1"),
            )

        self.assertEqual(respuesta.status_code, 200)
        consultar.assert_called_once_with("456")

    @override_settings(COBRO_ONLINE_ENABLED=False)
    def test_con_la_integracion_apagada_da_503(self):
        with patch("suscripcion.mercadopago.consultar_pago") as consultar:
            respuesta = self._post()

        self.assertEqual(respuesta.status_code, 503)
        consultar.assert_not_called()

    def test_una_sesion_de_staff_no_alcanza(self):
        """Sin firma, un staff logueado recibe lo mismo que un anónimo."""
        staff = _usuario("staff-g", self.gimnasio, Perfil.Rol.STAFF)
        logueado = Client(enforce_csrf_checks=True)
        logueado.force_login(staff)

        with patch("suscripcion.mercadopago.consultar_pago") as consultar:
            respuesta = self._post(firma="", client=logueado)

        self.assertEqual(respuesta.status_code, 401)
        consultar.assert_not_called()

    def test_ignora_request_user_con_una_firma_valida(self):
        """La vista ignora `request.user` por completo: es lo que hace seguro
        el `csrf_exempt`. Con la MISMA notificación firmada, un staff logueado
        y un anónimo tienen que producir exactamente el mismo resultado
        (status y estado del intento). Con la firma mala los dos dan 401 y el
        test no distinguiría una vista que lea el usuario después."""
        staff = _usuario("staff-g", self.gimnasio, Perfil.Rol.STAFF)
        logueado = Client(enforce_csrf_checks=True)
        logueado.force_login(staff)
        anonimo = Client(enforce_csrf_checks=True)

        resultados = []
        hoy = timezone.localdate()
        for cliente, pago_id, corrimiento in (
            (anonimo, "501", 0),
            # Otro período, para que el segundo no choque por solapamiento
            # con el pago que acaba de crear el primero.
            (logueado, "502", 100),
        ):
            desde = hoy + timedelta(days=corrimiento)
            intento = _intento(
                self.gimnasio,
                periodo_desde=desde,
                periodo_hasta=desde + timedelta(days=29),
            )
            pago = {
                "id": int(pago_id),
                "status": "approved",
                "transaction_amount": float(intento.monto_ars),
                "currency_id": "ARS",
                "external_reference": str(intento.referencia_externa),
            }
            with patch("suscripcion.mercadopago.consultar_pago", return_value=pago):
                respuesta = self._post(data_id=pago_id, client=cliente)
            intento.refresh_from_db()
            resultados.append((respuesta.status_code, intento.estado))

        self.assertEqual(resultados[0], (200, Estado.PAGADO))
        self.assertEqual(resultados[1], resultados[0])

    def test_no_pide_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        with patch(
            "suscripcion.mercadopago.consultar_pago",
            return_value={"id": 123, "status": "approved"},
        ):
            respuesta = self._post(client=cliente)

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)


# ---------------------------------------------------------------------------
# Invariante: cada vista declara su público
# ---------------------------------------------------------------------------


class PublicoDeLasVistasTests(TestCase):
    #: Las únicas vistas sin mixin de rol, cada una con su motivo.
    EXCEPCIONES = {
        # Se autentica por la firma de Mercado Pago, no por sesión.
        "WebhookView",
    }

    def test_toda_vista_declara_su_publico(self):
        """Reemplaza, para una app con dos públicos, la invariante «toda vista
        de `plataforma` es superadmin»: una vista nueva sin mixin rompe acá en
        vez de quedar abierta a cualquiera logueado."""
        clases = [
            clase
            for _, clase in inspect.getmembers(views, inspect.isclass)
            if issubclass(clase, View) and clase.__module__ == views.__name__
        ]
        self.assertTrue(clases)
        for clase in clases:
            with self.subTest(clase.__name__):
                publicos = [
                    mixin
                    for mixin in (StaffRequiredMixin, SuperadminRequiredMixin)
                    if issubclass(clase, mixin)
                ]
                if clase.__name__ in self.EXCEPCIONES:
                    self.assertEqual(publicos, [])
                else:
                    self.assertEqual(len(publicos), 1)


class CobrosConProblemaTests(_Base):
    URL = "suscripcion:cobros_con_problema"

    def _superadmin(self):
        User.objects.create_superuser("root-cobros", password=CLAVE)
        self.client.login(username="root-cobros", password=CLAVE)

    def test_un_staff_recibe_403(self):
        self._login()
        self.assertEqual(self.client.get(reverse(self.URL)).status_code, 403)

    def test_muestra_el_revisar_y_no_el_pagado(self):
        self._superadmin()
        _intento(self.gimnasio, estado=Estado.REVISAR, payment_id="111")
        _intento(self.otro, estado=Estado.PAGADO, payment_id="222")

        respuesta = self.client.get(reverse(self.URL))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Gimnasio A")
        self.assertContains(respuesta, "111")
        self.assertNotContains(respuesta, "Gimnasio B")
        self.assertNotContains(respuesta, "222")

    def test_lista_el_pagado_con_un_pago_adicional(self):
        """Un segundo pago aprobado sobre un link ya pagado: el intento sigue
        PAGADO, pero hay plata que devolver y tiene que aparecer acá."""
        desde = timezone.localdate() - timedelta(days=10)
        intento = _intento(
            self.gimnasio, periodo_desde=desde, periodo_hasta=desde + timedelta(days=29)
        )
        _intento(self.otro, estado=Estado.PAGADO, payment_id="ajeno-9191")

        def pago(pago_id):
            return {
                "id": int(pago_id),
                "status": "approved",
                "transaction_amount": float(intento.monto_ars),
                "currency_id": "ARS",
                "external_reference": str(intento.referencia_externa),
            }

        with override_settings(COBRO_ONLINE_ENABLED=True):
            for pago_id in ("401", "402"):
                with patch(
                    "suscripcion.mercadopago.consultar_pago", return_value=pago(pago_id)
                ):
                    services.acreditar(pago_id)
        intento.refresh_from_db()
        self.assertEqual(intento.estado, Estado.PAGADO)
        self._superadmin()

        respuesta = self.client.get(reverse(self.URL))

        self.assertContains(respuesta, "Gimnasio A")
        self.assertContains(respuesta, "Pago adicional")
        self.assertContains(respuesta, "Adicional a devolver: 402")
        self.assertNotContains(respuesta, "ajeno-9191")

    def test_rechazados_solo_los_ultimos_30_dias(self):
        self._superadmin()
        viejo = _intento(self.gimnasio, estado=Estado.RECHAZADO, payment_id="333")
        IntentoDeCobro.objects.filter(pk=viejo.pk).update(
            creado=timezone.now() - timedelta(days=31)
        )
        _intento(self.otro, estado=Estado.RECHAZADO, payment_id="444")

        respuesta = self.client.get(reverse(self.URL))

        self.assertContains(respuesta, "444")
        self.assertNotContains(respuesta, "333")

    def test_el_costo_en_queries_no_crece_con_los_intentos(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._superadmin()

        def costo():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse(self.URL))
            return len(ctx)

        for n in range(2):
            _intento(self.gimnasio, estado=Estado.REVISAR, payment_id=f"a{n}")
        con_2 = costo()
        for n in range(6):
            _intento(self.otro, estado=Estado.DEVUELTO, payment_id=f"b{n}")
        self.assertEqual(costo(), con_2)


class TramoDeTests(TestCase):
    def test_etiqueta_de_cada_tramo_con_sus_bordes(self):
        for alumnos, esperado in [
            (0, "hasta 100"),
            (100, "hasta 100"),
            (101, "de 101 a 300"),
            (300, "de 101 a 300"),
            (301, "más de 300"),
        ]:
            with self.subTest(alumnos=alumnos):
                self.assertEqual(views._tramo_de(alumnos), esperado)
