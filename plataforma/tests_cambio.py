"""
Tests de `plataforma.cambio`: la cotización del dólar MEP y el pasaje a pesos.

Lo que de verdad importa acá no es el número: es que **el panel nunca se caiga
ni se cuelgue por una API de terceros**. Por eso todos los caminos de fallo
(API caída, respuesta con otra forma, timeout) tienen su test y todos terminan
en `None`, que las pantallas leen como "solo USD".

Los tests del camino real van con `override_settings(TESTING=False)` y
`_descargar` parcheado: bajo la suite `cotizacion_dolar()` corta antes de
tocar la red (mismo criterio que `TESTING` con R2 y el push), y eso también
se verifica con un test propio.
"""

from decimal import Decimal
from unittest.mock import patch
from urllib.error import URLError

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from plataforma import cambio

#: La forma real que devuelve `https://dolarapi.com/v1/dolares/bolsa`.
RESPUESTA_REAL = {
    "moneda": "USD",
    "casa": "bolsa",
    "nombre": "Bolsa",
    "compra": 1531.6,
    "venta": 1536.5,
    "fechaActualizacion": "2026-09-21T20:58:00.000Z",
}


class CotizacionBajoTestingTests(SimpleTestCase):
    """En la suite no se sale a la red por ningún motivo."""

    def test_bajo_testing_devuelve_none_sin_llamar_a_la_api(self):
        with patch("plataforma.cambio._descargar") as descargar:
            self.assertIsNone(cambio.cotizacion_dolar())

        descargar.assert_not_called()


@override_settings(TESTING=False)
class CotizacionDolarTests(SimpleTestCase):
    def setUp(self):
        # La cotización se cachea 24 h: sin limpiar, el primer test que corra
        # le deja el valor (o el fallo) cacheado a todos los demás.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_lee_compra_venta_y_la_fecha_de_actualizacion(self):
        with patch("plataforma.cambio._descargar", return_value=RESPUESTA_REAL):
            cotizacion = cambio.cotizacion_dolar()

        self.assertEqual(cotizacion["compra"], Decimal("1531.6"))
        self.assertEqual(cotizacion["venta"], Decimal("1536.5"))
        self.assertEqual(cotizacion["fuente"], cambio.FUENTE)

    def test_la_fecha_llega_como_datetime_con_zona(self):
        """`fechaActualizacion` viene en UTC con la `Z` de ISO. Tiene que
        volver *aware*: con un datetime naive, Django no puede pasarlo a hora
        local al renderizar y la pantalla mostraría las 20:58 de Londres como
        si fueran las de Buenos Aires."""
        with patch("plataforma.cambio._descargar", return_value=RESPUESTA_REAL):
            fecha = cambio.cotizacion_dolar()["fecha"]

        self.assertIsNotNone(fecha.tzinfo)
        self.assertEqual(fecha.utcoffset().total_seconds(), 0)
        self.assertEqual((fecha.hour, fecha.minute), (20, 58))

    def test_una_api_caida_no_rompe_el_panel(self):
        # `assertLogs` cumple dos funciones: fija que el fallo queda
        # registrado (si no, una API muerta es invisible hasta que alguien
        # note que el panel no muestra pesos) y evita que el traceback
        # ensucie la salida de la suite.
        with patch("plataforma.cambio._descargar", side_effect=URLError("caída")):
            with self.assertLogs("plataforma.cambio", "WARNING"):
                self.assertIsNone(cambio.cotizacion_dolar())

    def test_una_respuesta_con_otra_forma_tampoco_rompe(self):
        """El día que dolarapi cambie los nombres de sus campos, el panel
        tiene que mostrarse en USD, no devolver un 500."""
        with patch("plataforma.cambio._descargar", return_value={"otra": "cosa"}):
            with self.assertLogs("plataforma.cambio", "WARNING"):
                self.assertIsNone(cambio.cotizacion_dolar())

    def test_el_fallo_queda_cacheado_para_no_golpear_una_api_muerta(self):
        """Sin cachear el fallo, cada carga del panel se come el timeout de 3
        segundos mientras la API esté caída."""
        with patch(
            "plataforma.cambio._descargar", side_effect=URLError("caída")
        ) as descargar:
            with self.assertLogs("plataforma.cambio", "WARNING"):
                self.assertIsNone(cambio.cotizacion_dolar())
                self.assertIsNone(cambio.cotizacion_dolar())

        self.assertEqual(descargar.call_count, 1)

    def test_el_exito_queda_cacheado(self):
        with patch(
            "plataforma.cambio._descargar", return_value=RESPUESTA_REAL
        ) as descargar:
            primera = cambio.cotizacion_dolar()
            segunda = cambio.cotizacion_dolar()

        self.assertEqual(descargar.call_count, 1)
        self.assertEqual(primera, segunda)


class PesosTests(SimpleTestCase):
    def test_convierte_al_valor_de_venta(self):
        cotizacion = {"venta": Decimal("1536.5")}

        self.assertEqual(cambio.pesos(10, cotizacion), Decimal("15365.00"))

    def test_redondea_a_dos_decimales(self):
        """Es plata: un `Decimal` con seis decimales en la pantalla (y peor,
        en la base) no es un monto, es un residuo de la multiplicación."""
        cotizacion = {"venta": Decimal("1536.555")}

        self.assertEqual(cambio.pesos(15, cotizacion), Decimal("23048.33"))

    def test_sin_cotizacion_no_hay_monto_en_pesos(self):
        self.assertIsNone(cambio.pesos(10, None))
