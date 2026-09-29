# Integración: lectores de la puerta (control de ingreso)

Para quien instala un lector de QR o facial, o escribe el programa que lo conecta
con la app (el **puente**). Describe el contrato HTTP tal como está en el código
(`ingreso/views.py`, `ingreso/services.py`, `ingreso/auth.py`,
`ingreso/limites.py`, `ingreso/decision.py`). Si este documento y el código dicen
cosas distintas, **manda el código**: avisá para corregir el documento.
`ingreso.tests.DocumentoDeIntegracionTests` compara las rutas, los campos de los
JSON de ejemplo y la tabla de números de acá abajo contra el código, así que un
cambio en el contrato que no pase por acá rompe la suite.

El diseño y el porqué de cada decisión están en
`docs/superpowers/specs/2026-09-27-control-de-ingreso-design.md`. Esto es cómo
se usa.

Hay un puente de ejemplo que implementa todo lo que sigue, solo con biblioteca
estándar de Python: `scripts/puente_ingreso_ejemplo.py`. Y un script de `curl`
para ejercitar la puerta a mano contra un servidor local:
`scripts/probar_ingreso_local.sh` (ver el paso 11).

> **Regla que aplica a todo este documento: la clave de un lector es tan
> sensible como el llavero del gimnasio.** Con ella se baja el padrón, que tiene
> el código QR de cada alumno (una llave al portador), y se registran ingresos a
> nombre del gimnasio. No la pegues en un chat, un mail, un issue ni un commit.
> Si se te escapa, rotala (paso 10) en vez de confiar en que nadie la leyó.

---

## Números del contrato

La primera columna es la constante del código que manda; la suite verifica que
el valor de la segunda coincida.

| Constante | Valor | Qué es |
|---|---|---|
| `ingreso.services.MAX_EVENTOS_POR_LOTE` | 100 | Eventos por request. Más es un 400. |
| `ingreso.services.DIAS_DE_ANTIGUEDAD_TOLERADOS` | 7 | Días hacia atrás que puede tener el `momento` de un evento en diferido. |
| `ingreso.services.HORAS_DE_ADELANTO_TOLERADAS` | 1 | Horas hacia adelante que puede tener el `momento` de un evento en diferido. |
| `ingreso.services.SEGUNDOS_DE_DESFASE_A_ANOTAR` | 60 | En online, desfase del reloj del lector a partir del cual queda anotado en el evento. |
| `ingreso.views.LARGO_ID_EVENTO_EQUIPO` | 64 | Caracteres máximos de `id_evento_equipo`. |
| `ingreso.limites.MAX_EVENTOS_POR_DIA` | 20000 | Eventos NUEVOS por lector y por día local. Más es un 429. |
| `ingreso.limites.MAX_FALLOS_POR_MINUTO` | 30 | Requests con clave inválida por IP antes del 429. |
| `ingreso.limites.SEGUNDOS_VENTANA_FALLOS` | 60 | Ventana de ese freno, y su `Retry-After`. |

---

## 1. Quién hace qué

- **El equipo** (Hikvision, ZKTeco, Dahua, Suprema...) lee el QR o reconoce la
  cara, y abre o no el molinete.
- **El puente** es el programa que habla con el equipo y con la app. Puede ser
  el firmware del propio equipo, si sabe hablar HTTP, o un programa en la PC del
  gimnasio.
- **La app** decide quién puede entrar y guarda qué pasó en la puerta.

**La app nunca recibe ni guarda biometría.** La cara se enrola en el equipo, que
guarda la plantilla facial en su propia memoria y reporta «entró el usuario 42».
La app guarda solo ese número, el id que el alumno tiene EN el equipo (la
plantilla facial es dato sensible según la Ley 25.326). Ningún campo del
contrato lleva una foto, una plantilla ni un vector: si un equipo solo sabe
mandar eso, no sirve para esta integración.

---

## 2. Dar de alta un lector y sacar la clave

1. En la app: **Mi gimnasio → Configurar la puerta → Lectores de la puerta →
   Nuevo lector** (o desde la tarjeta «Control de ingreso» del inicio).
2. Nombre (único en el gimnasio, p. ej. «Puerta principal») y qué lee (QR,
   cara, o las dos).
3. Al guardar, la app muestra **la clave una sola vez**. Copiala directo al
   equipo o al puente. La app guarda solo una huella (`sha256`) de la clave:
   **no hay forma de volver a verla**. Si se pierde, se genera una nueva
   (paso 10).

