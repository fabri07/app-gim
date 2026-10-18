# Tienda: cobro del add-on (Hito 3) + Mercado Pago del gimnasio (Hito 2)

Un solo plan en dos partes, en un worktree `tienda-mp`: **Parte A** (cobrarle la tienda al
gimnasio dentro de la suscripción que ya paga por Mercado Pago) y **Parte B** (cada gimnasio
conecta SU cuenta de Mercado Pago con un botón para cobrar las ventas de la tienda). Se
implementa A y después B; se mergea sólo con OK del usuario (juntas o A primero, como prefiera).

# Parte A — Hito 3: cobrarle el add-on al gimnasio (USD 20/mes)

## Contexto

La tienda (Hito 1, galería, portal) está en producción, pero hoy el superadmin la prende con
«Habilitar tienda» y **no se cobra nada**: el precio sale sólo del tramo de alumnos
(`plataforma/precios.py`). La spec (`docs/superpowers/specs/2026-10-17-tienda-design.md:38,177,234`)
fija el Hito 3: **USD 20 por mes sumados a la suscripción; el anual = 10 meses (USD 200)**, y la
portada deja de decir «todas las funciones incluidas».

**Decisiones del usuario (18-10):**
- Prenderla a un gimnasio que ya pagó (mensual o anual) → **se cobra desde el próximo pago**; lo
  pagado no se toca. Apagarla a mitad de período no devuelve nada.
- **Sin prueba gratis propia** (el primer mes gratis del gimnasio nuevo ya cubre todo).
- Portada: **una línea debajo de los tramos** («+ Tienda online (opcional): USD 20/mes …») y
  «todas las funciones **de gestión** incluidas».

Regla para todo el plan: **se cobra la tienda si `gimnasio.tienda_habilitada`** (no
`puede_usar_tienda`, que es verdadera en la demo) y sólo donde ya se cobra
(`facturacion_aplica` excluye demo, exentos y retirados, sin cambios).

Revisión adversarial contra el código: 15 hallazgos (2 bloqueantes: el banner «Continuar al pago»
de Mi suscripción reusaba el link viejo, y el campo nuevo del pago manual rompía los POST de hoy),
todos incorporados. Segunda pasada sobre el plan corregido: **nada bloqueante**; sus 3 medios y 6
menores, incorporados (abajo, «Casos aceptados»).

## Casos aceptados (se documentan en `ISSUES.md` y `ARQUITECTURA.md` «Cobro online»)

- **Un período atrasado pagado después de prender la tienda la incluye**: el monto sale del
  toggle del día en que se paga, no del período («desde el próximo pago» = el próximo cobro, sea
  del período que sea).
- **Un link viejo sin tienda (CANCELADO/VENCIDO) que igual se paga se acredita con su monto**
  (`monto_tienda_usd = 0`): se cobra lo que se mostró. Test que lo fija.
- El aviso por mail de vencimiento sale una vez por período: si la tienda cambia después, el precio
  del mail queda viejo (Mi suscripción siempre muestra el actual).
- Después de un «Monto a revisar», el pago manual precarga la tienda del toggle de hoy; el
  superadmin la corrige si hace falta.

## Qué NO se puede romper

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| `plataforma/precios.py` (puro, `SimpleTestCase`) | `precio_usd`, `precio_usd_anual`, `precio_usd_anual_sin_descuento` ganan `tienda=False` por keyword; sin el argumento devuelven lo mismo que hoy | `plataforma/tests_precios.py` sin tocar (10/15/20, ×10, ×12) |
| Portada (`tenants/views.py::_contexto_precios`) | sigue llamando sin `tienda`: los tramos quedan idénticos | `tenants/tests.py:495-556` |
| Monitor, `kpis`, `para_cobrar`, avisos | `FilaMonitor.precio_usd` pasa a ser el **total** y gana `tienda_usd: int = 0` al final; sin tienda, idénticos | `tests_facturacion.py:288-289,330,340`, `plataforma/tests.py:241` + test nuevo «sin tienda = igual que hoy» |
| Cobro online (`suscripcion/services.py::crear_intento`) | monto con `tienda=gimnasio.tienda_habilitada`; el link vivo se reusa sólo si además coincide la tienda | `suscripcion/tests_services.py:129,244,249,268` |
| Acreditación | `_acreditar_aprobado` copia también `monto_tienda_usd` del intento (nunca recalcula) | tests de `acreditar` |
| `PagoPlataforma` / `IntentoDeCobro` | campo nuevo `monto_tienda_usd` Decimal default 0 (filas viejas = 0, que es la verdad); no son `TenantOwnedModel` → no van a demo/`_ensuciar`/exportación | `makemigrations --check` |
| Pago manual (`PagoPlataformaForm`) | campo nuevo opcional (vacío = 0) con su línea de error (vía `partials/campo_form.html`), validado ≥ 0 y ≤ `monto_usd` | `tests_facturacion.py` sin tocar (`_datos` no manda el campo) + tests nuevos |
| Banner «Continuar al pago» (`suscripcion/views.py::_intento_abierto_de`) | filtra también por la tienda esperada | tests actuales del banner + test nuevo de toggle con link vivo |
| Toggle (`TiendaToggleView`) | sigue con `update()` (no toca `modificado`); sólo cambia el mensaje | `plataforma/tests_bloqueo.py:740-768` |
| Demo | `tienda_habilitada` sigue en False y `facturacion_aplica` la excluye; chip «Adicional» se queda | `test_chip_adicional_solo_en_la_demo` (sólo su docstring cambia) |

## Diseño

**1. Precio (fuente única, `plataforma/precios.py`)**
```python
PRECIO_TIENDA_USD = 20

def precio_tienda_usd(tienda):           # 20 o 0
def precio_tienda_usd_anual(tienda):     # precio_tienda_usd(tienda) * (MESES_ANIO - DESCUENTO_ANUAL_MESES) = 200 o 0
def precio_usd(alumnos_activos, *, tienda=False):            # tramo + tienda
def precio_usd_anual(alumnos_activos, *, tienda=False):      # precio_usd(...) * 10
def precio_usd_anual_sin_descuento(alumnos_activos, *, tienda=False):  # * 12
```
`tienda` es keyword-only (nadie puede pasar `precio_usd(n, True)`). El anual congela tramo **y**
tienda por 12 meses: se documenta en el docstring del módulo («El modelo comercial»), en
`precio_usd_anual` y en `ARQUITECTURA.md` «Precios, ciclo y prueba» (~`:838-856`).
Las comparaciones Decimal (campo) vs int (`precios`) son numéricas (`Decimal('20.00') == 20`);
nunca como string o float.

