#!/usr/bin/env bash
# Mide de dónde sale el tiempo de una carga de página en producción.
#
# La idea: comparar URLs con distinta CANTIDAD DE QUERIES pero el mismo
# recorrido de red. La diferencia entre ellas es el costo de hablar con la
# base; lo que queda es el piso de red + Django.
#
#   TTFB ≈ piso_de_red  +  (queries + 1) × RTT_a_la_base
#
# El "+1" es el chequeo de salud de la conexión (`conn_health_checks=True` en
# config/db.py, obligatorio contra el scale-to-zero de Neon).
#
# Uso:  ./scripts/medir_velocidad.sh [dominio] [slug-de-gimnasio]

set -uo pipefail
HOST="${1:-https://www.tugimapp.com}"
SLUG="${2:-vida-plena}"
REPS=5

ttfb() {  # mediana de REPS mediciones, en milisegundos
  local u=$1 vals=()
  for _ in $(seq $REPS); do
    vals+=("$(curl -s -o /dev/null --compressed -w '%{time_starttransfer}' "$u")")
  done
  printf '%s\n' "${vals[@]}" | sort -n | awk -v n=$REPS 'NR==int((n+1)/2){printf "%.0f", $1*1000}'
}

echo "=== $HOST  ($(date '+%H:%M:%S')) ==="
echo
echo "-- de dónde viene la respuesta --"
curl -s -D - -o /dev/null --compressed "$HOST/privacidad/" \
  | grep -iE '^(server|cf-ray|x-render-origin-server):' | sed 's/^/   /'
DOM=$(echo "$HOST" | sed 's#https\?://##')
ORIGIN=$(dig +short "$DOM" CNAME; dig +short "$DOM")
echo "   origin real:  $(echo "$ORIGIN" | grep -m1 'origin\.onrender' || echo '(no visible por DNS: miralo en el dashboard de Render)')"
echo
echo "-- handshake (una vez por conexión, no por página) --"
curl -s -o /dev/null -w "   dns %{time_namelookup}s | tcp %{time_connect}s | tls %{time_appconnect}s\n" "$HOST/privacidad/"
echo
echo "-- TTFB por cantidad de queries (mediana de $REPS) --"
P0=$(ttfb "$HOST/privacidad/")          # TemplateView, 0 queries
P1=$(ttfb "$HOST/g/$SLUG/login/")       # 1 query
P2=$(ttfb "$HOST/g/$SLUG/")             # 2 queries
printf "   0 queries  %5s ms   (piso: red hasta el origin + Django)\n" "$P0"
printf "   1 query    %5s ms\n" "$P1"
printf "   2 queries  %5s ms\n" "$P2"
echo
RTT=$(( (P2 - P0) / 3 ))
echo "-- diagnóstico --"
printf "   RTT estimado Django→base: ~%s ms por query\n" "$RTT"
if [ "$RTT" -gt 40 ]; then
  echo "   ⚠  ALTO. La base está en OTRA REGIÓN que el servidor web."
  echo "      Cada pantalla paga esto una vez por query:"
  for q in 5 7 11 28; do
    printf "        %2s queries → ~%s ms\n" "$q" "$(( P0 + (q + 1) * RTT ))"
  done
  echo "      (28 queries = el panel de inicio; 11 = ficha de alumno)"
else
  echo "   ✓  Servidor web y base en la misma región."
fi
echo
echo "-- ¿el CDN cachea los estáticos? --"
CSS=$(curl -s --compressed "$HOST/g/$SLUG/" | grep -oE '/static/css/app[^"]*\.css' | head -1)
if [ -n "$CSS" ]; then
  curl -s -D - -o /dev/null --compressed "$HOST$CSS" \
    | grep -iE '^(cf-cache-status|cache-control|age):' | sed 's/^/   /'
  printf "   TTFB del CSS: %s ms  (HIT desde Argentina debería dar <60)\n" "$(ttfb "$HOST$CSS")"
fi
echo
echo "-- primer request tras inactividad (spin-up del plan free) --"
echo "   corré esto después de ~20 min sin tráfico y comparalo con el piso de arriba:"
echo "   curl -s -o /dev/null -w '%{time_starttransfer}s\\n' $HOST/privacidad/"