La configuración de la puerta (bloquear por deuda, exigir turno reservado, y
los minutos de antipassback) está en la misma sección, **Configuración**.

---

## 3. Autenticación

Todos los requests llevan:

    Authorization: Bearer <clave-del-lector>

- El esquema `Bearer` **no distingue mayúsculas** (`bearer` también vale): hay
  firmwares que lo mandan en minúsculas.
- No hay sesión ni cookie, y la API no mira ninguna. Una sesión de staff logueada
  en el navegador no alcanza para usarla.
- **401** — sin clave, o con una clave que no corresponde a ningún lector. La
  respuesta no distingue «no existe» de «está mal» (no le revela nada a quien no
  tiene la llave). Qué hacer: revisar la clave.
- **403** — la clave es buena pero el gimnasio **desactivó este lector**. Solo lo
  ve quien tiene una clave válida. Qué hacer: pedirle al gimnasio que lo active.

```json
{"error": "token invalido"}
```

```json
{"error": "lector desactivado"}
```

Todo request autenticado cuenta como latido: la app muestra en la lista de
lectores cuándo fue el último contacto. No hay endpoint de heartbeat aparte.

**La puerta no depende de que el gimnasio le pague la app a la plataforma.** Con
la cuenta del gimnasio suspendida, el staff ve un cartel en el panel, pero la
API responde igual: dejar gente afuera de un edificio no es una forma de cobrar.

---

## 4. `POST /ingreso/v1/eventos/`

Lo que el equipo manda en cada lectura (online) y lo que sube después de haber
estado sin internet (en diferido).

### 4.1. El request

```json
{"eventos": [{"tipo": "qr", "valor": "kP3n...", "sentido": "entrada", "id_evento_equipo": "8821"}]}
```

| Campo | Obligatorio | Qué es |
|---|---|---|
| `tipo` | sí | `"qr"` o `"facial"`. Cualquier otro (p. ej. `"rfid"`) hace inválido ese evento. |
| `valor` | sí | El texto leído del QR, o el id del usuario en el equipo facial. Se le sacan espacios y saltos de línea de los extremos. |
| `sentido` | no | `"entrada"` (por defecto) o `"salida"`. El antipassback compara solo eventos del mismo sentido. |
| `id_evento_equipo` | en diferido | El id del evento en el equipo: texto o número, hasta 64 caracteres. Es lo que hace idempotente el reintento (4.5). |
| `abierto_por_el_equipo` | no | `true` o `false` de JSON (un `"true"` entre comillas es inválido). **Decide el modo**: ausente, `null` o `false` es ONLINE; `true` es DIFERIDO. |
| `momento` | en diferido | Fecha **con hora** en ISO 8601, idealmente con zona (`2026-09-27T19:03:11-03:00`). Sin zona se toma la hora de Argentina. |

### 4.2. Online: el equipo espera el veredicto para abrir

`abierto_por_el_equipo` ausente o `false`. **Manda el reloj del servidor**: el
ingreso es ahora, diga lo que diga el `momento` del equipo, que es opcional. Si
lo manda y difiere en más de 60 segundos (o es ilegible), no se rechaza: queda
anotado en el evento para que el instalador vea que hay un reloj a corregir.

Un request online trae normalmente un solo evento, pero la regla es el campo,
no el largo de la lista.

**Timeout**: el veredicto online depende de que la app conteste a tiempo, y no
siempre va a poder (ver 7.2). El equipo o el puente tienen que tener un timeout
corto (un par de segundos) y, pasado ese tiempo, decidir con el padrón (paso 5).

### 4.3. Diferido: el equipo ya abrió con su padrón

`abierto_por_el_equipo: true`. El equipo decidió solo (sin internet, o porque
la app no contestó a tiempo) y ahora cuenta lo que pasó. La app registra el
evento y **qué habría decidido ella** (el reporte puede decir «entró, y tenía
una cuota impaga»), pero la puerta ya abrió.

- `momento` es **obligatorio**, con hora, y es lo que se registra.
- Tiene que caer entre **7 días atrás y 1 hora adelante**. Afuera de ese rango
  el evento sale inválido: no hay forma de distinguir un lector que estuvo una
  semana sin internet de uno con el reloj reseteado, y aceptarlo ensucia los
  reportes para siempre. **Un backlog de más de 7 días se pierde.**
- `id_evento_equipo` es **obligatorio**: sin él, reintentar la subida duplicaría
  el ingreso.