**2. Panel de plataforma**
- `facturacion.fila_de_gimnasio`: `tienda = gimnasio.tienda_habilitada`;
  `precio_usd=precios.precio_usd(alumnos, tienda=tienda)`, `tienda_usd=precios.precio_tienda_usd(tienda)`.
  `kpis`, `para_cobrar` y `avisos` no cambian de código: ya suman/usan el total.
- `inicio.html`: columna «Precio (USD)» (`:141`, dentro del `{% if fila.factura %}`, así las filas «—» quedan limpias) y «Para cobrar» (`:58`) muestran el total + `<span class="texto-suave">con tienda</span>` si `fila.tienda_usd`.
  **Aceptado y documentado** en `ARQUITECTURA.md` «Panel de plataforma»: el monitor muestra el
  precio del PRÓXIMO cobro, así que un gimnasio con un anual pago al que se le prende la tienda ya
  suma +20 en `kpis` aunque no pague hasta renovar (mismo comportamiento de hoy con un cambio de
  tramo); la etiqueta «con tienda» lo explica.
- `gimnasio_detalle.html` «Precio por ciclo»: `USD 35` + «(USD {{ escalon.1 }} del tramo + USD {{ fila.tienda_usd }} de la tienda)»
  si `fila.tienda_usd` (`escalon` ya está en el contexto, `plataforma/views.py:91`); `precio_ars` ya sale del total (`views.py:90`). Historial de pagos: en la
  celda USD, «incl. tienda» si `pago.monto_tienda_usd`.
- `PagoPlataformaCreateView.get_initial`: `monto_usd = precio_usd(alumnos, tienda=...)`,
  `monto_tienda_usd = precio_tienda_usd(tienda_habilitada and facturacion_aplica)`; `PagoPlataformaForm` suma `monto_tienda_usd`
  (después de `monto_usd` en `fields` y en `pago_form.html`, help «Parte del monto que es la
  tienda; 0 si no la tiene»). El campo del modelo lleva `default=0` +
  `MinValueValidator(0)`; en el form `self.fields["monto_tienda_usd"].required = False` en
  `__init__` (no redeclarar el campo: perdería `max_digits`, label y help) y
  `clean_monto_tienda_usd` convierte vacío en `Decimal(0)` y rechaza negativos con un solo mensaje — así los POST de hoy sin el campo (`tests_facturacion.py:308-320`, `_datos`, ~11
  tests) siguen andando. `clean()`: si `monto_usd` y `monto_tienda_usd` están los dos, la tienda no
  puede superar el monto (error en el campo `monto_tienda_usd`). Aceptado: después de un «Monto a
  revisar», el precargado de la tienda sale del toggle de hoy, no del intento; el superadmin lo
  corrige (se documenta).
- `plataforma/admin.py` `list_display` suma `monto_tienda_usd`.
- `TiendaToggleView`: mensaje según `gimnasio.facturacion_aplica` (se lee del objeto ya cargado):
  con cobro, «Tienda habilitada para X. Se le suma USD {PRECIO_TIENDA_USD} por mes desde su próximo pago.» (el número sale de `precios`, nunca literal) /
  «Tienda deshabilitada para X. Deja de cobrarse desde su próximo pago.»; sin cobro (demo,
  exento, retirado), «Tienda habilitada/deshabilitada para X (no se le cobra).».

**3. Cobro online (`suscripcion/`)**
- Helper nuevo `services.tienda_usd_para(gimnasio, plan)` → `precio_tienda_usd_anual` o
  `precio_tienda_usd` de `gimnasio.tienda_habilitada`: lo usan `crear_intento` y la vista (una sola
  regla).
- `crear_intento`: `tienda = gimnasio.tienda_habilitada`; `monto_tienda_usd = tienda_usd_para(...)`
  calculado **antes** de buscar el link abierto; si
  `abierto.plan != plan or abierto.monto_tienda_usd != monto_tienda_usd` → se cancela como hoy el
  cambio de plan (si no, un link generado antes de prender la tienda se reusaría 48 h con el monto
  viejo). `monto_usd = precio_usd[_anual](alumnos, tienda=tienda)`. El intento guarda
  `monto_tienda_usd`.
- `_acreditar_aprobado`: copia `monto_tienda_usd=intento.monto_tienda_usd`.
- **`views._intento_abierto_de`** (el banner «Continuar al pago» de Mi suscripción, que va directo a
  MP con el `monto_ars` viejo): además del período, sólo ofrece un link cuyo `monto_tienda_usd ==
  tienda_usd_para(gimnasio, intento.plan)`. Sin esto, prender o apagar la tienda con un link vivo
  cobra USD 20 (o 200 en el anual) de menos o de más. `plataforma` no importa `suscripcion`, así
  que el toggle no puede cancelar los links: el filtro vive acá (mismo criterio que el docstring
  actual) y `crear_intento` cancela el viejo cuando se genera el nuevo.
- Mails al superadmin `cobro_acreditado.txt:6` y `cobro_con_problema.txt:8`: «(incluye tienda USD
  X)» si `intento.monto_tienda_usd`. Título de la preferencia de MP: « + tienda» si la incluye.
  `suscripcion/admin.py` suma el campo.
- `_contexto_suscripcion` (Mi suscripción): planes con `tienda=gimnasio.tienda_habilitada`
  (incluido el tachado del anual) + `tienda_usd`/`tienda_anual_usd` al contexto; en
  `mi_suscripcion.html`, debajo del tramo: «Incluye la tienda: USD 20 por mes (USD 200 en el
  anual).» sólo si la tiene (números del contexto, no literales). La frase de `:111` («El precio
  mensual se ajusta solo si cambiás de tramo; el anual queda fijo los 12 meses») pasa a «…si
  cambiás de tramo o se activa o desactiva la tienda; el anual queda fijo los 12 meses, con la
  tienda incluida si la tenías». En la demo (`es_demo`), una línea: «La tienda es un adicional de
  USD 20 por mes.»
