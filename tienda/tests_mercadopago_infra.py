"""Infraestructura de Mercado Pago de la tienda (B1): cliente con token del
gimnasio, `Secreto`, OAuth, campo cifrado con otra clave, bandera y Sentry.
Sin red: se parchea `urllib.request.urlopen`."""

import json
import traceback
import urllib.error
from datetime import datetime, timezone as tz
from decimal import Decimal
from io import BytesIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from calendario.fields import EncryptedTextField
from suscripcion import mercadopago as mp
from tienda import mercadopago_oauth as oauth

OAUTH = dict(
    TESTING=False,
    MERCADOPAGO_ACCESS_TOKEN="APP_USR-plataforma",
    MERCADOPAGO_CLIENT_ID="cid-123",
    MERCADOPAGO_CLIENT_SECRET="csecret-ultra-secreto",
    MERCADOPAGO_OAUTH_REDIRECT_URI="https://www.tugimapp.com/tienda/mercadopago/callback/",
)


def _respuesta(cuerpo=b'{"ok": 1}'):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = cuerpo
    return r


@override_settings(**OAUTH)
class ClienteConTokenTests(SimpleTestCase):
    def test_sin_token_manda_el_de_la_plataforma(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta()) as uo:
            mp._pedir("GET", "/x")
        self.assertEqual(
            uo.call_args.args[0].get_header("Authorization"), "Bearer APP_USR-plataforma"
        )

    def test_con_token_manda_el_del_gimnasio(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta()) as uo:
            mp._pedir("GET", "/x", token=mp.Secreto("APP_USR-gimnasio"))
        self.assertEqual(
            uo.call_args.args[0].get_header("Authorization"), "Bearer APP_USR-gimnasio"
        )

    def test_sin_autorizacion_no_manda_bearer_ni_el_de_la_plataforma(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta()) as uo:
            mp._pedir("POST", "/oauth/token", cuerpo={"a": 1}, sin_autorizacion=True)
        pedido = uo.call_args.args[0]
        self.assertIsNone(pedido.get_header("Authorization"))
        self.assertEqual(pedido.data, b'{"a": 1}')

    def test_cuerpo_secreto_se_serializa_su_valor(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta()) as uo:
            mp._pedir("POST", "/x", cuerpo=mp.Secreto({"k": "v"}))
        self.assertEqual(uo.call_args.args[0].data, b'{"k": "v"}')

    def test_secreto_no_se_deja_ver(self):
        s = mp.Secreto("valor-sensible")
        self.assertNotIn("valor-sensible", repr(s))
        self.assertNotIn("valor-sensible", str(s))
        self.assertNotIn("valor-sensible", f"{s}")

    def test_es_de_prueba_por_token(self):
        self.assertTrue(mp.es_de_prueba(mp.Secreto("TEST-1")))
        self.assertFalse(mp.es_de_prueba(mp.Secreto("APP_USR-1")))
        self.assertFalse(mp.es_de_prueba())  # el de la plataforma, de siempre

    def test_consultar_y_buscar_pasan_el_token(self):
        t = mp.Secreto("APP_USR-g")
        with mock.patch.object(mp, "_pedir", return_value={"results": []}) as pedir:
            mp.consultar_pago("77", token=t)
            mp.buscar_pagos("ref", token=t)
        self.assertTrue(all(c.kwargs["token"] is t for c in pedir.call_args_list))