- **`abierto_por_el_equipo` solo sabe decir «abrió».** Un rechazo decidido con
  el padrón NO se sube: con `false` la app lo tomaría como una lectura online de
  ahora y podría registrar un ingreso que no pasó. El puente de ejemplo lo deja
  en su log.

```json
{"eventos": [
  {"tipo": "facial", "valor": "42", "sentido": "entrada", "momento": "2026-09-27T19:03:11-03:00", "id_evento_equipo": "8821", "abierto_por_el_equipo": true},
  {"tipo": "qr", "valor": "kP3n...", "momento": "2026-09-27T19:05:40-03:00", "id_evento_equipo": "8822", "abierto_por_el_equipo": true}
]}
```

### 4.4. La respuesta

Status 200 con un resultado por evento, **en el mismo orden del request**, así
el equipo los correlaciona por posición o por id. La app **decide** en orden
cronológico (un lote diferido puede llegar desordenado, y el antipassback no
puede depender de eso), pero responde en el orden en que llegaron.

```json
{"resultados": [
  {"id_evento_equipo": "8821", "permitido": true, "resultado": "permitido", "mensaje": "¡Hola, Juan! Pasá.", "alumno": "Juan P."},
  {"id_evento_equipo": "8822", "permitido": false, "resultado": "deuda", "mensaje": "Tenés una cuota sin pagar hace 3 días. Pasá por el mostrador.", "alumno": "Ana G."}
]}
```

- `mensaje` es para la pantalla del molinete.
- `alumno` es el nombre corto, o `null` si la credencial no se reconoció.
- **`id_evento_equipo` vuelve como texto**: si lo mandaste como número (`8821`),
  vuelve `"8821"`. Compará como string. Sin id, vuelve `null`.

Los valores de `resultado`:

| `resultado` | `permitido` | Cuándo |
|---|---|---|
| `permitido` | sí | Pasa. |
| `sin_credencial` | no | El QR o el id facial no es de ningún alumno de ESTE gimnasio (el QR de otro gimnasio también cae acá). |
| `credencial_inactiva` | no | El staff dio de baja esa credencial. |
| `alumno_inactivo` | no | El alumno está dado de baja. |
| `deuda` | no | Tiene una cuota impaga fuera de la tolerancia, y el gimnasio prendió «bloquear por deuda». |
| `sin_reserva` | no | El gimnasio exige turno reservado y no tiene uno vigente (se acepta llegar hasta 15 minutos antes). |
| `duplicado` | no | Ya entró hace menos de los minutos de antipassback que configuró el gimnasio. |
| `invalido` | no | El evento no se pudo leer. **No se registra** (ver 4.6). |

El orden en que se evalúan es el de la tabla: el que debe y además acaba de
pasar ve «cuota sin pagar», no «ya entraste».

### 4.5. Idempotencia: reusar el mismo id es obligatorio

Con `id_evento_equipo`, la app busca primero si ese lector ya mandó ese id. Si
existe, **devuelve el veredicto guardado sin volver a decidir** y no registra
nada nuevo. Dos veces el mismo id dentro del mismo lote se registra una vez.

**Al reintentar un request que no tuvo respuesta, mandá EL MISMO
`id_evento_equipo`, también en online.** Si el primer intento llegó a la app y
se perdió la respuesta:

- con el mismo id, el reintento devuelve el veredicto que ya se había tomado;
- sin id (o con uno nuevo), la app lo ve como una segunda lectura y el
  antipassback la da por `duplicado`: el alumno figura adentro pero la puerta
  no abre.

En online el id es opcional (hay equipos simples que no lo mandan), pero sin él
ese caso no tiene arreglo.

### 4.6. Errores: por evento o del request entero

**Un evento mal armado no tumba el lote.** Ocupa su posición con
`resultado: "invalido"` y un `error` legible, no se registra, y el resto se
procesa normal (status 200). El `id_evento_equipo` de un evento inválido vuelve
tal como vino (número si era número), o `null`.

```json
{"resultados": [
  {"id_evento_equipo": 8823, "permitido": false, "resultado": "invalido", "mensaje": "No pudimos leer el ingreso. Pasá por el mostrador.", "alumno": null, "error": "tipo desconocido: 'rfid' (se espera qr o facial)"}
]}
```

Reenviar un evento inválido no lo arregla: el puente lo descarta (y lo anota).

**Status 400** solo cuando el request entero no tiene la forma del contrato: no
es JSON, no es un objeto con `eventos`, `eventos` no es una lista, trae más de
100 eventos, o está anidado a una profundidad absurda. El body dice por qué.