- Aviso por mail: `avisos.avisar` pasa `tienda_usd` **como default del lambda** (`tu=fila.tienda_usd`,
  igual que `usd=fila.precio_usd` en `avisos.py:91-92`; si no, todos los mails llevan el valor de la
  última fila);
  `emails.enviar_aviso_cobro(..., tienda_usd=0)`; `aviso_cobro.txt`: «Precio del plan mensual:
  USD 35 (incluye la tienda, USD 20) …» sólo con tienda; sin tienda, el texto de hoy.

**4. Portada (`templates/tenants/portada.html`)**
- `:316` «con <strong>todas las funciones de gestión incluidas</strong> en cualquier tramo»;
  `:330` `<li>Todas las funciones de gestión</li>`.
- Después de `.portada__precios`: `<p class="portada__precio-adicional"><strong>+ Tienda online
  (opcional): USD {{ precios_tienda_usd }} / mes</strong> · USD {{ precios_tienda_anual_usd }} /
  año. Vendé suplementos y ropa a tus alumnos, con cobro por transferencia.</p>` (≈ pesos del
  mensual si hay cotización, como los tramos). `_contexto_precios` suma `precios_tienda_usd`,
  `precios_tienda_anual_usd` (de `precio_tienda_usd_anual`), `precios_tienda_ars`. «con cobro por
  transferencia» es verdad hoy; el Hito 2 (MP) tiene que actualizar esta frase (se anota en la spec
  y en `ROADMAP.md`). Estilo nuevo en `styles/input.css` junto a
  `.portada__precio-auto` (mismo color claro sobre el campo), `npm run build:css`.

**5. Docs** — spec (Hito 3 hecho), `ARQUITECTURA.md` «Panel de plataforma», «Cobro online de la
suscripción» y «Tienda del gimnasio» (regla `tienda_habilitada`, desde el próximo pago, anual
congela tienda), `ARQUITECTURA.md:631` y docstring del chip («todas las funciones incluidas» ya no
aplica: el chip queda porque es un adicional), `ROADMAP.md:92-97`, `CLAUDE.md` (resumen de la
tienda), entrada en `ISSUES.md` (decisión «desde el próximo pago»), memoria
`facts_modelo_comercial_plataforma.md`, docstring del módulo `precios.py`.

## Tareas de la Parte A (subagentes + review por tarea; suite verde y commit en cada una)

A1. **Precio + panel de plataforma**: `precios.py`; migración `plataforma` (`monto_tienda_usd`);
   `FilaMonitor`/`fila_de_gimnasio`; inicio, detalle e historial; pago manual (form, initial,
   template, admin); mensaje del toggle. Tests: funciones puras con y sin tienda (20/200/240,
   `precio_tienda_usd_anual` 200/0, `tienda` keyword-only);
   gimnasio sin tienda → fila, `kpis` y `initial` idénticos a hoy; con tienda → total, `tienda_usd`,
   `kpis` suma 35; demo con `tienda_habilitada=True` igual no factura; form: POST sin el campo
   guarda 0, tienda > monto y negativo rechazados con error visible; toggle no toca `modificado`
   y dice el mensaje con cobro y sin cobro (exento).
A2. **Cobro online**: migración `suscripcion` (`monto_tienda_usd`); `crear_intento`, reuso,
   acreditación, Mi suscripción, aviso por mail. Tests: mensual/anual con y sin tienda (montos y
   `monto_ars`); link vivo sin tienda + prender tienda → nuevo intento con el monto nuevo y el
   viejo CANCELADO; mismo estado → se reusa (como hoy); el pago acreditado copia
   `monto_tienda_usd`; **Mi suscripción con un link vivo sin tienda + prender la tienda → el banner
   «Continuar al pago» desaparece y los planes muestran el total nuevo** (y al revés, apagándola);
   Mi suscripción muestra la línea sólo con tienda; aviso con y sin tienda, con dos gimnasios en la
   misma corrida (uno con, otro sin) para fijar el default del lambda; mails al superadmin con
   tienda; link CANCELADO sin tienda pagado después de prenderla → pago con
   `monto_tienda_usd == 0`; frase del tramo actualizada; línea de la demo.
A3. **Portada + docs**: textos, línea de la tienda con su estilo, `build:css`, docs y memoria.
   Tests: la portada muestra «Tienda online (opcional)» con `precio_tienda_usd(True)` y
   `precio_tienda_usd_anual(True)` dentro de esa línea (el tramo de más de 300 ya muestra «USD 200 /
   año», así que el test busca el texto de la línea, no el número suelto) y ya no dice «todas las
   funciones incluidas»; los tests de tramos siguen.

QA de la Parte A en el navegador: portada (escritorio y 390 px), panel de plataforma (monitor,
ficha con y sin tienda, prender/apagar, pago manual precargado), Mi suscripción de un gimnasio con
tienda (mensual y anual).

## Verificación de la Parte A

- Suite completa con `.env` sobre el merge, `makemigrations --check` (2 migraciones nuevas, nada
  pendiente), `npm run build:css`, `git status` sin archivos nuevos sueltos.
- Post-deploy: ninguno a mano (los campos nuevos arrancan en 0; los gimnasios con la tienda
  prendida empiezan a pagarla en su próximo cobro). Avisarle al usuario qué gimnasios tienen hoy
  la tienda prendida, porque su próximo cobro sube USD 20.

# Parte B — Hito 2: Mercado Pago del gimnasio para la tienda

## Contexto

Hoy el alumno sólo puede pagar un pedido por transferencia y subir el comprobante. El usuario pidió
que **cada gimnasio conecte su propia cuenta de Mercado Pago** para cobrar las ventas (la plata va
directo al gimnasio; la plataforma nunca la toca). El diseño base está en la spec
(`docs/superpowers/specs/2026-10-17-tienda-design.md:56-69,82-97,149-167,217-222`).

