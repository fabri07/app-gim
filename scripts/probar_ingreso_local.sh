#!/usr/bin/env bash
# Ejercita la puerta de punta a punta contra un `runserver`, con curl.
#
# Es la verificación end-to-end del contrato de `docs/integracion-control-de-ingreso.md`
# mientras no haya hardware: 401 -> veredicto online -> lote en diferido ->
# el mismo lote otra vez (idempotencia) -> un 400 por forma -> padrón con y
# sin If-None-Match.
#
# Necesita un lector dado de alta (su clave se ve una sola vez, al crearlo en
# «Lectores de la puerta») y el valor del QR de un alumno ACTIVO del mismo
# gimnasio (Shell: `Credencial.objects.get(alumno__pk=..., tipo="qr").valor`).
#
# Uso:
#   PUENTE_INGRESO_TOKEN=<clave> ./scripts/probar_ingreso_local.sh <valor-del-qr> [url]
#
# La URL por defecto es http://127.0.0.1:8000. No escribe nada fuera de la
# base del servidor al que le pega: usalo contra una base de prueba.

set -uo pipefail

QR="${1:?falta el valor del QR de un alumno activo}"
URL="${2:-http://127.0.0.1:8000}"
TOKEN="${PUENTE_INGRESO_TOKEN:?falta PUENTE_INGRESO_TOKEN con la clave del lector}"
EVENTOS="$URL/ingreso/v1/eventos/"
PADRON="$URL/ingreso/v1/padron/"
ID="prueba-$(date +%s)-$$"

# Diez minutos atrás, con zona (BSD date en macOS, GNU date en Linux).
HACE_10_MIN="$(date -v-10M +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || date -d '-10 min' +%Y-%m-%dT%H:%M:%S%z)"

paso() { printf '\n== %s\n' "$*"; }

# curl que imprime el status y el cuerpo. -sS: callado salvo errores de red.
pedir() { curl -sS -w '\n[HTTP %{http_code}]\n' "$@"; }

paso "1. Sin clave: 401"
pedir -X POST "$EVENTOS" -H 'Content-Type: application/json' -d '{"eventos": []}'

paso "2. Clave inventada: 401 (no dice si el lector existe)"
pedir -X POST "$EVENTOS" -H 'Authorization: Bearer no-es-una-clave' \
  -H 'Content-Type: application/json' -d '{"eventos": []}'

paso "3. Online: el equipo espera el veredicto (esquema en minúsculas: vale igual)"
pedir -X POST "$EVENTOS" -H "Authorization: bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"eventos\": [{\"tipo\": \"qr\", \"valor\": \"$QR\", \"sentido\": \"entrada\", \"id_evento_equipo\": \"$ID-online\"}]}"

paso "4. Online, reintento con el MISMO id: devuelve el veredicto guardado"
pedir -X POST "$EVENTOS" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"eventos\": [{\"tipo\": \"qr\", \"valor\": \"$QR\", \"id_evento_equipo\": \"$ID-online\"}]}"

LOTE="{\"eventos\": [
  {\"tipo\": \"qr\", \"valor\": \"$QR\", \"sentido\": \"salida\", \"momento\": \"$HACE_10_MIN\", \"id_evento_equipo\": \"$ID-dif-1\", \"abierto_por_el_equipo\": true},
  {\"tipo\": \"qr\", \"valor\": \"no-existe\", \"momento\": \"$HACE_10_MIN\", \"id_evento_equipo\": \"$ID-dif-2\", \"abierto_por_el_equipo\": true},
  {\"tipo\": \"qr\", \"valor\": \"$QR\", \"abierto_por_el_equipo\": true},
  {\"tipo\": \"rfid\", \"valor\": \"x\", \"id_evento_equipo\": 8821}
]}"

paso "5. Lote en diferido: dos válidos, uno sin id (inválido) y un tipo desconocido (inválido)"
pedir -X POST "$EVENTOS" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "$LOTE"

paso "6. El MISMO lote otra vez: mismos veredictos, nada se registra dos veces"
pedir -X POST "$EVENTOS" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "$LOTE"

paso "7. Forma inválida: 400"
pedir -X POST "$EVENTOS" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"lecturas": []}'

paso "8. Padrón: 200 con ETag"
CABECERAS="$(mktemp)"
pedir -D "$CABECERAS" "$PADRON" -H "Authorization: Bearer $TOKEN"
ETAG="$(grep -i '^etag:' "$CABECERAS" | cut -d' ' -f2- | tr -d '\r')"
grep -iE '^(etag|cache-control):' "$CABECERAS"
rm -f "$CABECERAS"

paso "9. Padrón con If-None-Match: $ETAG -> 304 sin cuerpo"
pedir -o /dev/null "$PADRON" -H "Authorization: Bearer $TOKEN" -H "If-None-Match: $ETAG"

paso "10. Padrón con la versión debilitada por un proxy (W/...): también 304"
pedir -o /dev/null "$PADRON" -H "Authorization: Bearer $TOKEN" -H "If-None-Match: W/$ETAG"