class CrearPreferenciaConTokenTests(SimpleTestCase):
    def _crear(self, **extra):
        args = dict(
            titulo="Pedido #1",
            monto_ars=Decimal("100"),
            referencia_externa="ref-1",
            url_retorno="https://www.tugimapp.com/x/",
            url_notificacion="https://www.tugimapp.com/hook/",
            expira=datetime(2026, 10, 1, 12, 0, tzinfo=tz.utc),
            ahora=datetime(2026, 9, 29, 9, 30, tzinfo=tz.utc),
        )
        args.update(extra)
        resp = {"id": "p", "init_point": "https://real", "sandbox_init_point": "https://sandbox"}
        with mock.patch.object(mp, "_pedir", return_value=resp) as pedir:
            salida = mp.crear_preferencia(**args)
        return salida, pedir

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="APP_USR-plat")
    def test_sandbox_explicito_gana_sobre_el_prefijo(self):
        t = mp.Secreto("APP_USR-sin-prefijo-test")
        salida, pedir = self._crear(token=t, sandbox=True)
        self.assertEqual(salida["init_point"], "https://sandbox")
        self.assertIs(pedir.call_args.kwargs["token"], t)
        salida, _ = self._crear(token=mp.Secreto("TEST-x"), sandbox=False)
        self.assertEqual(salida["init_point"], "https://real")

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="APP_USR-plat")
    def test_sandbox_none_decide_por_el_prefijo_del_token(self):
        self.assertEqual(
            self._crear(token=mp.Secreto("TEST-x"))[0]["init_point"], "https://sandbox"
        )
        self.assertEqual(self._crear()[0]["init_point"], "https://real")

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="APP_USR-plat")
    def test_statement_descriptor(self):
        _, pedir = self._crear()
        self.assertEqual(pedir.call_args.kwargs["cuerpo"]["statement_descriptor"], "TUGIMAPP")
        _, pedir = self._crear(statement_descriptor=None)
        self.assertNotIn("statement_descriptor", pedir.call_args.kwargs["cuerpo"])
        _, pedir = self._crear(statement_descriptor="MIGYM")
        self.assertEqual(pedir.call_args.kwargs["cuerpo"]["statement_descriptor"], "MIGYM")