**Decisiones del usuario (18-10):** botón **«Conectar Mercado Pago»** (OAuth: el dueño autoriza en
Mercado Pago y vuelve, sin copiar nada); **se construye ya, apagado** hasta que el usuario haga la
prueba con cuentas de prueba de Mercado Pago (guiada paso a paso); recién ahí se cargan las
variables en Render y se prende.

**Interruptor:** `TIENDA_MP_ENABLED = _bandera_todo_o_nada([MERCADOPAGO_CLIENT_ID,
MERCADOPAGO_CLIENT_SECRET, MERCADOPAGO_OAUTH_REDIRECT_URI, MP_TOKEN_ENCRYPTION_KEY], …) and not
TESTING` (`config/settings.py:286`, molde `COBRO_ONLINE_ENABLED` `:466-473`). Sin las cuatro, nada
de la Parte B se ve: la tienda sigue sólo con transferencia, exactamente como hoy.

Revisión adversarial de la Parte B contra el código: 1 bloqueante (los campos nuevos de la
configuración se veían con la bandera apagada y rompían `test_guardar`), 13 medios y 17 menores,
todos incorporados abajo. Sin conflictos con la Parte A (A se mergea primero). Segunda pasada:
1 bloqueante (devolver el pago duplicado dejaba el pedido bueno en «Devuelto»), 6 medios y 10
menores, incorporados. Tercera pasada (máquina de estados de acreditar, combinación por
combinación): **nada bloqueante**; 8 medios y 10 menores, incorporados (§5 «Reglas finas»).

**Cambios deliberados respecto de la spec:** un segundo pago aprobado sobre un pedido ya pagado
NO lo pasa a `REVISAR` (perdería el estado y el stock): queda como está, el pago va a
`pagos_adicionales` y aparece en la pestaña «Revisar MP». El tópico `mp-connect`
(desautorización) queda fuera: un 401 al usar el token cumple la misma función (pasa a
RECONECTAR). El refresco corre en el workflow de cada 6 h (sólo toca cuentas con menos de 30 días).

## Qué NO se puede romper (Parte B)

| Código existente | Cómo se toca | Qué lo protege |
|---|---|---|
| Tienda con transferencia (checkout, comprobante, rechazo, vencer, arrepentimiento, configuración) | con la bandera apagada (o en la demo) el form de configuración **no tiene** los campos nuevos (se sacan de `self.fields` en `__init__`), el carrito no muestra elección y `crear_pedido` arma el mismo pedido de hoy | toda la suite de `tienda/` sin tocar (incluido `ConfiguracionTests.test_guardar`, `tests_vistas_staff.py:700-707`) + test «bandera apagada: el HTML no tiene `acepta_`» |
| `suscripcion/mercadopago.py` (cobro de la suscripción, vivo) | parámetros nuevos opcionales (`token=None`, `sandbox=None`, `statement_descriptor="TUGIMAPP"`); sin ellos, idéntico | `suscripcion/tests_mercadopago.py` + test de frames extendido |
| Webhook de la suscripción | si un pago de un vendedor le llega (app con webhook en el panel), `consultar_pago` con el token de la plataforma da 401/403 → **200** con `logger.error` (hoy 500 y MP reintenta para siempre); 404 → 200 como hoy | test nuevo; los de 404/500 siguen |
| `calendario/fields.py` (`EncryptedTextField`, tokens de Google) | kwarg `clave_setting="GOOGLE_TOKEN_ENCRYPTION_KEY"` con `deconstruct()` que lo omite si es el default | `makemigrations --check` sin cambios en `calendario`; tests de calendario |
| Sentry | `include_local_variables=False`, con los kwargs en una constante `SENTRY_OPCIONES` (el `init` no corre en la suite) | test sobre la constante |
| Lazy path de vencer | sigue sin red; un pedido MP vence igual que uno de transferencia | test con un pedido MP |
| Panel del staff (`tenants/views.py:41-48`, `home.html:77-79`) y `contadores_panel` | clave agregada nueva `a_revisar_mp` y filtro/push `revisar_mp` (`a_revisar`/`revisar` ya son EN_REVISION), `vivos` suma REVISAR; sigue UNA query agregada | tests de queries del panel |
| Los TRES lugares | `CuentaMercadoPago` → `vaciar_gimnasio`, `_ensuciar` (con tokens `""`: el campo no cifra un vacío, así la suite no necesita la clave), `EXCLUIDOS` | `test_ensuciar_cubre_todos_los_modelos_tenant_owned`, cobertura de la exportación |
| Exportación | columnas nuevas de `ConfiguracionTienda` y `Pedido` (`payment_id`, `estado_mp`; `medio` ya se exporta; **nunca** `respuesta_mp`) | `tests_exportacion.py` |
| Admin | `CuentaMercadoPago` con `exclude` de los tokens (el admin los mostraría descifrados); en `PedidoAdmin`, los campos MP de sólo lectura | test del admin |
| Analítica y «Vendido este mes» | un pago MP aprobado escribe `pagado_en` | test de `resumen_del_mes` con un pedido MP |
| Workflows | **ninguno nuevo**: pasos en `reconciliar-cobros.yml` **antes** de «Avisar a Healthchecks» (`:79-87`), `if: ${{ !cancelled() }}`, con `env` completo (molde `enviar-recordatorios.yml:77-83`: `DATABASE_URL`, `DJANGO_DEBUG`, las 4 variables nuevas, `VAPID_*` para los avisos); con la bandera apagada salen 0 **salvo que exista alguna cuenta CONECTADA** (→ 1: secrets olvidados, los tokens vencerían sin que nadie lo note) | tests de los comandos; `docs/runbook-respaldos.md` (lista de secrets) en el mismo commit |
| `render.yaml`, `.env.example` | las 4 variables nuevas `sync: false` | `BlueprintDeclaraLoQueSettingsLeeTests` |

## Diseño (Parte B)

**1. Cliente de MP (`suscripcion/mercadopago.py`)**
- `class Secreto` (`.valor` + `__repr__` = «Secreto(***)»): envuelve el token y el cuerpo con el
  `client_secret`; `_headers` y `_pedir` los abren inline, así nunca quedan en claro en
  `repr(frame.f_locals)` (hoy `_pedir` guarda el cuerpo en `cuerpo`/`datos`, `:84,96`).