```json
{"error": "se espera un objeto con «eventos»"}
```

---

## 5. `GET /ingreso/v1/padron/`

Quién puede entrar, para que el equipo decida **sin internet** y para que el
puente dé de alta, bloquee y borre usuarios en un equipo facial.

```json
{
  "version": "3f1c9a0d5b7e4c2a8e6f1d0b9c7a5e3f",
  "reglas_no_incluidas": ["reserva", "antipassback"],
  "mensajes": {
    "permitido": "¡Hola, {nombre}! Pasá.",
    "deuda": "Tenés una cuota sin pagar. Pasá por el mostrador.",
    "sin_credencial": "Credencial no reconocida. Pasá por el mostrador."
  },
  "alumnos": [
    {"id": 12, "nombre": "Juan P.", "habilitado": true, "motivo": null, "qr": "kP3n...", "id_facial": "42"},
    {"id": 15, "nombre": "Ana G.", "habilitado": false, "motivo": "deuda", "qr": "Zx81...", "id_facial": null}
  ]
}
```

- **Solo viajan los alumnos ACTIVOS con sus credenciales activas.** `qr` o
  `id_facial` son `null` si el alumno no tiene esa credencial.
- `habilitado` es la decisión de la puerta **menos** reserva y antipassback
  (que dependen del instante exacto y no se pueden calcular de antemano: lo
  declara `reglas_no_incluidas`).
- **`habilitado: false` es un deudor: se BLOQUEA en el equipo, no se borra.**
  Vuelve apenas paga. `motivo` es `"deuda"` (o `null` si está habilitado).
- **El que desaparece del padrón se BORRA del equipo, cara incluida.** Es un
  alumno dado de baja o con las credenciales revocadas. Si viajara, su QR y su
  id facial quedarían para siempre en la memoria de un equipo de terceros.
  Consecuencia: **si después lo reactivan, hay que volver a enrolarle la cara**
  (paso 9).
- **Los textos de la pantalla viajan una sola vez**, en `mensajes`, y salen de
  las mismas plantillas que la respuesta online. `mensajes.permitido` es una
  plantilla: el puente reemplaza `{nombre}` por el `nombre` de la fila. Ojo que
  el saludo **no queda idéntico**: online va el nombre de pila («¡Hola, Juan!
  Pasá.») y offline el nombre corto del padrón («¡Hola, Juan P.! Pasá.»). A un deudor se le
  muestra `mensajes.deuda` (el genérico, sin los días de atraso, que
  envejecerían adentro del equipo). A lo que no está en el padrón,
  `mensajes.sin_credencial`.
- El padrón lleva lo mínimo: nombre corto, si puede pasar y las llaves. Nunca
  email, teléfono, datos de salud ni montos.

### 5.1. `ETag`, `If-None-Match` y 304

La respuesta trae `ETag` igual a `version`, entre comillas. Guardalo y mandalo
en el próximo poll:

    If-None-Match: "3f1c9a0d5b7e4c2a8e6f1d0b9c7a5e3f"

Si el padrón no cambió, la respuesta es **304 sin cuerpo** y seguís con tu
copia. Se acepta también la versión debilitada por un proxy (`W/"..."`) o sin
comillas. **Ojo:** un 304 le cuesta al servidor lo mismo que un 200 (para saber
si cambió hay que armarlo); solo ahorra los bytes.

Las dos respuestas llevan `Cache-Control: no-store`: el que guarda la copia es
el equipo o el puente, nunca un intermediario.

### 5.2. Cada cuánto

**Cada 2 a 5 minutos, siempre con `If-None-Match`, y nunca más de una vez por
minuto.** El padrón es una foto del último poll: cuanto más seguido, menos
tiempo dura el desfase del paso 8. Pero cada poll ocupa el único proceso que
atiende también las lecturas online de todos los gimnasios.

El padrón **nunca** recibe 429 por el tope diario de eventos (paso 6): cortarle
el poll a un lector hace que la puerta funcione hoy y deje de funcionar mañana.

---

## 6. 429: demasiados requests

```json
{"error": "demasiados intentos con un token inválido desde esta IP: revisá el token del lector y esperá un minuto", "motivo": "intentos_fallidos"}
```

Viene con el header `Retry-After` (segundos). Dos `motivo` posibles:

- **`intentos_fallidos`** — demasiadas claves inválidas desde esta IP en un
  minuto. Un lector con una clave válida no lo recibe nunca, aunque comparta la
  IP (NAT) con otro mal configurado. Si lo ves, la clave del puente está mal.
