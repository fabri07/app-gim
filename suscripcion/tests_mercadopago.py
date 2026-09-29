import hashlib
import hmac
import json
import urllib.error
from datetime import datetime, timezone as tz
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.test import SimpleTestCase, override_settings

from suscripcion import mercadopago as mp

SECRETO = "secreto-de-prueba"


def _hmac(manifest, secreto=SECRETO):
    return hmac.new(secreto.encode(), manifest.encode(), hashlib.sha256).hexdigest()


class FirmaValidaTests(SimpleTestCase):
    def _valida(self, x_signature, data_id="123456", request_id="req-1", secret=SECRETO):
        return mp.firma_valida(
            secret, data_id=data_id, request_id=request_id, x_signature=x_signature
        )

    def test_firma_correcta(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1700000000;")
        self.assertTrue(self._valida(f"ts=1700000000,v1={v1}"))

    def test_tolera_espacios(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1700000000;")
        self.assertTrue(self._valida(f" ts=1700000000 , v1={v1} "))

    def test_un_caracter_distinto_en_v1(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1700000000;")
        malo = ("0" if v1[0] != "0" else "1") + v1[1:]
        self.assertFalse(self._valida(f"ts=1700000000,v1={malo}"))

    def test_data_id_alfanumerico_va_en_minusculas(self):
        v1 = _hmac("id:abc123xyz;request-id:req-1;ts:1700000000;")
        self.assertTrue(self._valida(f"ts=1700000000,v1={v1}", data_id="ABC123XYZ"))

    def test_sin_request_id_omite_el_segmento(self):
        v1 = _hmac("id:123456;ts:1700000000;")
        self.assertTrue(self._valida(f"ts=1700000000,v1={v1}", request_id=None))
        self.assertTrue(self._valida(f"ts=1700000000,v1={v1}", request_id=""))

    def test_ts_de_hace_un_anio_valida(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1665000000;")
        self.assertTrue(self._valida(f"ts=1665000000,v1={v1}"))

    def test_header_malformado_no_levanta(self):
        for basura in ("basura", "", None, "ts=1700000000", "v1=abc", ",,,", "ts=,v1="):
            with self.subTest(basura=basura):
                self.assertFalse(self._valida(basura))

    def test_secret_vacio(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1700000000;", secreto="")
        self.assertFalse(self._valida(f"ts=1700000000,v1={v1}", secret=""))

    def test_otro_secret(self):
        v1 = _hmac("id:123456;request-id:req-1;ts:1700000000;", secreto="otro")
        self.assertFalse(self._valida(f"ts=1700000000,v1={v1}"))


class PedirTests(SimpleTestCase):
    def test_bajo_testing_levanta_runtime_error(self):
        with self.assertRaises(RuntimeError):
            mp._pedir("GET", "/v1/payments/1")

    @override_settings(TESTING=False, MERCADOPAGO_ACCESS_TOKEN="TEST-token-secreto-123")
    def test_http_error_no_filtra_el_token_ni_el_cuerpo(self):
        error = urllib.error.HTTPError(
            "https://x", 401, "no", {}, BytesIO(b'{"detalle": "cuerpo-sensible"}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(mp.ErrorDeMercadoPago) as ctx:
                mp._pedir("GET", "/v1/payments/1")
        mensaje = str(ctx.exception)
        self.assertIn("401", mensaje)
        self.assertIn("/v1/payments/1", mensaje)
        self.assertNotIn("token-secreto", mensaje)
        self.assertNotIn("cuerpo-sensible", mensaje)

    @override_settings(TESTING=False, MERCADOPAGO_ACCESS_TOKEN="TEST-123")
    def test_url_error_y_json_invalido(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp._pedir("GET", "/x")
        respuesta = mock.MagicMock()
        respuesta.__enter__.return_value.read.return_value = b"no es json"
        with mock.patch("urllib.request.urlopen", return_value=respuesta):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp._pedir("GET", "/x")

    @override_settings(TESTING=False, MERCADOPAGO_ACCESS_TOKEN="TEST-123")
    def test_timeout(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp._pedir("GET", "/x")

    @override_settings(TESTING=False, MERCADOPAGO_ACCESS_TOKEN="TEST-123")
    def test_manda_headers_y_devuelve_json(self):
        respuesta = mock.MagicMock()
        respuesta.__enter__.return_value.read.return_value = b'{"ok": 1}'
        with mock.patch("urllib.request.urlopen", return_value=respuesta) as uo:
            salida = mp._pedir("POST", "/x", cuerpo={"a": 1}, clave_idempotencia="k")
        self.assertEqual(salida, {"ok": 1})
        pedido = uo.call_args.args[0]
        self.assertEqual(pedido.get_header("Authorization"), "Bearer TEST-123")
        self.assertEqual(pedido.get_header("X-idempotency-key"), "k")
        self.assertEqual(pedido.data, b'{"a": 1}')


@override_settings(MERCADOPAGO_ACCESS_TOKEN="APP_USR-real")
class CrearPreferenciaTests(SimpleTestCase):
    def _crear(self, **extra):
        args = dict(
            titulo="TuGimApp",
            monto_ars=Decimal("15000.50"),
            referencia_externa="ref-1",
            url_retorno="https://www.tugimapp.com/x/",
            url_notificacion="https://www.tugimapp.com/hook/",
            expira=datetime(2026, 10, 1, 12, 0, tzinfo=tz.utc),
            ahora=datetime(2026, 9, 29, 9, 30, 15, tzinfo=tz.utc),
        )
        args.update(extra)
        respuesta = {
            "id": "pref-1",
            "init_point": "https://real",
            "sandbox_init_point": "https://sandbox",
        }
        with mock.patch.object(mp, "_pedir", return_value=respuesta) as pedir:
            salida = mp.crear_preferencia(**args)
        return salida, pedir

    def test_payload(self):
        salida, pedir = self._crear()
        self.assertEqual(salida, {"id": "pref-1", "init_point": "https://real"})
        self.assertEqual(pedir.call_args.args, ("POST", "/checkout/preferences"))
        self.assertEqual(pedir.call_args.kwargs["clave_idempotencia"], "ref-1")
        p = pedir.call_args.kwargs["cuerpo"]
        self.assertIsInstance(p["items"][0]["unit_price"], float)
        self.assertEqual(p["items"][0]["unit_price"], 15000.5)
        self.assertIs(p["binary_mode"], True)
        self.assertIs(p["expires"], True)
        self.assertEqual(p["external_reference"], "ref-1")
        self.assertEqual(
            p["payment_methods"]["excluded_payment_types"],
            [{"id": "ticket"}, {"id": "atm"}],
        )
        self.assertEqual(p["expiration_date_from"], "2026-09-29T09:30:15.000+00:00")
        self.assertEqual(p["expiration_date_to"], "2026-10-01T12:00:00.000+00:00")
        self.assertNotIn("payer", p)
        json.dumps(p)  # serializable: un Decimal rompería acá

    def test_https_manda_auto_return_y_notification_url(self):
        _, pedir = self._crear()
        p = pedir.call_args.kwargs["cuerpo"]
        self.assertEqual(p["auto_return"], "approved")
        self.assertEqual(p["notification_url"], "https://www.tugimapp.com/hook/")

    def test_http_no_los_manda(self):
        _, pedir = self._crear(
            url_retorno="http://localhost:8000/x/",
            url_notificacion="http://localhost:8000/h/",
        )
        p = pedir.call_args.kwargs["cuerpo"]
        self.assertNotIn("auto_return", p)
        self.assertNotIn("notification_url", p)

    def test_email_pagador(self):
        _, pedir = self._crear(email_pagador="a@b.com")
        self.assertEqual(pedir.call_args.kwargs["cuerpo"]["payer"], {"email": "a@b.com"})

    @override_settings(MERCADOPAGO_ACCESS_TOKEN="TEST-123")
    def test_token_de_prueba_usa_sandbox(self):
        salida, _ = self._crear()
        self.assertEqual(salida["init_point"], "https://sandbox")

    def test_respuesta_sin_link_es_error(self):
        with mock.patch.object(mp, "_pedir", return_value={"id": "x"}):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp.crear_preferencia(
                    titulo="t",
                    monto_ars=1,
                    referencia_externa="r",
                    url_retorno="https://a",
                    url_notificacion="https://b",
                    expira=datetime(2026, 10, 1, tzinfo=tz.utc),
                )


class ConsultasTests(SimpleTestCase):
    def test_consultar_pago(self):
        with mock.patch.object(mp, "_pedir", return_value={"id": 9}) as pedir:
            self.assertEqual(mp.consultar_pago(9), {"id": 9})
        self.assertEqual(pedir.call_args.args, ("GET", "/v1/payments/9"))

    def test_buscar_pagos(self):
        with mock.patch.object(mp, "_pedir", return_value={"results": [{"id": 1}]}) as pedir:
            self.assertEqual(mp.buscar_pagos("ref 1"), [{"id": 1}])
        self.assertEqual(
            pedir.call_args.args,
            (
                "GET",
                "/v1/payments/search?external_reference=ref+1&sort=date_created&criteria=desc",
            ),
        )

    def test_errores_se_propagan(self):
        with mock.patch.object(mp, "_pedir", side_effect=mp.ErrorDeMercadoPago("x")):
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp.consultar_pago(1)
            with self.assertRaises(mp.ErrorDeMercadoPago):
                mp.buscar_pagos("r")


class ResumenTests(SimpleTestCase):
    def test_descarta_payer_y_extras(self):
        pago = {
            "id": 1,
            "status": "approved",
            "transaction_amount": 10.5,
            "payer": {"email": "x@y.com"},
            "card": {"last_four_digits": "1234"},
            "external_reference": "r",
        }
        self.assertEqual(
            mp.resumen_de_pago(pago),
            {
                "id": 1,
                "status": "approved",
                "transaction_amount": 10.5,
                "external_reference": "r",
            },
        )