- `_headers(clave_idempotencia, token=None, *, sin_autorizacion=False)` (el canje OAuth no lleva
  Bearer: con `token=None` mandaría el de la plataforma), `_pedir(..., token=None,
  sin_autorizacion=False)`, `es_de_prueba(token=None)`, `crear_preferencia(..., token=None,
  sandbox=None, statement_descriptor="TUGIMAPP")` (`sandbox=None` = criterio de hoy por prefijo;
  la tienda pasa `cuenta.es_de_prueba`; `statement_descriptor=None` no se manda),
  `consultar_pago(id, token=None)`, `buscar_pagos(ref, token=None)`.
- `tienda/mercadopago_oauth.py`: `url_de_autorizacion(state, code_challenge)`
  (`https://auth.mercadopago.com.ar/authorization`, `client_id`, `response_type=code`,
  `platform_id=mp`, `redirect_uri`, PKCE S256), `canjear_codigo(code, code_verifier)` y
  `refrescar(Secreto(refresh_token))` → `POST /oauth/token` con el cuerpo en `Secreto`. El callback
  rechaza la respuesta sin `refresh_token` (molde `calendario/views.py:98-106`; depende de
  `offline_access` en la app, punto de la prueba).
- Token que no se descifra (`from_db_value` → `None` con la clave rotada) → la cuenta pasa a
  RECONECTAR sin llamar a MP. **Sin la clave en el entorno** el campo con `clave_setting` no default
  devuelve `None` en vez de `ImproperlyConfigured` (si no, sacar las 4 variables con cuentas
  conectadas da 500 en el admin y al borrar un gimnasio); con la bandera apagada nada de la tienda
  carga instancias de la cuenta. `Secreto` en `_pedir`: `cuerpo` acepta dict (los tests de hoy,
  `tests_mercadopago.py:154-158,183-197`) o `Secreto`, y desaparece el local `datos` (`:96`).

**2. Modelos (`tienda/models.py`)**
- `CuentaMercadoPago(TenantOwnedModel)`: `UniqueConstraint(gimnasio)`, `access_token` y
  `refresh_token` `EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY", blank=True,
  default="")`, `mp_user_id`, `token_expira`, `uuid_webhook` (UUID único, default `uuid4`),
  `es_de_prueba` (de `live_mode`), `estado` (CONECTADA / RECONECTAR / DESCONECTADA),
  `conectada_por` (FK User, SET_NULL), `conectada_en`, `desconectada_en`.
- `ConfiguracionTienda`: `acepta_transferencia` (True), `acepta_mercadopago` (False),
  `minutos_reserva_mp` (30, 20–120). Form: los tres campos sólo con bandera y no demo; `clean()`
  valida **sólo si los campos están en `self.fields`**: al menos un medio, y MP sólo con la cuenta
  CONECTADA **cuando se lo prende** (si ya estaba prendido y la cuenta pasó a RECONECTAR, guardar
  otro campo no se bloquea). Cada campo con su línea de error. Los campos nuevos de texto y
  booleanos llevan `db_default` además de `default`, también `minutos_reserva_mp` (`db_default=30`)
  (el `migrate` corre en el `buildCommand` mientras la versión vieja sigue atendiendo: sus INSERT no
  conocen las columnas nuevas).
- `Pedido`: `Medio.MERCADOPAGO`; `Estado.REVISAR` («Pagado, a revisar», entra en `max_length`),
  con entrada en `_BADGE_POR_ESTADO`; campos `referencia_externa` (UUID **`null=True, unique=True, default=uuid4`**:
  Postgres admite varios NULL, así un INSERT del código viejo durante el deploy no rompe; un
  RunPython con `reverse_code=noop` llena los existentes; el código exige que no sea nulo al crear
  un link), `preference_id` e
  `init_point` (`TextField` blank), `payment_id` (blank, `UniqueConstraint` parcial
  `~Q(payment_id="")`), `estado_mp`, `respuesta_mp` (JSON, `null=True`, sólo `resumen_de_pago`), `revision_mp_pendiente` (bool,
  `db_default=False`). `REVISAR` NO retiene stock.
- Constante `MARGEN_PREFERENCIA = timedelta(minutes=10)`: `vence_preferencia = reservado_hasta −
  MARGEN_PREFERENCIA` (property), la usan la preferencia, `cancelar_por_alumno` y `mi_pedido`.

**3. Conectar / desconectar / refrescar (staff, `tienda/views_mercadopago.py`)**
- Tarjeta «Mercado Pago» en `configuracion_form.html`, sólo con la bandera: sin cuenta →
  «Conectar Mercado Pago» (`<a … hx-boost="false">`); conectada → «Conectada» (+ «cuenta de
  prueba») con quién y cuándo la conectó + «Desconectar» (POST); RECONECTAR → aviso + botón. Demo:
  «En la cuenta de demostración no se conecta Mercado Pago», sin botón.
- `ConectarView` (GET; staff, `puede_usar_tienda`, no demo, bandera) → `code_verifier`, `state =
  signing.dumps({gimnasio, usuario, nonce})`, nonce+verifier+ts en la sesión → redirect.
  `CallbackView`: `?error` → mensaje; `state` firmado + mismo usuario y gimnasio + nonce + ≤ 10 min
  (molde `calendario/views.py::_state_valido`) → canje → si ya había una cuenta con **otro**
  `mp_user_id` y hay pedidos MP vivos o de los últimos 7 días → se rechaza («terminá o cancelá esos
  pedidos antes de cambiar de cuenta»); si no, `update_or_create` (CONECTADA,
  `token_expira = ahora + expires_in`) → mensaje y **push a todo el staff** («X conectó la cuenta de
  Mercado Pago de la tienda»: cualquier staff puede conectar, igual que hoy cualquiera edita el
  alias; así nadie desvía ventas en silencio). Errores: log sanitizado (molde `_sanitizar_error`),
  mensaje, vuelta a la configuración con 200.
