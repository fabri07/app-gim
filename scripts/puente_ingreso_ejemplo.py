#!/usr/bin/env python3
"""Puente de EJEMPLO entre un lector de la puerta y la API de control de ingreso.

Es la referencia de cómo se consume el contrato descrito en
`docs/integracion-control-de-ingreso.md`. No habla con ningún fabricante (eso
depende del equipo: ISAPI de Hikvision, SDK de ZKTeco...): la parte del equipo
es `EquipoDeEjemplo`, que solo imprime lo que habría que hacer. Lo que sí hace
de verdad es lo que el contrato exige:

- baja el padrón con `If-None-Match` y lo guarda en disco, para decidir sin
  internet;
- reporta cada lectura ONLINE con un timeout corto y, ante un TIMEOUT, un 429
  o un 5xx, decide con el padrón local y, si el equipo abrió, encola el evento
  para subirlo EN DIFERIDO **con el mismo `id_evento_equipo`** (si la app llegó a registrarlo,
  el reintento devuelve el veredicto guardado en vez de un «duplicado»);
- sube la cola en lotes de a lo sumo 100, respetando `Retry-After`, y nunca
  reintenta en bucle.

**Solo biblioteca estándar** (`urllib`, `json`): tiene que correr en la PC de
un gimnasio sin `pip install`. Lo verifica
`ingreso.tests.PuenteDeEjemploTests.test_solo_importa_la_biblioteca_estandar`.

Configuración: la URL y la clave del lector entran por argumento o por las
variables de entorno `PUENTE_INGRESO_URL` y `PUENTE_INGRESO_TOKEN`, que son
de ESTE script y no de la app (la app no lee ninguna variable nueva). Preferí
la variable de entorno para la clave: un argumento queda a la vista de
cualquiera que liste los procesos de la PC.

Uso:
    export PUENTE_INGRESO_URL=https://www.tugimapp.com
    export PUENTE_INGRESO_TOKEN=...            # la clave del lector
    python3 puente_ingreso_ejemplo.py padron   # un ciclo de padrón
    python3 puente_ingreso_ejemplo.py leer qr <valor>
    python3 puente_ingreso_ejemplo.py leer facial 42 --sin-red
    python3 puente_ingreso_ejemplo.py subir    # sube la cola de diferidos
    python3 puente_ingreso_ejemplo.py correr   # bucle: lecturas por stdin
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime

RUTA_EVENTOS = "/ingreso/v1/eventos/"
RUTA_PADRON = "/ingreso/v1/padron/"

TIMEOUT_ONLINE = 2.0
"""Segundos que se espera el veredicto online. Un molinete no puede tener a
alguien parado más que esto: pasado el timeout decide con el padrón local."""

TIMEOUT_PADRON = 20.0
"""El padrón no apura a nadie en la puerta: puede tardar más."""

SEGUNDOS_ENTRE_POLLS = 120
"""Cada cuánto se baja el padrón. El padrón es una foto del último poll:
cuanto más seguido, menos tiempo abre offline a quien se atrasó (y menos
tiempo rechaza al que acaba de pagar)."""

MAX_EVENTOS_POR_LOTE = 100
"""El tope del contrato: más que esto por request es un 400."""

ESPERA_TRAS_FALLA_DE_RED = 30
ESPERA_MAXIMA = 15 * 60

MENSAJE_SIN_PADRON = "Sin conexión con el gimnasio. Pasá por el mostrador."
MENSAJE_LECTOR_SIN_PERMISO = "Este lector no está habilitado. Pasá por el mostrador."


# ---------------------------------------------------------------------------
# Lógica pura: sin red y sin disco. Es lo que se testea.
# ---------------------------------------------------------------------------


@dataclass
class Veredicto:
    permitido: bool
    resultado: str
    mensaje: str
    alumno: object = None
    #: True si decidió el padrón local (sin internet, 429 o timeout).
    offline: bool = False


def indice_del_padron(padron):
    """`(tipo, valor) -> fila`. El QR y el id facial van en claves separadas:
    en la app son únicos por `(tipo, valor)`, así que un id facial «42» y un QR
    «42» son dos personas distintas."""
    indice = {}
    for fila in (padron or {}).get("alumnos", []):
        if fila.get("qr"):
            indice[("qr", fila["qr"])] = fila
        if fila.get("id_facial"):
            indice[("facial", fila["id_facial"])] = fila
    return indice


def decidir_con_padron(padron, tipo, valor):
    """La decisión offline. Es la de la app MENOS reserva y antipassback (lo
    que el padrón declara en `reglas_no_incluidas`): con el padrón en la mano
    solo se puede saber si la credencial existe y si el alumno está habilitado.

    Los textos salen del propio padrón (`mensajes`), así la pantalla dice lo
    mismo online y offline."""
    if not padron:
        return Veredicto(False, "sin_credencial", MENSAJE_SIN_PADRON, offline=True)
    mensajes = padron.get("mensajes", {})
    fila = indice_del_padron(padron).get((tipo, valor.strip()))
    if fila is None:
        return Veredicto(
            False, "sin_credencial",
            mensajes.get("sin_credencial", MENSAJE_SIN_PADRON), offline=True,
        )
    if not fila.get("habilitado"):
        motivo = fila.get("motivo") or "deuda"
        return Veredicto(
            False, motivo, mensajes.get(motivo, MENSAJE_SIN_PADRON),
            fila.get("nombre"), offline=True,
        )
    # `replace` y no `format`: una llave suelta en el texto no puede romper
    # la puerta.
    saludo = mensajes.get("permitido", "Pasá.").replace(
        "{nombre}", fila.get("nombre") or ""
    )
    return Veredicto(True, "permitido", saludo, fila.get("nombre"), offline=True)


def cambios_para_el_equipo(anterior, nuevo):
    """Qué hacerle al equipo cuando cambia el padrón. Por `id` de alumno:

    - `altas`: aparecen, o cambiaron de credencial (QR regenerado, id facial
      nuevo), o vuelven a estar habilitados;
    - `bloqueos`: `habilitado: false` (deudor). **Se bloquea, no se borra**:
      vuelve apenas paga;
    - `bajas`: desaparecieron del padrón (dado de baja, credenciales
      revocadas). **Se borran del equipo, cara incluida**: si después lo
      reactivan, hay que volver a enrolarle la cara.
    """
    viejos = {f["id"]: f for f in (anterior or {}).get("alumnos", [])}
    nuevos = {f["id"]: f for f in (nuevo or {}).get("alumnos", [])}
    altas, bloqueos = [], []
    for pk, fila in nuevos.items():
        previa = viejos.get(pk)
        if not fila.get("habilitado"):
            if previa is None or previa.get("habilitado") or previa != fila:
                bloqueos.append(fila)
        elif previa != fila:
            altas.append(fila)
    bajas = [fila for pk, fila in viejos.items() if pk not in nuevos]
    return {"altas": altas, "bloqueos": bloqueos, "bajas": bajas}


def ahora_con_zona():
    return datetime.now().astimezone()


def nuevo_id_de_evento():
    """El id que el evento tiene EN el equipo. Se genera UNA vez, al leer, y
    viaja igual en el intento online y en cada reintento en diferido: es lo
    que hace idempotente el reintento. Hasta 64 caracteres."""
    return uuid.uuid4().hex


def segundos_de_retry_after(encabezados, por_defecto):
    try:
        return max(1, int(encabezados.get("Retry-After", por_defecto)))
    except (TypeError, ValueError):
        return por_defecto


# ---------------------------------------------------------------------------
# Transporte HTTP. Separado de la decisión para poder reemplazarlo en tests.
# ---------------------------------------------------------------------------


class ErrorDeRed(Exception):
    """Sin respuesta de la app: timeout, DNS, conexión rechazada. Para la
    puerta es lo mismo que no tener internet."""


def transporte_urllib(metodo, url, *, cuerpo=None, encabezados=None, timeout):
    """`(status, encabezados, cuerpo_bytes)`. Un 4xx/5xx es una respuesta, no
    una excepción; solo la falta de respuesta levanta `ErrorDeRed`."""
    pedido = urllib.request.Request(
        url, data=cuerpo, method=metodo, headers=dict(encabezados or {})
    )
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as respuesta:
            return respuesta.status, dict(respuesta.headers), respuesta.read()
    except urllib.error.HTTPError as error:
        # El 304 del padrón también llega por acá.
        return error.code, dict(error.headers or {}), error.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        raise ErrorDeRed(str(error)) from error


# ---------------------------------------------------------------------------
# Estado en disco (o en memoria, con ruta=None).
# ---------------------------------------------------------------------------


class Almacen:
    """Un JSON en disco. Escritura atómica (archivo temporal + `replace`): un
    corte de luz a mitad de escritura no puede dejar la cola corrupta."""

    def __init__(self, ruta, por_defecto):
        self.ruta = ruta
        self.valor = por_defecto
        if ruta and os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as archivo:
                self.valor = json.load(archivo)

    def guardar(self):
        if not self.ruta:
            return
        temporal = f"{self.ruta}.tmp"
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump(self.valor, archivo, ensure_ascii=False)
        os.replace(temporal, self.ruta)


# ---------------------------------------------------------------------------
# El puente
# ---------------------------------------------------------------------------


class Puente:
    def __init__(self, url_base, token, *, estado_dir=None,
                 transporte=transporte_urllib, reloj=time.monotonic,
                 timeout_online=TIMEOUT_ONLINE, equipo=None, log=print):
        self.url_base = url_base.rstrip("/")
        self.token = token
        self.transporte = transporte
        self.reloj = reloj
        self.timeout_online = timeout_online
        self.equipo = equipo or EquipoDeEjemplo(log)
        self.log = log
        ruta = (lambda n: os.path.join(estado_dir, n)) if estado_dir else (lambda n: None)
        if estado_dir:
            os.makedirs(estado_dir, exist_ok=True)
        # `{"etag": ..., "padron": {...}}`
        self._padron = Almacen(ruta("padron.json"), {"etag": None, "padron": None})
        # Lista de eventos listos para mandar tal cual, con su id ya fijo.
        self._cola = Almacen(ruta("cola.json"), [])
        #: Momento (según `reloj`) antes del cual no se vuelve a subir la cola.
        self.no_subir_antes_de = 0.0
        self.no_pollear_antes_de = 0.0
        self.no_online_antes_de = 0.0

    # -- helpers ------------------------------------------------------------

    @property
    def padron(self):
        return self._padron.valor.get("padron")

    @property
    def cola(self):
        return self._cola.valor

    def _encabezados(self, extra=None):
        encabezados = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        encabezados.update(extra or {})
        return encabezados

    def _post_eventos(self, eventos, timeout):
        cuerpo = json.dumps({"eventos": eventos}).encode()
        return self.transporte(
            "POST", self.url_base + RUTA_EVENTOS, cuerpo=cuerpo,
            encabezados=self._encabezados({"Content-Type": "application/json"}),
            timeout=timeout,
        )

    def _encolar(self, evento):
        self.cola.append(evento)
        self._cola.guardar()

    # -- padrón -------------------------------------------------------------

    def sincronizar_padron(self):
        """Un ciclo de poll. Devuelve el status (o `None` sin red)."""
        if self.reloj() < self.no_pollear_antes_de:
            return None
        extra = {}
        if self._padron.valor.get("etag"):
            extra["If-None-Match"] = self._padron.valor["etag"]
        try:
            status, encabezados, cuerpo = self.transporte(
                "GET", self.url_base + RUTA_PADRON,
                encabezados=self._encabezados(extra), timeout=TIMEOUT_PADRON,
            )
        except ErrorDeRed as error:
            self.log(f"padrón: sin red ({error}); sigo con la copia local")
            return None
        if status == 304:
            return status
        if status == 200:
            nuevo = json.loads(cuerpo)
            cambios = cambios_para_el_equipo(self.padron, nuevo)
            self.equipo.aplicar(cambios)
            self._padron.valor = {
                "etag": encabezados.get("ETag") or f'"{nuevo["version"]}"',
                "padron": nuevo,
            }
            self._padron.guardar()
            return status
        if status == 429:
            # Solo por el freno de intentos fallidos: un token válido no lo
            # alcanza. Se respeta `Retry-After` y no se insiste.
            espera = segundos_de_retry_after(encabezados, 60)
            self.no_pollear_antes_de = self.reloj() + espera
        self.log(f"padrón: la app respondió {status}; sigo con la copia local")
        return status

    # -- una lectura ----------------------------------------------------------

    def leer(self, tipo, valor, sentido="entrada", *, id_evento=None, sin_red=False):
        """Lo que pasa cuando alguien apoya el QR o la cara. Devuelve el
        `Veredicto` que el equipo muestra y según el cual abre o no."""
        id_evento = id_evento or nuevo_id_de_evento()
        momento = ahora_con_zona().isoformat(timespec="seconds")
        evento = {
            "tipo": tipo, "valor": valor, "sentido": sentido,
            "momento": momento, "id_evento_equipo": id_evento,
        }
        # Durante un 429 no se pregunta online: cada intento sería otro 429.
        if not sin_red and self.reloj() >= self.no_online_antes_de:
            try:
                status, encabezados, cuerpo = self._post_eventos(
                    [evento], self.timeout_online
                )
            except ErrorDeRed as error:
                self.log(f"online: sin respuesta ({error}); decido con el padrón")
            else:
                if status == 200:
                    r = json.loads(cuerpo)["resultados"][0]
                    return Veredicto(
                        r["permitido"], r["resultado"], r["mensaje"], r.get("alumno")
                    )
                if status in (401, 403):
                    # La app dice que este lector no tiene permiso (clave
                    # rotada, o el gimnasio lo desactivó). No se abre con el
                    # padrón: sería saltearse justo esa decisión.
                    self.log(f"online: {status}, revisá la clave o si el lector está activo")
                    return Veredicto(False, "lector_sin_permiso", MENSAJE_LECTOR_SIN_PERMISO)
                if status == 400:
                    # Un bug del puente, no algo que se arregle reintentando.
                    self.log(f"online: 400 {cuerpo[:200]!r}")
                    return Veredicto(False, "invalido", MENSAJE_SIN_PADRON)
                if status == 429:
                    espera = segundos_de_retry_after(encabezados, 60)
                    self.no_online_antes_de = self.reloj() + espera
                    self._esperar(espera)
                self.log(f"online: {status}; decido con el padrón")
        # Sin veredicto online: timeout, 429, 5xx, o sin red. Se decide con el
        # padrón y, si el equipo abrió, el evento se sube después EN DIFERIDO
        # con el MISMO id: si la app llegó a registrar el intento online antes
        # del timeout, el reintento devuelve ese veredicto y no duplica nada.
        #
        # Un rechazo offline NO se sube: el contrato solo sabe decir «el
        # equipo ya abrió» (`abierto_por_el_equipo: true`), y `false` significa
        # ONLINE -- la app lo decidiría de nuevo con la hora del servidor y
        # podría anotar como PERMITIDO un ingreso que no pasó. Queda en el log.
        veredicto = decidir_con_padron(self.padron, tipo, valor)
        if veredicto.permitido:
            self._encolar({**evento, "abierto_por_el_equipo": True})
        else:
            self.log(f"offline: rechazado {id_evento} ({veredicto.resultado}), no se sube")
        return veredicto

    # -- la cola --------------------------------------------------------------

    def subir_diferidos(self):
        """UNA pasada sobre la cola: nunca un bucle de reintentos. Lo que no
        se pudo subir queda para la próxima, después de la espera."""
        if not self.cola or self.reloj() < self.no_subir_antes_de:
            return 0
        subidos = 0
        while self.cola:
            # Cada evento de la cola ya lleva `abierto_por_el_equipo: true`,
            # que es lo que lo hace DIFERIDO, y su `momento` original.
            lote = self.cola[:MAX_EVENTOS_POR_LOTE]
            try:
                status, encabezados, cuerpo = self._post_eventos(lote, TIMEOUT_PADRON)
            except ErrorDeRed as error:
                self.log(f"diferidos: sin red ({error}); la cola queda para después")
                self._esperar(ESPERA_TRAS_FALLA_DE_RED)
                return subidos
            if status == 429:
                self._esperar(segundos_de_retry_after(encabezados, 60))
                self.log("diferidos: 429, respeto Retry-After")
                return subidos
            if status != 200:
                # 400: el lote tiene una forma que la app no acepta; 401/403:
                # la clave. Reintentar ya no lo arregla.
                self.log(f"diferidos: {status} {cuerpo[:200]!r}; reviso en un rato")
                self._esperar(ESPERA_MAXIMA)
                return subidos
            resultados = json.loads(cuerpo)["resultados"]
            for enviado, resultado in zip(lote, resultados):
                if resultado["resultado"] == "invalido":
                    # No se arregla reenviándolo (p. ej. más de 7 días): se
                    # descarta y queda en el log.
                    self.log(
                        f"diferidos: {enviado['id_evento_equipo']} descartado: "
                        f"{resultado.get('error')}"
                    )
            # Las respuestas vuelven en el orden del request: los primeros
            # `len(lote)` de la cola ya están (registrados o descartados).
            del self.cola[:len(lote)]
            self._cola.guardar()
            subidos += len(lote)
        return subidos

    def _esperar(self, segundos):
        self.no_subir_antes_de = self.reloj() + min(segundos, ESPERA_MAXIMA)


class EquipoDeEjemplo:
    """Lo que dependería del fabricante. Acá solo se imprime."""

    def __init__(self, log=print):
        self.log = log

    def aplicar(self, cambios):
        for fila in cambios["altas"]:
            self.log(f"equipo: dar de alta/habilitar {fila['id']} ({fila['nombre']})")
        for fila in cambios["bloqueos"]:
            self.log(f"equipo: BLOQUEAR {fila['id']} ({fila['nombre']}), no borrar")
        for fila in cambios["bajas"]:
            self.log(f"equipo: BORRAR {fila['id']} ({fila['nombre']}), cara incluida")


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------


def _argumentos(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=os.environ.get("PUENTE_INGRESO_URL"))
    parser.add_argument("--token", default=os.environ.get("PUENTE_INGRESO_TOKEN"))
    parser.add_argument("--estado", default="estado_puente",
                        help="carpeta donde se guardan el padrón y la cola")
    parser.add_argument("--timeout-online", type=float, default=TIMEOUT_ONLINE)
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("padron")
    leer = sub.add_parser("leer")
    leer.add_argument("tipo", choices=["qr", "facial"])
    leer.add_argument("valor")
    leer.add_argument("--sentido", default="entrada", choices=["entrada", "salida"])
    leer.add_argument("--id-evento", help="reusar un id (para probar un reintento)")
    leer.add_argument("--sin-red", action="store_true",
                      help="simular que no hay internet")
    sub.add_parser("subir")
    sub.add_parser("correr")
    args = parser.parse_args(argv)
    if not args.url or not args.token:
        parser.error("faltan la URL y la clave (--url/--token o "
                     "PUENTE_INGRESO_URL/PUENTE_INGRESO_TOKEN)")
    return args


def main(argv=None):
    args = _argumentos(argv)
    puente = Puente(args.url, args.token, estado_dir=args.estado,
                    timeout_online=args.timeout_online)
    if args.comando == "padron":
        status = puente.sincronizar_padron()
        n = len((puente.padron or {}).get("alumnos", []))
        print(f"padrón: status={status} alumnos={n} etag={puente._padron.valor.get('etag')}")
    elif args.comando == "leer":
        v = puente.leer(args.tipo, args.valor, args.sentido,
                        id_evento=args.id_evento, sin_red=args.sin_red)
        print(json.dumps({**v.__dict__, "en_cola": len(puente.cola)}, ensure_ascii=False))
    elif args.comando == "subir":
        n = puente.subir_diferidos()
        print(f"diferidos: subidos={n} pendientes={len(puente.cola)}")
    elif args.comando == "correr":
        # Simula el lector: cada línea de stdin es «qr VALOR» o «facial ID».
        puente.sincronizar_padron()
        proximo_poll = time.monotonic() + SEGUNDOS_ENTRE_POLLS
        for linea in sys.stdin:
            partes = linea.split()
            if len(partes) == 2 and partes[0] in ("qr", "facial"):
                v = puente.leer(*partes)
                print(("ABRE  " if v.permitido else "NIEGA ") + v.mensaje, flush=True)
            puente.subir_diferidos()
            if time.monotonic() >= proximo_poll:
                puente.sincronizar_padron()
                proximo_poll = time.monotonic() + SEGUNDOS_ENTRE_POLLS
    return 0


if __name__ == "__main__":
    sys.exit(main())