@override_settings(**OAUTH)
class OAuthTests(SimpleTestCase):
    def test_url_de_autorizacion(self):
        url = urlparse(oauth.url_de_autorizacion("st", "chal"))
        self.assertEqual(
            f"{url.scheme}://{url.netloc}{url.path}",
            "https://auth.mercadopago.com.ar/authorization",
        )
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual(q["client_id"], "cid-123")
        self.assertEqual(q["response_type"], "code")
        self.assertEqual(q["platform_id"], "mp")
        self.assertEqual(q["redirect_uri"], OAUTH["MERCADOPAGO_OAUTH_REDIRECT_URI"])
        self.assertEqual((q["state"], q["code_challenge"]), ("st", "chal"))
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertNotIn("csecret", url.query)

    def test_canje_sin_bearer_y_con_el_cuerpo_esperado(self):
        r = _respuesta(b'{"access_token": "a", "refresh_token": "r", "expires_in": 15552000}')
        with mock.patch("urllib.request.urlopen", return_value=r) as uo:
            salida = oauth.canjear_codigo("code-1", "verif-1")
        self.assertEqual(salida["refresh_token"], "r")
        pedido = uo.call_args.args[0]
        self.assertEqual(pedido.full_url, mp.BASE + "/oauth/token")
        self.assertEqual(pedido.get_method(), "POST")
        self.assertIsNone(pedido.get_header("Authorization"))
        cuerpo = json.loads(pedido.data)
        self.assertEqual(cuerpo["grant_type"], "authorization_code")
        self.assertEqual(cuerpo["client_secret"], "csecret-ultra-secreto")
        self.assertEqual(cuerpo["code"], "code-1")
        self.assertEqual(cuerpo["code_verifier"], "verif-1")
        self.assertEqual(cuerpo["redirect_uri"], OAUTH["MERCADOPAGO_OAUTH_REDIRECT_URI"])

    def test_refrescar(self):
        r = _respuesta(b'{"access_token": "a2", "refresh_token": "r2"}')
        with mock.patch("urllib.request.urlopen", return_value=r) as uo:
            salida = oauth.refrescar(mp.Secreto("r1"))
        self.assertEqual(salida["access_token"], "a2")
        pedido = uo.call_args.args[0]
        self.assertIsNone(pedido.get_header("Authorization"))
        cuerpo = json.loads(pedido.data)
        self.assertEqual(cuerpo["grant_type"], "refresh_token")
        self.assertEqual(cuerpo["refresh_token"], "r1")

    def test_respuesta_que_no_es_objeto_es_error_de_mp(self):
        with mock.patch("urllib.request.urlopen", return_value=_respuesta(b"[1]")):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                oauth.canjear_codigo("c", "v")

    def test_error_http_lleva_el_codigo_y_ningun_secreto(self):
        error = urllib.error.HTTPError("https://x", 400, "no", {}, BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(mp.ErrorDeMercadoPago) as ctx:
                oauth.canjear_codigo("c", "v")
        self.assertEqual(ctx.exception.codigo, 400)
        self.assertNotIn("csecret", str(ctx.exception))


@override_settings(**OAUTH)
class SinSecretosEnLosFramesTests(SimpleTestCase):
    """Sentry captura las variables locales de cada frame de la traza. Ni el
    token del gimnasio (por parámetro) ni el `client_secret` del canje pueden
    quedar en claro en ninguno. try/except a mano: `assertRaises` limpia los
    frames y el test pasaría sin mirar nada."""

    @staticmethod
    def _frames(llamar):
        error = urllib.error.HTTPError("https://x", 500, "no", {}, BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            try:
                llamar()
            except mp.ErrorDeMercadoPago as e:
                traza = e.__traceback__
            else:
                raise AssertionError("debía levantar ErrorDeMercadoPago")
        return [f for f, _ in traceback.walk_tb(traza)]

    def _sin(self, frames, *secretos):
        for frame in frames:
            volcado = repr(frame.f_locals)
            for secreto in secretos:
                self.assertNotIn(secreto, volcado, frame.f_code.co_name)
            self.assertNotIn("Bearer", volcado)

    def test_token_por_parametro(self):
        frames = self._frames(
            lambda: mp._pedir("GET", "/x", token=mp.Secreto("APP_USR-tok-del-gym-999"))
        )
        self.assertTrue(any(f.f_code.co_name == "_pedir" for f in frames))
        self._sin(frames, "tok-del-gym-999")

    def test_token_por_consultar_pago(self):
        frames = self._frames(
            lambda: mp.consultar_pago("1", token=mp.Secreto("APP_USR-tok-del-gym-999"))
        )
        self._sin(frames, "tok-del-gym-999")

    def test_client_secret_del_canje_y_del_refresco(self):
        for llamar in (
            lambda: oauth.canjear_codigo("c", "v"),
            lambda: oauth.refrescar(mp.Secreto("refresh-secreto-777")),
        ):
            frames = self._frames(llamar)
            self.assertTrue(any(f.f_code.co_name == "_pedir" for f in frames))
            self._sin(frames, "csecret-ultra-secreto", "refresh-secreto-777")


class CampoCifradoConOtraClaveTests(SimpleTestCase):
    def test_round_trip_con_su_propia_clave(self):
        campo = EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            crudo = campo.get_prep_value("tok-mp")
            self.assertNotIn("tok-mp", crudo)
            self.assertEqual(campo.from_db_value(crudo, None, None), "tok-mp")

    def test_vacio_no_se_cifra_ni_pide_clave(self):
        campo = EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            self.assertEqual(campo.get_prep_value(""), "")
            self.assertEqual(campo.from_db_value("", None, None), "")

    def test_leer_sin_la_clave_da_none_y_no_500(self):
        campo = EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            crudo = campo.get_prep_value("tok-mp")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            self.assertIsNone(campo.from_db_value(crudo, None, None))

    def test_escribir_sin_la_clave_sigue_levantando(self):
        campo = EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                campo.get_prep_value("tok-mp")

    def test_clave_rotada_da_none(self):
        campo = EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            crudo = campo.get_prep_value("tok-mp")
        with override_settings(MP_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.assertIsNone(campo.from_db_value(crudo, None, None))

    def test_el_default_de_google_sigue_levantando_sin_clave(self):
        campo = EncryptedTextField()
        with override_settings(GOOGLE_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            crudo = campo.get_prep_value("t")
        with override_settings(GOOGLE_TOKEN_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                campo.from_db_value(crudo, None, None)

    def test_deconstruct_omite_el_default_y_conserva_la_otra(self):
        self.assertNotIn("clave_setting", EncryptedTextField().deconstruct()[3])
        self.assertEqual(
            EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY").deconstruct()[3][
                "clave_setting"
            ],
            "MP_TOKEN_ENCRYPTION_KEY",
        )


class BanderaYSentryTests(SimpleTestCase):
    def test_la_bandera_esta_apagada_en_la_suite(self):
        self.assertIs(settings.TIENDA_MP_ENABLED, False)

    def test_sentry_no_manda_variables_locales(self):
        self.assertIs(settings.SENTRY_OPCIONES["include_local_variables"], False)
        self.assertIs(settings.SENTRY_OPCIONES["send_default_pii"], False)