- `DesconectarView` (POST): rechazado con pedidos MP en `PENDIENTE_PAGO`; si no → DESCONECTADA,
  `acepta_mercadopago=False`, `acepta_transferencia=True` (nunca queda sin medio), `desconectada_en`. **Los tokens se conservan 7 días** (pagos tardíos de
  pedidos vencidos siguen verificables con `reconciliar_pedidos`); `refrescar_cuentas_mp` los borra
  pasado ese plazo.
- `refrescar_cuentas_mp` (comando): CONECTADAS con `token_expira < ahora + 30 días`, por cuenta
  `select_for_update` + relectura tras el lock (refresh token de un solo uso) → tokens nuevos; 400/401
  → RECONECTAR + push al staff. El borrado de tokens de las desconectadas hace más de 7 días corre
  **antes** del chequeo de la bandera (no necesita la clave). Un token ilegible nunca se guarda con
  `save()` completo: `update()`/`save(update_fields=…)` sin las columnas de tokens, y el admin con
  `save_model` que no las toca. Un 401 al usar el token pasa a RECONECTAR con UPDATE condicional
  (`filter(pk, estado=CONECTADA, token_expira=<el leído>)`) para no pisar una cuenta recién
  refrescada.

**4. Compra con Mercado Pago (alumno)**
- `medios_disponibles(gimnasio, config)`: MP si bandera + `acepta_mercadopago` + cuenta CONECTADA +
  no demo; transferencia si `acepta_transferencia` **o si MP no está disponible** (respaldo: una
  cuenta en RECONECTAR nunca deja la tienda sin poder vender; la configuración le avisa al staff
  «Mercado Pago no está disponible: tus alumnos ven sólo transferencia»).
- Carrito: con un medio, como hoy (el texto de las horas sólo si es transferencia); con dos, radios
  «Transferencia» / «Mercado Pago» (fieldset a mano con su error, molde `mi_suscripcion.html`). El
  form del checkout lleva `hx-boost="false"` (redirige a mercadopago.com.ar).
- `crear_pedido(..., medio=TRANSFERENCIA)` revalida `medio in medios_disponibles` (el POST puede
  traer cualquier cosa; `medio_no_disponible` en `MENSAJES`); MP → `reservado_hasta = ahora +
  minutos_reserva_mp`. La vista, después del `atomic`, crea la preferencia (`token=Secreto(…)`,
  `sandbox=cuenta.es_de_prueba`, `titulo="Pedido #N — {gimnasio}"`, `expira=vence_preferencia`,
  `url_retorno`, `url_notificacion` con `uuid_webhook` armadas por la vista y pasadas al servicio,
  molde `crear_intento`, `statement_descriptor=None`) → guarda `preference_id`/`init_point` →
  vacía el carrito → redirect al link. Si MP falla: el pedido **se borra** (nunca tuvo link
  pagable): en un `atomic`, `filter(pk, estado=PENDIENTE_PAGO, preference_id="").update(estado=CANCELADO)`
  y **sólo si tocó 1 fila** `_devolver_stock([pk])` y después `delete()` (pedido antes que
  variantes, mismo orden de locks que `vencer_pedidos`; si el staff lo canceló en esos segundos, no
  se devuelve dos veces); el carrito queda intacto, mensaje «No pudimos conectar con Mercado
  Pago. Probá de nuevo» (+ «o elegí transferencia» sólo si se acepta); un 401 → RECONECTAR.
- Con MP: **sin push «Nuevo pedido»** al staff (llegaría por cada intento abandonado; lo reemplaza
  `pago_mp` al acreditar) y mensaje propio en vez de «Transferí y subí el comprobante».
- `mi_pedido.html`, pedido MP en `PENDIENTE_PAGO`: «Pagá $X antes de las HH:MM» (`vence_preferencia`)
  + «Pagar con Mercado Pago» (`hx-boost="false"`); sin `init_point` (el proceso murió) → sólo
  «Cancelar». Sin formulario de comprobante. `REVISAR`: «Recibimos tu pago; el gimnasio te va a
  contactar». `DEVUELTO` por reintegro de MP: «Mercado Pago te devolvió el pago».
- `cancelar_por_alumno` rechaza un pedido MP con la preferencia vigente (`pago_en_curso`, sólo si
  `preference_id != ""` y `ahora < vence_preferencia`; sin link puede cancelar ya); pasada
  `vence_preferencia`, puede cancelar. `tope_pedidos` deja de decir «cancelalos» sin matices
  («esperá a que se confirmen o venzan»).
- Staff, detalle de un pedido MP en `PENDIENTE_PAGO`: **sin «Marcar pagado» ni «Cancelar» mientras
  la preferencia está vigente** (si lo marca después y llega el pago, se adjunta sin alarma; un pago
  tardío sobre un pedido que el staff canceló va a `REVISAR`, no lo revive: ver §5).
- Checkouts MP abandonados: vencen sin push «Tu pedido venció» si no tienen `payment_id` (el alumno
  simplemente no pagó); el tope `max_pedidos_activos` los cuenta sólo mientras la preferencia está
  vigente (documentado).
- Retorno `tienda/mercadopago/retorno/<uuid:referencia>/` (alumno dueño, `never_cache`, mismo
  acceso que `mi_pedido`): `payment_id`/`collection_id` (MP manda `"null"`) → `acreditar_pedido`
  (error → log y «Estamos confirmando tu pago») → redirect a `mi_pedido`. Nunca confía en el
  `status` de la URL. En la app instalada (iOS) la vuelta puede caer en el login: acredita igual el
  webhook (documentado).

