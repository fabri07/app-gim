"""Frenos del endpoint público de la puerta.

Son dos, con dos lógicas distintas, y la regla que ordena el módulo es la de la
Tarea 9: **un freno que corta una lectura online deja la puerta cerrada.** Por
eso ninguno mira `estado_cuenta` y ninguno toca el padrón.

**1. Freno por IP del camino de FALLO** (sin token, o con uno que no matchea).
Vive en el cache de Django. **Honesto sobre sus límites**: no hay setting
`CACHES` en `config/`, así que el backend es `LocMemCache` -- por proceso y
volátil. Un reinicio lo pone en cero, y el día que alguien arranque gunicorn
con `--workers 2` cada worker cuenta por su lado (el límite efectivo se
duplica, EN SILENCIO). Es un badén, no una garantía: la defensa real es
Cloudflare adelante. Alcanza para lo que tiene que hacer, que es que un
atacante sin llave no nos cueste ni la query del token.

**Un lector legítimo no recibe nunca este 429**, aunque su IP esté frenada: la
IP de un gimnasio es compartida (NAT) con cualquier cosa mal configurada del
mismo edificio -- un puente viejo con el token rotado alcanza. Para no pagar la
query del token con la IP frenada y aun así dejar pasar al bueno, se recuerda
en el mismo cache el hash de cada token que autenticó bien (`recordar_lector`).
Riesgo aceptado: un lector legítimo que todavía NO está en ese recuerdo (el
recién instalado; tras un reinicio el contador también vuelve a cero, así que
ése no cuenta) y comparte IP con algo que falla más de `MAX_FALLOS_POR_MINUTO`
veces por minuto espera a que termine la ventana, y puede no llegar a entrar
si lo que falla no afloja. El 429 lleva `Retry-After`; la salida es apagar lo
que falla (el cartel del 429 lo dice).

**2. Tope diario de eventos NUEVOS por lector**, contado en la base
(`Dispositivo.eventos_hoy`/`eventos_hoy_fecha`): el respaldo durable, que
sobrevive a los reinicios y a los workers. Acota lo que un token robado, o un
puente que reenvía en bucle, puede escribir en `EventoIngreso`. **Solo en
eventos, nunca en el padrón**: el padrón no escribe nada, y cortarle el poll a
un lector hace que la puerta abra hoy y deje de abrir mañana.
"""

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db.models import Case, F, Value, When
from django.utils import timezone

from ingreso.models import Dispositivo
from solicitudes.antispam import hash_de as _hash_con_pimienta
from solicitudes.antispam import ip_de

__all__ = [
    "ip_de", "demasiados_intentos_fallidos", "anotar_fallo",
    "lector_conocido", "recordar_lector", "cupo_diario_excedido",
    "sumar_al_cupo", "segundos_hasta_medianoche",
]

MAX_FALLOS_POR_MINUTO = 30
"""Un puente mal configurado (token viejo, mal copiado) pollea el padrón cada
pocos minutos y postea una vez por lectura: queda lejos. Treinta llaves por
minuto no es un número con el que se adivine un token de 256 bits, ni se lo
pretende: el freno es de costo, no de adivinanza."""

SEGUNDOS_VENTANA_FALLOS = 60

SEGUNDOS_RECUERDO_LECTOR = 24 * 60 * 60
"""Se renueva en cada request autenticado; un lector sano pollea el padrón
cada pocos minutos, así que no se le vence nunca."""

MAX_EVENTOS_POR_DIA = 20_000
"""Por lector y por día local. Un gimnasio de 600 alumnos que entran y salen
todos los días, con rechazos y dobles lecturas, no pasa de 2.000: el tope deja
un margen de diez veces para que una puerta real no lo toque nunca. Los
reintentos idempotentes (mismo `id_evento_equipo`) no suman: no escriben."""


def _clave_ip(ip):
    # Hasheada: una IPv6 tiene `:` y una clave de cache no tiene por qué
    # aceptarlo en todos los backends. Es la misma función que usa el
    # formulario público para no guardar la IP en claro.
    return f"ingreso:fallos:{_hash_con_pimienta(ip)}"


def _clave_lector(token_hash):
    return f"ingreso:lector:{token_hash}"


def demasiados_intentos_fallidos(ip):
    """True si esta IP ya gastó sus fallas de la ventana. Sin IP (un request
    sin `REMOTE_ADDR`, que solo arma un test) no se frena."""
    if not ip:
        return False
    return (cache.get(_clave_ip(ip)) or 0) >= MAX_FALLOS_POR_MINUTO


def anotar_fallo(ip):
    """Suma una falla a la ventana de esta IP. Mismo patrón que
    `solicitudes/antispam.py::rate_limit_excedido`: `add` abre la ventana (y
    fija su vencimiento; `incr` no lo corre) y `incr` es atómico dentro del
    proceso."""
    if not ip:
        return
    clave = _clave_ip(ip)
    if cache.add(clave, 1, timeout=SEGUNDOS_VENTANA_FALLOS):
        return
    try:
        cache.incr(clave)
    except ValueError:
        # La clave venció entre el add y el incr: ventana nueva.
        cache.set(clave, 1, timeout=SEGUNDOS_VENTANA_FALLOS)


def lector_conocido(token_hash):
    return bool(token_hash) and cache.get(_clave_lector(token_hash)) is not None


def recordar_lector(token_hash):
    cache.set(_clave_lector(token_hash), 1, timeout=SEGUNDOS_RECUERDO_LECTOR)


def cupo_diario_excedido(dispositivo):
    """Lee el contador que ya vino con el dispositivo al autenticar: 0
    queries. Se compara contra el día LOCAL (`localdate`): entre las 21:00 y
    las 23:59 la fecha UTC ya es la de mañana.

    Lee un valor que puede estar un request atrasado: dos requests simultáneos
    pueden pasar los dos con el cupo justo, y el tope se excede en a lo sumo un
    lote (`MAX_EVENTOS_POR_LOTE`). La SUMA sí es atómica (ver `sumar_al_cupo`),
    así que no se pierde ninguna cuenta."""
    return (
        dispositivo.eventos_hoy_fecha == timezone.localdate()
        and dispositivo.eventos_hoy >= MAX_EVENTOS_POR_DIA
    )


def sumar_al_cupo(dispositivo, cantidad):
    """Un solo `UPDATE` atómico, sin leer antes: si la fecha guardada es la de
    hoy suma, si no arranca de `cantidad`. `update()` y no `save()`: es
    telemetría, no una edición, y no tiene por qué tocar `modificado`."""
    if not cantidad:
        return
    hoy = timezone.localdate()
    Dispositivo.objects.filter(pk=dispositivo.pk).update(
        eventos_hoy=Case(
            When(eventos_hoy_fecha=hoy, then=F("eventos_hoy") + cantidad),
            default=Value(cantidad),
        ),
        eventos_hoy_fecha=hoy,
    )


def segundos_hasta_medianoche():
    """Para el `Retry-After` del tope diario: se reinicia a medianoche local."""
    ahora = timezone.localtime()
    manana = timezone.make_aware(
        datetime.combine(ahora.date() + timedelta(days=1), time.min)
    )
    return max(1, int((manana - ahora).total_seconds()))