- **`tope_diario`** — este lector ya registró 20000 eventos nuevos hoy. Solo en
  `POST /eventos/`. Se reinicia a la medianoche de Argentina (el `Retry-After`
  dice cuánto falta). Un gimnasio real no se acerca: es el freno contra un
  puente que reenvía en bucle, o contra una clave robada. Los reintentos con un
  `id_evento_equipo` ya registrado no suman. El backlog en diferido cuenta en el
  día en que se SUBE.

**Ante cualquier 429, no se reintenta en bucle**: los reintentos con una clave
mala también cuentan como fallas. Se respeta `Retry-After`.

---

## 7. Qué tiene que hacer el puente

### 7.1. El circuito

1. **Padrón**: pollearlo (5.2) y guardarlo en disco, para sobrevivir a un corte
   de luz. Con cada versión nueva, aplicar al equipo las altas, los bloqueos y
   los borrados (paso 5).
2. **Cada lectura**: generar un `id_evento_equipo` nuevo **una sola vez**, y
   mandar el evento online con un timeout corto.
   - 200 → mostrar `mensaje` y abrir si `permitido`.
   - 401 / 403 → no abrir, y avisar (la clave está mal o el lector está
     desactivado). No se decide con el padrón: sería saltearse justo esa
     decisión.
3. **Fallback obligatorio: ante un TIMEOUT, un 429 o un error del servidor
   (5xx, sin conexión), decidir con el padrón local** y, si el equipo abrió,
   guardar el evento en una cola para subirlo después **en diferido, con el
   MISMO `id_evento_equipo`** y el `momento` real de la lectura.
4. **Cola**: subirla en lotes de hasta 100, cuando haya conexión. Las respuestas
   vuelven en orden: lo registrado se saca de la cola, lo `invalido` se descarta
   y se anota. Ante 429, esperar `Retry-After`; ante un error de red, esperar un
   rato. **Nunca un bucle de reintentos.**

### 7.2. Por qué el fallback es obligatorio

La app corre con **un único proceso** que atiende a la vez el panel de todos los
gimnasios y la API de todos los lectores. Cualquier pedido largo del panel (un
PDF de tarjetas, un padrón grande, el exportador de datos) deja en cola las
lecturas online de todos los gimnasios mientras dura, y además la base se
«duerme» sin tráfico y la primera consulta después de un rato tarda más. Un
molinete que espera el veredicto online sin límite deja gente parada en la
puerta. Con el timeout corto y el padrón local, la peor consecuencia de una app
lenta es que ese ingreso se decide offline.

Y el mismo id en el reintento no es un detalle: si la app llegó a registrar el
intento online antes de que venciera el timeout, el diferido devuelve ese
veredicto en vez de un segundo ingreso.

---

## 8. Qué NO se aplica offline

Con el padrón cacheado el equipo decide solo si la credencial existe y si el
alumno está habilitado. No puede:

- **exigir turno reservado** ni aplicar el **antipassback**, que dependen del
  instante exacto de cada lectura (`reglas_no_incluidas`);
- saber nada de lo que cambió **después del último poll**. El desfase corre en
  las dos direcciones:
  - **deja entrar** a quien se atrasó con la cuota después del último poll
    (incluido el que cruza el límite de tolerancia a la medianoche, que el
    padrón ve recién en el primer poll del día) y a un **QR regenerado porque se
    filtró**, que sigue abriendo hasta que el equipo baje el padrón nuevo;
  - **rechaza** al que acaba de pagar en el mostrador, a un alumno reactivado y
    a una credencial recién emitida, hasta el próximo poll.

La pantalla de Configuración de la puerta dice lo mismo al lado de las dos
opciones.

Los eventos en diferido se registran con lo que la app **habría** decidido. La
deuda se evalúa contra el día en que se SUBE el lote, no contra el día de la
lectura: en un lote de varios días, el que pagó en el medio figura como que
entró al día.

---

## 9. Enrolar una cara

La cara se da de alta **en el equipo**, no en la app.

1. En el equipo (o en su software), dar de alta al alumno en modo enrolamiento,
   con su nombre para reconocerlo.
2. Anotar el **número de usuario** que el equipo le asignó.
3. En la app: ficha del alumno → tarjeta «Credencial de ingreso» → **Cargar
   número del lector facial**, y pegar ese número. Tiene que ser único en el
   gimnasio.

Desde ahí, cuando el equipo reconoce esa cara reporta `tipo: "facial"` con ese
número en `valor`.