**5. Acreditar (`tienda/pagos_mp.py`, molde `suscripcion/services.py::acreditar`)**
- `acreditar_pedido(cuenta, payment_id)`: `pago = resumen_de_pago(consultar_pago(id,
  token=Secreto(cuenta.access_token)))` en una línea, fuera de la transacción; pedido por
  `external_reference` **del mismo gimnasio** y `medio=MERCADOPAGO`; si no → `None`. Todas las
  transiciones con `_transicionar` (UPDATE condicional, regla #2 de `tienda/services.py:7-9`;
  `select_for_update` no hace nada en SQLite); idempotente por `(payment_id, estado_mp)`.
  **Nunca `pedido.save()`**: toda escritura es `filter(pk, estado__in=…, [filtro]).update(...)`, y
  las que leen-modifican-escriben `respuesta_mp`/`revision_mp_pendiente` van con
  `select_for_update()` del pedido (producción es Postgres; conserva `pagos_adicionales` al reescribir,
  molde `suscripcion/services.py:437-441`). `_transicionar` gana `filtro=Q(...)` opcional (p. ej. `payment_id=""` al adjuntar un pago a un
  pedido marcado a mano) y, si el UPDATE no toca filas (otro proceso lo movió, p. ej.
  `vencer_pedidos`), se relee y se decide **una vez más**; si tampoco, error reintentable (webhook
  500, MP reintenta). Reglas del molde
  `suscripcion/services.py:385-430`: **para el mismo pago, `estado_mp` nunca retrocede**; un pago con
  `payment_id` distinto del del pedido sólo cuenta si está aprobado (adicional); un rechazado/devuelto
  de OTRO pago nunca toca el pedido.
  - `approved` + `ARS` + monto ±0,01: `PENDIENTE_PAGO` → `PAGADO` (`pagado_en`, `payment_id`);
    `VENCIDO`/`CANCELADO` (tardío) → re-reserva todo-o-nada **en su propio savepoint** (mismo UPDATE
    sin JOIN de `_reservar_y_crear`) → `PAGADO`, o `REVISAR` si falta stock; ya `PAGADO`/`ENTREGADO`
    con `payment_id` vacío (el staff lo marcó a mano) → se adjunta el pago, sin alarma; con otro
    `payment_id` → queda como está, el pago a `respuesta_mp["pagos_adicionales"]` + aviso «cobrado
    dos veces: devolvé uno desde Mercado Pago».
  - monto o moneda distintos → `REVISAR` (si estaba `PENDIENTE_PAGO`, devuelve el stock) + aviso.
  - `rejected`/`cancelled` → guarda `payment_id`/`estado_mp` **sólo** si el pedido está en
    `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO` (un aprobado posterior lo reemplaza); con el pedido
    pagado, no toca nada.
  - `refunded`/`charged_back` **del `payment_id` del pedido** → `PAGADO` → `DEVUELTO` (devuelve
    stock), `ENTREGADO`/`REVISAR` → `DEVUELTO` (sin stock); ya `DEVUELTO` → no-op explícito. **De un
    pago adicional** (el duplicado que el staff devolvió) → sólo se marca resuelta su entrada en
    `pagos_adicionales` (el pedido bueno sigue como está).
  - Re-reserva en savepoint: **primero** `_transicionar([VENCIDO, CANCELADO] → PAGADO)` y
    **después** los UPDATE de stock por `variante_id` (orden pedido → variantes, como
    `vencer_pedidos`; dos llamadas del mismo pago no reservan dos veces); si falta stock, rollback del
    savepoint, se relee la instancia y `→ REVISAR`. Mismo orden en «Entregar igual».

  **Reglas finas** (tercera pasada):
  - Monto o moneda distintos → `REVISAR` **sólo** desde `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO`.
  - Aprobado con `payment_id` distinto (o vacío en `DEVUELTO`) sobre `PAGADO`/`ENTREGADO`/`REVISAR`/
    `DEVUELTO` → pago adicional (`pagos_adicionales` + `revision_mp_pendiente`).
  - Aprobado tardío sobre un pedido que **canceló el staff** (`motivo` cargado) → `REVISAR`, no se
    revive.
  - `revision_mp_pendiente` = «hay alguna entrada adicional sin resolver» (con dos duplicados, la
    primera devolución no vacía la pestaña).
  - Otros status (`in_process`, `pending`, `authorized`, `in_mediation`) → no cambian nada.
  - Un `refunded` de un pago sobre un pedido no pagado también guarda `payment_id`/`estado_mp` (frena
    que una lectura vieja de «approved» lo acredite después, molde `:449-454`).
  - Toda transición a `DEVUELTO` (acreditar y «Ya lo devolví») pone `cancelacion_resuelta_en` si había
    un arrepentimiento pedido, como `cancelar_por_staff` (`services.py:458-460`).
  - Permisivo: no se niega por `publicada`, `tienda_habilitada` ni cuenta congelada.
  - Avisos por `on_commit`. Staff (`_AVISOS_STAFF`, siempre `rol=STAFF`): `pago_mp`, `revisar_mp`,
    `pago_duplicado`, `devuelto_mp`. Alumno (`_AVISOS_ALUMNO`): `confirmado` (reuso), `revisar_mp`,
    `devuelto_mp`.
- Webhook `tienda/v1/webhook/mercadopago/<uuid:uuid_webhook>/` (`csrf_exempt`, POST, nunca lee
  `request.user`): bandera apagada → 503; cuenta inexistente → 200 «ignorado»; DESCONECTADA con
  tokens (≤ 7 días) → procesa igual; cuenta sin tokens (RECONECTAR por token ilegible, o
  desconectada hace más de 7 días) → 200 «ignorado» sin llamar a MP; tipo ≠ `payment` o sin `data_id` → 200; `acreditar_pedido`:
  404 y 403 → 200, 401 → RECONECTAR condicional + 200 (anotado en la prueba: confirmar que un
  pago de OTRA cuenta da 404/403 y no 401; si diera 401, después de un cambio de cuenta los webhooks
  viejos desconectarían la nueva), otro error → 500. **La autenticidad sale de la
  API** (pago leído con el token de ESE gimnasio + referencia de un pedido suyo + monto + ARS); la
  firma de un `notification_url` de vendedor OAuth es pregunta de la prueba: si llega firmada, se
  agrega obligatoria en un cambio aparte (nunca «si viene»).
- `reconciliar_pedidos` (comando, paso en `reconciliar-cobros.yml`): pedidos MP de los últimos 7 días
  en `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO` con `~Q(preference_id="")` **filtrados por estado, no por `payment_id`** (la lección
  de `suscripcion/services.py:620-628`: un rechazado deja su id y el reintento aprobado se perdería),
  más nuevos primero, `limite=50` (cada llamada espera hasta 5 s; el job tiene 15 min) →
  `buscar_pagos(ref, token=…)` → `acreditar_pedido`; un error se loguea y sigue.
