"""
Cotización del dólar MEP, para leer en pesos lo que se factura en dólares.

El precio de la plataforma está pactado en USD, pero los gimnasios pagan por
transferencia en pesos. Sin esto, el superadmin tiene que abrir otra pestaña y
hacer la cuenta a mano cada vez que registra un pago.

**Regla que ordena todo este módulo: el panel no puede caerse ni colgarse por
una API de terceros.** `cotizacion_dolar()` no levanta nunca y devuelve `None`
ante cualquier problema (API caída, timeout, respuesta con otra forma); las
pantallas leen ese `None` como "mostrar solo USD". El fallo además se cachea
unos minutos, porque si no cada carga del panel se come el timeout entero
mientras la API esté muerta.

Se usa `urllib` de la biblioteca estándar y no `requests`: no es dependencia
del proyecto y no vale la pena agregarla por una sola llamada GET.
"""

import json
import logging
import urllib.request
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Dólar "bolsa" es el MEP. Decisión cerrada con el dueño del producto.
URL = "https://dolarapi.com/v1/dolares/bolsa"

#: Quién publica el número. Se muestra en pantalla: un tipo de cambio sin
#: fuente es un número que nadie puede verificar.
FUENTE = "dolarapi.com"

#: Segundos de espera. Corto a propósito: esto corre dentro del request del
#: panel, y el único worker de gunicorn tiene 30 s antes de que lo maten.
TIMEOUT = 3

#: La cotización cambia una vez por día hábil; no hace falta pedirla más.
TTL = 24 * 3600

#: Cuánto dura el recuerdo de un fallo. Diez minutos: suficiente para que una
#: caída no se pague en cada carga, corto para que el panel se recupere solo.
TTL_FALLO = 600

CLAVE_CACHE = "plataforma:cotizacion_dolar"

#: Qué se guarda en la cache cuando la API falló. Es un string y la
#: comparación es por igualdad, no por identidad: la cache serializa lo que
#: guarda, así que un objeto centinela vuelve como una copia distinta.
_FALLO = "sin-cotizacion"


def _descargar():
    """El único punto que toca la red. Aparte para poder parchearlo en los
    tests sin simular todo `urllib`."""
    with urllib.request.urlopen(URL, timeout=TIMEOUT) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def _fecha_utc(texto):
    """`"2026-09-21T20:58:00.000Z"` como datetime **aware** en UTC.

    Aware y no naive: con un datetime sin zona, Django no puede pasarlo a hora
    local al renderizar y la pantalla mostraría las 20:58 de Londres como si
    fueran las de Buenos Aires. La `Z` se reemplaza a mano en vez de confiar
    en que `fromisoformat` la acepte, que recién es así desde Python 3.11.
    """
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _leer(crudo):
    """Traduce la respuesta de la API a lo que usan las pantallas."""
    return {
        "compra": Decimal(str(crudo["compra"])),
        "venta": Decimal(str(crudo["venta"])),
        "fecha": _fecha_utc(crudo["fechaActualizacion"]),
        "fuente": FUENTE,
    }


def cotizacion_dolar():
    """`{compra, venta, fecha, fuente}` del dólar MEP, o `None`.

    `None` significa "hoy no hay cotización": ni el panel ni el formulario de
    pago dependen de ella, simplemente se muestran en dólares.

    Bajo `TESTING` devuelve `None` sin tocar la red, mismo criterio que el
    storage de R2 y el push: `manage.py test` no sale a internet por ningún
    motivo.
    """
    if settings.TESTING:
        return None

    guardado = cache.get(CLAVE_CACHE)
    if guardado is not None:
        return None if guardado == _FALLO else guardado

    try:
        cotizacion = _leer(_descargar())
    except Exception:
        # Se atrapa `Exception` a propósito y no solo `URLError`: un JSON
        # inválido, una clave que la API renombró o una fecha con otro
        # formato son todos el mismo caso de negocio ("no hay cotización") y
        # ninguno puede devolver un 500 en el panel.
        logger.warning("No se pudo leer la cotización del dólar", exc_info=True)
        cache.set(CLAVE_CACHE, _FALLO, TTL_FALLO)
        return None

    cache.set(CLAVE_CACHE, cotizacion, TTL)
    return cotizacion


def pesos(monto_usd, cotizacion):
    """El monto en pesos al valor de **venta**, o `None` sin cotización.

    Venta y no compra: es el precio al que hay que comprar los dólares, que es
    lo que el gimnasio realmente tiene que poner.

    Redondea a dos decimales porque es plata: un `Decimal` con seis decimales
    no es un monto, es el residuo de la multiplicación.
    """
    if not cotizacion:
        return None
    total = Decimal(monto_usd) * cotizacion["venta"]
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