- Dar de baja la credencial facial desde la ficha: la puerta la rechaza como
  `credencial_inactiva` y desaparece del padrón (el puente la borra del
  equipo). **Volver a cargar el número la reactiva.**
- **Alumno reactivado**: mientras estuvo dado de baja no viajó en el padrón y el
  puente lo borró del equipo, cara incluida. Hay que **volver a enrolar la
  cara** en el equipo (el número puede cambiar: actualizalo en la ficha).

---

## 10. Operación

### Rotar la clave de un lector

Si se perdió, si sospechás que la tiene alguien que no debería, o si cambió el
instalador: **Lectores de la puerta → Editar → Generar una clave nueva**. Pide
confirmación. **La clave vieja deja de abrir en el acto**, y el lector no
funciona hasta que cargues la nueva en el equipo o en el puente: hacelo en un
horario tranquilo y con alguien al lado del equipo. La clave nueva se muestra
una sola vez. Rotar también reinicia el tope diario del lector (por si quien
tenía la clave vieja lo agotó).

### Desactivar un lector

**Lectores de la puerta → Editar → destildar «Activo».** La API responde 403
desde el request siguiente. No se borra nunca: el historial de ingresos lo
necesita. **Un lector desactivado antes de subir su cola en diferido pierde
esa cola** (el 403 rechaza el lote entero).

### Revocar un lector con la cuenta del gimnasio suspendida

Con la cuenta suspendida, el staff del gimnasio no puede entrar al panel, así que
no puede rotar ni desactivar una clave robada. Lo hace **el dueño de la
plataforma desde `/admin/`**: «Lectores de la puerta» → el lector → destildar
«Activo» → Guardar. La API responde 403 desde el request siguiente. Detalle en `ISSUES.md`
[2026-09-28].

### QR regenerado

Desde la ficha del alumno, «Regenerar» le da un código nuevo y **el anterior deja
de abrir** online en el acto (offline, hasta que el equipo baje el padrón: paso
8). Si el alumno tenía la **tarjeta impresa, hay que reimprimirla** (ficha →
imprimir tarjeta, o Alumnos → Imprimir tarjetas de ingreso) y retirarle la
vieja; el código del portal del alumno se actualiza solo.

### Retención del historial

Los eventos crecen con el uso (del orden de 70.000 por año en un gimnasio de
300 alumnos). `manage.py purgar_ingresos --gimnasio <slug> --meses N` borra los
anteriores a N meses (primero con `--dry-run`). Es retención periódica, **nunca**
un paso previo a exportar los datos de un gimnasio que se va: eso se hace con
`manage.py exportar_gimnasio`.

---

## 11. Probar sin hardware

Contra un `runserver` local y una base de prueba:

1. Crear un lector en el panel y copiar la clave.
2. Generar el QR de un alumno activo y sacar su valor desde la Shell:
   `Credencial.objects.get(alumno__pk=<pk>, tipo="qr").valor`.
3. Correr:

```bash
PUENTE_INGRESO_TOKEN=<clave> ./scripts/probar_ingreso_local.sh <valor-del-qr> http://127.0.0.1:8000
```

Recorre: 401 sin clave y con una inventada, veredicto online (con `bearer` en
minúsculas), el reintento online con el mismo id, un lote en diferido con
eventos válidos e inválidos, el mismo lote otra vez (idempotente), un 400 por
forma, y el padrón con y sin `If-None-Match`.

El puente de ejemplo, contra el mismo servidor:

```bash
export PUENTE_INGRESO_URL=http://127.0.0.1:8000
export PUENTE_INGRESO_TOKEN=<clave>
python3 scripts/puente_ingreso_ejemplo.py padron
python3 scripts/puente_ingreso_ejemplo.py leer qr <valor-del-qr>
python3 scripts/puente_ingreso_ejemplo.py leer qr <valor-del-qr> --sin-red
python3 scripts/puente_ingreso_ejemplo.py subir
```

`PUENTE_INGRESO_URL` y `PUENTE_INGRESO_TOKEN` son variables del script, no de la
app. Preferí la variable a `--token`: un argumento queda visible para cualquiera
que liste los procesos de la PC.

---

## 12. Lo que no está verificado todavía

Hasta que haya un cliente con hardware: que un equipo real hable este contrato
(o que un puente lo traduzca sin sorpresas), que el enrolamiento facial de un
fabricante concreto sea como el del paso 9, la latencia de un molinete de verdad,
y si la primera consulta después de un rato de calma entra en su timeout. Por
eso el fallback del paso 7 no es opcional.