- Staff: pestaña «Revisar MP» (`REVISAR` o `revision_mp_pendiente=True`, un booleano del pedido que
  se prende al registrar un pago adicional y se apaga cuando llega su devolución o con la acción
  «Ya devolví el duplicado»; un booleano y no `has_key` sobre el JSON, así la pestaña se vacía) en
  `FILTROS_PEDIDOS` y `pedido_list.html:16-19`, contador `revisar_mp` en `contadores_panel`,
  `_contadores_tienda` y `home.html`. En el detalle de `REVISAR`: «Entregar igual» (pide el stock con
  el UPDATE condicional y pone `pagado_en` si falta, así cuenta en «Vendido este mes»; sin stock →
  error) y «Ya lo devolví» (→ `DEVUELTO`). El «pedidos sin cerrar» del portal del alumno
  (`tenants/views.py:188-199`) también suma `REVISAR`. Cancelar un pedido MP
  pagado o aceptar su arrepentimiento (`resolver_cancelacion`) muestra «Devolvé la plata desde tu
  cuenta de Mercado Pago»: la devolución automática queda fuera de esta versión.

**6. Docs y la prueba guiada**
- `docs/mercadopago-tienda.md`: los clicks de la prueba (usuarios de prueba vendedor y comprador;
  en la app de MP: OAuth, PKCE, `offline_access`, redirect URI HTTPS; generar la clave Fernet;
  cargar las 4 variables en Render y los secrets en GitHub; conectar al vendedor de prueba; comprar
  con el comprador; qué mirar) y la checklist de lo no confirmado: prefijo y `live_mode` del token
  de prueba, `init_point` vs `sandbox_init_point`, si llega `refresh_token`, si refrescar invalida el
  token viejo, si la `notification_url` llega firmada, si esos pagos también llegan al webhook de
  `suscripcion`, `test_token` en el canje.
- `ARQUITECTURA.md` «Tienda del gimnasio» (MP: casos aceptados, devolución manual, vuelta en iOS),
  `CLAUDE.md` (principio #3: el MP de la tienda existe, apagado hasta la prueba), política de
  privacidad (párrafo de MP), spec (Hito 2 hecho + los cambios deliberados de arriba), `ISSUES.md`,
  `.env.example`, `docs/runbook-respaldos.md`.
- Portada: `_contexto_precios` (`tenants/views.py:461`) suma `precios_tienda_mp =
  settings.TIENDA_MP_ENABLED` y la línea de la Parte A dice «con cobro por transferencia{% if
  precios_tienda_mp %} o Mercado Pago{% endif %}»: al prender cambia sola.
- Casos aceptados (a `ARQUITECTURA.md`): devolución manual desde MP; pasados los 7 días de una
  desconexión o un cambio de cuenta, los reintegros y contracargos de pedidos de la cuenta vieja ya
  no se reflejan solos (el staff usa «Cancelar»).

## Tareas de la Parte B

B1. **Infra**: bandera + settings + `render.yaml` + `.env.example`; `EncryptedTextField(clave_setting=)`;
   `Secreto`, modo sin autorización y parámetros opcionales del cliente; `SENTRY_OPCIONES`; webhook de
   `suscripcion` 401/403 → 200. Tests: cliente con y sin token (headers, `sandbox`, sin Bearer en el
   canje), frames sin el token ni el `client_secret`, blueprint, campo con otra clave, webhook.
B2. **Modelos y datos**: `CuentaMercadoPago`, campos de config y pedido, la migración (con el llenado de `referencia_externa`), tres
   lugares, exportación, badge de `REVISAR`, admin, `MARGEN_PREFERENCIA`. Tests: aislamiento,
   migración con pedidos existentes, constraint parcial, export sin tokens ni `respuesta_mp`, admin
   sin tokens.
B3. **Conectar/desconectar/refrescar** + tarjeta + form. Tests: bandera apagada (form y HTML como
   hoy), demo, `state` vencido/ajeno, canje feliz/sin `refresh_token`/con error, cambio de cuenta con
   pedidos, push al staff, desconectar con y sin pedidos vivos, refresco con relectura, RECONECTAR
   condicional, token que no se descifra, borrado de tokens a los 7 días, `hx-boost="false"`.
B4. **Compra**: `medios_disponibles` (con respaldo), carrito, `crear_pedido(medio)` con revalidación,
   preferencia, falla de MP (pedido borrado, stock devuelto, carrito intacto), sin push «nuevo»,
   `mi_pedido` (todos los estados MP), cancelar con preferencia vigente, «Marcar pagado» oculto,
   retorno. Tests: con un medio el carrito es el de hoy; un pedido MP vence perezoso sin red.
B5. **Acreditar + webhook + reconciliar + staff**: todos los casos de §5 (incluidos: devolución
   del duplicado → el pedido bueno sigue PAGADO y la pestaña se vacía; rechazado de otro pago sobre
   un pedido pagado → no toca nada; UPDATE que no toca filas → relee y decide), webhook (503/200/500/401,
   desconectada ≤ 7 días), `reconciliar_pedidos` (rechazado y después aprobado), pasos del workflow
   (salida 1 con cuentas conectadas y bandera apagada), pestaña/contadores/panel `revisar_mp`,
   acciones de `REVISAR`, avisos, «Vendido este mes» con un pedido MP.
B6. **Docs + guía de la prueba + privacidad + portada condicional**.

Después de las dos partes: review final del branch (modelo más capaz) y **QA en navegador** con la
bandera prendida en local y las llamadas a MP simuladas (el OAuth real y un pago real se prueban
en la prueba guiada del usuario): configuración con/sin cuenta, carrito con dos medios, mi pedido
MP, staff «Revisar MP», panel; y con la bandera apagada, la tienda idéntica a hoy. Sin merge ni
push sin OK.

## Verificación de la Parte B

- Suite completa con `.env` sobre el merge, `makemigrations --check`, `git status` limpio.
- En producción queda **apagada** (sin las 4 variables): la configuración de la tienda no muestra
  la tarjeta de MP y los pasos nuevos del workflow imprimen «apagado» y salen 0.
- Para prender: la prueba guiada de `docs/mercadopago-tienda.md`, las variables en Render y los
  secrets en GitHub, y una compra real chica que pase a «Pagado».
