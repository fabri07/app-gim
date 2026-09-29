# Plan — Cobro online de la suscripción (plataforma → gimnasio)

Spec (autoridad): `docs/superpowers/specs/2026-09-29-cobro-online-suscripcion-design.md`.
Su sección final «Decisiones de ejecución» manda sobre el resto.

Repo Django 5.2 sin DRF, templates + htmx (`hx-boost="true"` global en `<body>`).
**Un solo worker gunicorn sync.** Tests: `python manage.py test <app>`; el intérprete es
`/Users/fabriziosola/app_gim/.venv/bin/python` (el worktree no tiene `.venv` propio).
Todo en castellano rioplatense: nombres, docstrings, comentarios, mensajes. Leé
`CLAUDE.md` en las secciones que tu tarea nombra antes de escribir código.

## Global Constraints

Valen para TODAS las tareas. El revisor las usa como lente.

1. **No se toca nada de `pagos/`** (el cobro del gimnasio a sus alumnos). Ningún archivo.
2. **`Gimnasio.modificado` no se toca nunca**: `Gimnasio.version_media` deriva de él y
   versiona el logo, el fondo y el ícono PWA. Toda escritura sobre `Gimnasio` va con
   `QuerySet.update(...)` o `save(update_fields=[...])`, nunca `save()` pelado.
3. **Todo `try/except IntegrityError` va DENTRO de un `transaction.atomic()` propio**; si
   no, la transacción de afuera queda marcada para rollback y los `messages` (que viven en
   la base) revientan con `TransactionManagementError`.
4. **`periodo_hasta` es INCLUSIVO** en todos lados (igual que `PagoPlataforma` y
   `plataforma.precios.periodo_siguiente`).
5. **Fechas del dominio con `timezone.localdate()`**, nunca `timezone.now().date()`
   (UTC-3: entre las 21:00 y las 23:59 la fecha UTC ya es la de mañana).
6. **Nada sale a la red en la suite.** Todo lo que habla con Mercado Pago pasa por un único
   seam (`suscripcion.mercadopago._pedir`), que levanta bajo `settings.TESTING`; los tests
   parchean las funciones públicas de `suscripcion.mercadopago`.
7. **Efectos externos (mails) sólo por `transaction.on_commit`**, con los valores ya
   resueltos antes (nunca capturar el `request`).
8. **Ningún secreto ni respuesta completa de la API en un log, en un mensaje de error ni en
   un campo de la base.**
9. **Forms campo por campo con `templates/partials/campo_form.html`, nunca
   `{{ form.as_p }}`**: la `errorlist` de Django no tiene estilo en este proyecto.
10. **Todo form o link que termina en un redirect a `mercadopago.com.ar` lleva
    `hx-boost="false"`**: htmx no puede seguir un redirect cross-origin y el click queda
    tragado sin error.
11. **Ningún modelo nuevo es `TenantOwnedModel`**: son datos de la plataforma sobre el
    gimnasio (mismo criterio que `PagoPlataforma` y `solicitudes`). Quedan fuera de
    `tenants/demo.py::vaciar_gimnasio`, del fixture `_ensuciar` de `tenants/tests.py` y de
    `HOJAS`/`EXCLUIDOS` de `tenants/exportacion.py`.
12. **Ningún modelo nuevo tiene FK a `User`**: `vaciar_gimnasio` borra los `User` de la
    cuenta demo cada 6 h (con PROTECT se cae el cron; con CASCADE se borra plata).
13. **Un test de regresión que pasa sin el fix no prueba nada**: en los tests críticos,
    verificá mutando el código que fallan sin él.
14. **No se agregan dependencias** a `requirements.txt` (el SDK `mercadopago` arrastra
    `requests`, que el proyecto rechazó; se usa `urllib` de la stdlib).
15. **Comentarios de template de varias líneas con `{% comment %}`**, nunca `{# #}`
    partido en dos líneas (Django lo imprime en pantalla).

---

### Task 1: Plan anual en `plataforma/precios.py`

**Dónde encaja:** el precio y los períodos de la suscripción viven en
`plataforma/precios.py`, un módulo **Django-free a propósito** (sin ORM, sin `timezone`,
sin settings; el «hoy» lo pasa quien llama; se testea con `SimpleTestCase` en
`plataforma/tests_precios.py`). Hay que agregarle el plan anual sin cambiar nada de lo
que ya hace para el mensual.

**Archivos:** `plataforma/precios.py`, `plataforma/tests_precios.py`. Nada más.

**Qué agregar, exacto:**

```python
#: Meses que cubre el plan anual.
MESES_ANIO = 12

#: Meses que se regalan al pagar el año por adelantado. Supuesto del dueño del
#: producto, pendiente de confirmar: se pagan 10 de 12.
DESCUENTO_ANUAL_MESES = 2

#: Días del período anual: 12 ciclos de `DIAS_CICLO`, o sea 360 A PROPÓSITO.
DIAS_ANIO = MESES_ANIO * DIAS_CICLO
```

- `precio_usd_anual(alumnos_activos)` → `precio_usd(alumnos_activos) * (MESES_ANIO - DESCUENTO_ANUAL_MESES)`.
- `periodo_siguiente(inicio, cubierto_hasta, dias=DIAS_CICLO)`: mismo cuerpo, con `dias`
  en lugar de `DIAS_CICLO` en el cálculo de `hasta` (`desde + timedelta(days=dias - 1)`).
  **El default es lo que deja intactos a `proximo_vencimiento`, `estado_pago` y
  `dias_de_atraso`**, que no se tocan: la noción de «cuándo vence el próximo cobro» del
  monitor no puede depender de qué plan podría elegir el gimnasio.

**Docstrings que tienen que decir el porqué** (en el estilo del módulo):
- `DIAS_ANIO`: por qué 360 y no 365. Con un año calendario (a) se regalan 5 días y (b) —lo
  grave— los períodos mensual y anual dejan de encajar en la misma grilla de 30 días, y un
  gimnasio que paga anual y después mensual arrastra un corrimiento en `periodo_desde`
  para siempre.
- `precio_usd_anual`: el anual congela el escalón de precio por 12 meses (un gimnasio de
  95 alumnos que llega a 250 paga todo el año al precio de 100). Es una decisión
  comercial asumida, no un bug.

**Tests** (en `plataforma/tests_precios.py`, `SimpleTestCase`, fechas literales):
- Los tests que ya existen pasan **sin tocar una línea**.
- `periodo_siguiente(..., dias=DIAS_ANIO)` da un período de exactamente 360 días
  (inclusivo: `hasta - desde == timedelta(days=359)`), con y sin `cubierto_hasta`.
- Mensual y anual para el mismo gimnasio arrancan el mismo `periodo_desde`.
- Después de un anual, el `periodo_siguiente` mensual arranca el día siguiente al fin del
  anual (encaja en la grilla: `desde_siguiente - desde_anual == timedelta(days=360)`).
- `precio_usd_anual` = mensual × 10 en los tres escalones y en los bordes 100/101 y
  300/301 (100 → 100 USD, 101 → 150, 300 → 150, 301 → 200).
- `DIAS_ANIO == 360`.

**Listo cuando:** `python manage.py test plataforma.tests_precios` en verde y
`python manage.py test plataforma` en verde. Un commit.

---

### Task 2: `PagoPlataforma.origen` y `registrado_por` nullable

**Dónde encaja:** hoy todo pago de la plataforma lo tipea el superadmin, y
`PagoPlataforma.registrado_por` es un FK obligatorio a `User` (PROTECT). Un pago que llega
solo por Mercado Pago no tiene quién lo haya tipeado: **es el bloqueante duro** del cobro
online. Esta tarea abre ese lugar sin degradar el camino manual. **Es la única migración
sobre una tabla con plata real: va sola en su commit** para poder revertirse sin arrastrar
nada.

**Archivos:** `plataforma/models.py`, `plataforma/migrations/0005_*.py` (nueva),
`plataforma/views.py` (sólo `PagoPlataformaCreateView.form_valid`), `plataforma/admin.py`,
`templates/plataforma/gimnasio_detalle.html` (la tabla «Pagos registrados»), y tests en
`plataforma/tests_facturacion.py`. **`plataforma/forms.py` no cambia**: `origen` queda
fuera de `PagoPlataformaForm.Meta.fields`, igual que `gimnasio` y `registrado_por`.

**Qué hacer:**
1. En `PagoPlataforma`:
   ```python
   class Origen(models.TextChoices):
       MANUAL = "manual", "Manual"
       ONLINE = "online", "Mercado Pago"
   ```
   `origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.MANUAL)`.
   `registrado_por`: agregar `null=True, blank=True` (sigue PROTECT).
2. Constraint nueva en `Meta.constraints`:
   ```python
   models.CheckConstraint(
       condition=models.Q(origen="online") | models.Q(registrado_por__isnull=False),
       name="pago_plataforma_manual_con_autor",
   )
   ```
   Con **string literal** `"online"` y no `Origen.ONLINE`: dentro de la `Meta` anidada el
   nombre de la clase de afuera no está en scope (mismo motivo que documenta
   `solicitudes/models.py` en su `UniqueConstraint`). **Unidireccional a propósito**: lo que
   importa es que un pago tipeado a mano siempre tenga autor; prohibir el autor en una fila
   online no compra nada y bloquearía que el superadmin repare un cobro online. Escribilo
   en un comentario al lado.
3. **Actualizar el docstring de la clase**: hoy dice que no hay integración de cobros.
   Tiene que decir que los pagos entran por dos caminos (el registro manual del
   superadmin y el cobro online de la app `suscripcion`), que `origen` los distingue, y por
   qué `registrado_por` es nullable.
4. `PagoPlataformaCreateView.form_valid`: estampar `form.instance.origen =
   PagoPlataforma.Origen.MANUAL` al lado de donde ya estampa `registrado_por` (explícito,
   aunque el default lo cubra: es la vista del camino manual y lo tiene que decir).
5. `plataforma/admin.py` (`PagoPlataformaAdmin`): `origen` en `list_display` y en
   `list_filter`.
6. `gimnasio_detalle.html`, tabla «Pagos registrados»: una columna «Origen» que muestre
   `get_origen_display`. Una fila sin autor no puede quedar con un hueco sin explicación.

**Tests** (en `plataforma/tests_facturacion.py`, junto a `PagoPlataformaModeloTests`):
- Un pago creado sin `origen` queda `MANUAL`.
- Un `MANUAL` sin `registrado_por` levanta `IntegrityError` (envolvé el `create` en su
  propio `transaction.atomic()`).
- Un `ONLINE` sin `registrado_por` se guarda.
- El registro por la vista queda `MANUAL` y con `registrado_por` = el superadmin.
- **Ningún test existente se modifica.** Si alguno falla, el cambio está mal, no el test.

**Verificación de la migración (la «Puerta A» del plan):** copiá la base de desarrollo del
checkout principal al worktree (`cp /Users/fabriziosola/app_gim/db.sqlite3 ./db.sqlite3`,
está en `.gitignore`), corré `python manage.py migrate` y verificá con
`python manage.py shell -c "..."` que toda fila vieja de `PagoPlataforma` quedó `MANUAL` y
con autor. Anotá el resultado (cantidad de filas y el conteo por origen) en tu reporte y
**borrá `db.sqlite3` del worktree al terminar**.

**Listo cuando:** `python manage.py makemigrations --check --dry-run` no detecta nada
pendiente, `python manage.py test plataforma` en verde sin modificar tests viejos, y la
verificación de arriba anotada en el reporte. Un commit.

---

### Task 3: Cliente de Mercado Pago y configuración

**Dónde encaja:** la app nueva `suscripcion` habla con Mercado Pago (Checkout Pro) por un
módulo propio. Esta tarea crea el paquete de la app **sin registrarlo todavía en
`INSTALLED_APPS`** (lo registra la tarea siguiente, junto con su primer modelo), el cliente
HTTP y la configuración. **No hay modelos ni vistas en esta tarea.**

**Archivos:** `suscripcion/__init__.py` (vacío), `suscripcion/mercadopago.py`,
`suscripcion/tests_mercadopago.py`, `config/settings.py`, `render.yaml`, `.env.example`.

Un test de Django dentro de una app no registrada no se descubre solo: corrélo por ruta
(`python manage.py test suscripcion.tests_mercadopago`) — como `SimpleTestCase` que no
toca modelos, anda. Si no lo descubre, registrá la app en `INSTALLED_APPS` en esta tarea
(al final, después de `'plataforma'`) y anotalo en el reporte.

**`suscripcion/mercadopago.py`** — el molde es `plataforma/cambio.py` (leelo: `urllib` de
la stdlib, un único punto que toca la red para parchear en tests, `USER_AGENT` propio), **con
la regla de errores al revés**, y el docstring del módulo tiene que decirlo con estas
ideas: `cambio.py` traga todo y devuelve `None` porque el panel no puede caerse por una API
de terceros y no hay plata en juego; acá hay plata, así que **las funciones que hablan con
la API levantan `ErrorDeMercadoPago`** y quien llama decide cómo degradar. Un `None`
silencioso en el camino de la plata haría que un test que olvida parchear la red pase en
verde con el pago sin acreditar. El docstring también dice: **esta integración usa UNA sola
credencial, la de la plataforma**; no hay OAuth por gimnasio ni split. Si algún día el
gimnasio le cobra a sus alumnos por MP (app `pagos`), eso necesita credenciales POR
gimnasio, cifradas con `calendario.fields.EncryptedTextField`: este módulo no sirve para
eso tal cual.

Contenido:
- Constantes: `BASE = "https://api.mercadopago.com"`, `TIMEOUT = 5` (segundos; corre dentro
  del request en un único worker; cero reintentos internos, el reintento es el de Mercado
  Pago a los 15 minutos o el cron), `USER_AGENT = "TuGimApp/1.0 (+https://www.tugimapp.com)"`.
- `class ErrorDeMercadoPago(Exception)`.
- `_pedir(metodo, ruta, *, cuerpo=None, clave_idempotencia=None)`: **el único punto que
  toca la red.** Si `settings.TESTING` → `raise RuntimeError(...)` (un patch olvidado tiene
  que romper el test, no degradarlo). Lee el token de `settings.MERCADOPAGO_ACCESS_TOKEN`
  en cada llamada (para que `override_settings` sirva). Headers: `Authorization: Bearer
  <token>`, `Content-Type: application/json`, `User-Agent`, y `X-Idempotency-Key` si viene.
  `cuerpo` se serializa con `json.dumps`. Cualquier `HTTPError`, `URLError`, timeout o JSON
  inválido → `ErrorDeMercadoPago` con un mensaje corto (código HTTP y la ruta; **nunca el
  token ni el cuerpo de la respuesta**). Devuelve el JSON parseado.
- `es_de_prueba()` → `settings.MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")`.
- `crear_preferencia(*, titulo, monto_ars, referencia_externa, url_retorno,
  url_notificacion, expira, email_pagador="")` → `{"id": ..., "init_point": ...}`.
  - `init_point` sale de `sandbox_init_point` si `es_de_prueba()`, si no de `init_point`
    (si no, el riesgo concreto es desplegarle un link de sandbox a un cliente real).
  - Payload: `items=[{"title": titulo, "quantity": 1, "unit_price": float(monto_ars),
    "currency_id": "ARS"}]` (**`float()` porque `json.dumps` levanta `TypeError` con un
    `Decimal`**; el monto ya viene cuantizado a 0,01), `external_reference`,
    `back_urls={"success": url_retorno, "failure": url_retorno, "pending": url_retorno}`,
    `binary_mode=True`, `payment_methods={"excluded_payment_types": [{"id": "ticket"},
    {"id": "atm"}]}`, `expires=True`, `expiration_date_from` (ahora) y
    `expiration_date_to` (`expira`), las dos en ISO 8601 UTC con formato
    `%Y-%m-%dT%H:%M:%S.000+00:00`, `statement_descriptor="TUGIMAPP"`, `payer={"email": ...}`
    sólo si viene. **`auto_return="approved"` y `notification_url` sólo si la URL
    respectiva empieza con `https://`** (Mercado Pago rechaza `localhost`/`http` ahí, y en
    desarrollo no hay HTTPS). `X-Idempotency-Key` = `referencia_externa`.
  - Comentario al lado de `binary_mode` y de la exclusión de `ticket`/`atm`: con los dos,
    «pago parcial» o «pendiente por días» dejan de existir como caso (un pago de tarjeta
    no puede quedar en revisión manual de MP, y un Rapipago pagado a los 3 días llegaría
    con el tipo de cambio de hace 3 días). Costo asumido: se pierde al que paga en
    efectivo.
- `consultar_pago(payment_id)` → dict (`GET /v1/payments/{id}`).
- `buscar_pagos(referencia_externa)` → lista (`GET /v1/payments/search?external_reference=
  <ref>&sort=date_created&criteria=desc`, devuelve `respuesta["results"]`). Es lo único que
  cubre «el webhook se perdió y el usuario cerró la pestaña»: ahí nunca hubo un
  `payment_id` que consultar.
- `resumen_de_pago(pago)` → dict con **sólo** estas claves (las que existan): `id`,
  `status`, `status_detail`, `transaction_amount`, `currency_id`, `external_reference`,
  `date_approved`, `date_last_updated`, `payment_type_id`, `payment_method_id`. Es lo que
  se guarda en la base: nada del pagador.
- `firma_valida(secret, *, data_id, request_id, x_signature)` → bool. **Puro, no levanta
  nunca.** El header es `ts=<epoch>,v1=<hex>` (partes separadas por coma, cada una
  `clave=valor`, con espacios tolerados). El manifest se arma concatenando, en este orden
  y **omitiendo el segmento cuyo valor falta** (no va vacío): `id:<data_id en
  minúsculas>;`, `request-id:<request_id>;`, `ts:<ts>;`. Se compara
  `hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()` contra `v1`
  con `hmac.compare_digest`. Sin `secret`, sin `v1` o sin `ts` → `False`. **El `ts` NO se
  valida como ventana de frescura** — docstring: un replay no puede cobrar dos veces porque
  la defensa real es la idempotencia, y un chequeo de reloj es un reloj más que puede
  desincronizarse y rechazar un reintento legítimo de MP a las 3 de la mañana.

**`config/settings.py`**, junto a los otros grupos opcionales (después del bloque de
`PUSH_ENABLED`):
```python
MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")
MERCADOPAGO_WEBHOOK_SECRET = os.environ.get("MERCADOPAGO_WEBHOOK_SECRET", "")
COBRO_ONLINE_ENABLED = (
    _bandera_todo_o_nada(
        ["MERCADOPAGO_ACCESS_TOKEN", "MERCADOPAGO_WEBHOOK_SECRET"],
        "Mercado Pago",
        "para que el cobro online quede oculto y sólo quede el registro manual",
    )
    and not TESTING
)
# A quién le llegan los avisos de cobros online (acreditado, con problema).
# Suelta y opcional, mismo criterio que SOLICITUDES_AVISO_EMAIL: vacía cae a
# DEFAULT_FROM_EMAIL.
COBROS_AVISO_EMAIL = os.environ.get("COBROS_AVISO_EMAIL", "")
```
Con un comentario arriba en el estilo de los otros grupos. **Sin `MERCADOPAGO_PUBLIC_KEY`**:
Checkout Pro por redirección no la usa.

**`render.yaml`**: las tres variables (`MERCADOPAGO_ACCESS_TOKEN`,
`MERCADOPAGO_WEBHOOK_SECRET`, `COBROS_AVISO_EMAIL`) como `- key: ...` con `sync: false` y un
comentario en el estilo del archivo. **Van en este mismo commit**:
`config/tests.py::BlueprintDeclaraLoQueSettingsLeeTests` es bidireccional y falla si
`settings.py` lee una variable que el Blueprint no declara. **`.env.example`**: el grupo
comentado, en el estilo del archivo, diciendo que va el token de PRUEBA (`TEST-…`) en local
y de dónde sale la clave secreta del webhook (panel de MP → Tus integraciones → Webhooks, se
muestra una sola vez).

**Tests** (`suscripcion/tests_mercadopago.py`):
- `firma_valida` con vectores **literales** (calculá el HMAC esperado en el test con
  `hmac`/`hashlib` sobre un manifest escrito a mano, no con la función bajo prueba): firma
  correcta → True; un carácter distinto en `v1` → False; `data_id` alfanumérico en
  mayúsculas se valida contra el manifest en minúsculas; **sin `request_id` el manifest
  omite el segmento** (`id:...;ts:...;`) y valida; `ts` de hace un año → True; header
  malformado (`"basura"`, `""`, `None`, sin `v1`) → False sin levantar; secret vacío →
  False.
- `_pedir` bajo `TESTING` levanta `RuntimeError`.
- `crear_preferencia` parcheando `_pedir`: el payload trae `unit_price` float, `binary_mode`,
  la exclusión de `ticket`/`atm`, `expires`, `external_reference`, `X-Idempotency-Key`; con
  URLs `http://` no manda `auto_return` ni `notification_url`, con `https://` sí; con token
  `TEST-...` devuelve el `sandbox_init_point` y con otro token el `init_point`.
- `consultar_pago` y `buscar_pagos` arman la ruta correcta; un `ErrorDeMercadoPago` de
  `_pedir` se propaga.
- `resumen_de_pago` descarta el `payer` y cualquier clave fuera de la lista.
- `_pedir` traduce un `HTTPError` (parcheá `urllib.request.urlopen` con
  `override_settings(TESTING=False)` o parcheando el guard) a `ErrorDeMercadoPago` **sin el
  token en el mensaje**.

**Listo cuando:** `python manage.py test suscripcion.tests_mercadopago config` en verde
(`config` incluye el test del Blueprint), `python manage.py check` limpio, y arrancar con
sólo una de las dos `MERCADOPAGO_*` seteada levanta `ImproperlyConfigured` (probalo a mano
con `MERCADOPAGO_ACCESS_TOKEN=x python manage.py check` y anotalo). Un commit.

---

### Task 4: Modelo `IntentoDeCobro` y los guards de gimnasio anotado

**Dónde encaja:** un intento de cobro es el precio congelado en el momento en que se
generó un link de pago de Mercado Pago. Esta tarea registra la app `suscripcion`, crea el
modelo y agrega en `plataforma/facturacion.py` los dos guards que evitan el bug más fácil
de introducir en todo el proyecto: `facturacion.periodo_a_cobrar(gimnasio)` usa
`getattr(gimnasio, "cubierto_hasta", None)`, así que con un `Gimnasio` que NO vino de
`facturacion.gimnasios_anotados()` devuelve `None` **en silencio** y cobra siempre el
primer período después de la prueba.

**Archivos:** `suscripcion/apps.py`, `suscripcion/models.py`,
`suscripcion/migrations/__init__.py`, `suscripcion/migrations/0001_initial.py`,
`suscripcion/admin.py`, `suscripcion/tests_modelos.py`, `config/settings.py`
(`INSTALLED_APPS` y su comentario), `plataforma/facturacion.py`,
`plataforma/tests_facturacion.py`.

**`config/settings.py`:** `'suscripcion'` al final de `INSTALLED_APPS`, después de
`'plataforma'` (si la Task 3 ya lo registró, dejalo). **Corregí el comentario de la
lista**: hoy dice que `plataforma` va última porque nadie la lee; ahora la lee
`suscripcion`. Agregá la línea de `suscripcion` explicando qué es y por qué va última.

**`suscripcion/apps.py`:** `SuscripcionConfig`, `name = "suscripcion"`,
`verbose_name = "Suscripción"`.

**`suscripcion/models.py`** — docstring del módulo: por qué no es `TenantOwnedModel`
(dato de la plataforma sobre el gimnasio, mismo criterio que `plataforma.PagoPlataforma` y
`solicitudes`) y por qué **no tiene FK a `User`** (`tenants/demo.py::vaciar_gimnasio`
borra los `User` de la cuenta demo cada 6 h vía `restaurar-demo.yml`: con PROTECT ese cron
se cae con `ProtectedError`, con CASCADE se borra un registro de plata; es la lección de
`plataforma.ActividadDiaria`).

```python
class IntentoDeCobro(TimeStampedModel):   # core.models.TimeStampedModel
    class Plan(models.TextChoices):
        MENSUAL = "mensual", "Mensual"
        ANUAL = "anual", "Anual"

    class Estado(models.TextChoices):
        CREADO = "creado", "Link generado"
        PAGADO = "pagado", "Pagado"
        RECHAZADO = "rechazado", "Rechazado"
        VENCIDO = "vencido", "Link vencido"
        CANCELADO = "cancelado", "Cancelado"
        DUPLICADO = "duplicado", "Pago duplicado"
        REVISAR = "revisar", "A revisar"
        DEVUELTO = "devuelto", "Devuelto"
```
Campos, exactos:
| campo | definición |
|---|---|
| `gimnasio` | `ForeignKey("tenants.Gimnasio", on_delete=PROTECT, related_name="intentos_de_cobro")` |
| `plan` | `CharField(max_length=10, choices=Plan.choices)` |
| `periodo_desde` | `DateField()` |
| `periodo_hasta` | `DateField()` — INCLUSIVO |
| `monto_usd` | `DecimalField(max_digits=8, decimal_places=2)` |
| `monto_ars` | `DecimalField(max_digits=12, decimal_places=2)` |
| `tipo_cambio` | `DecimalField(max_digits=10, decimal_places=2)` |
| `alumnos_activos` | `PositiveIntegerField()` |
| `referencia_externa` | `UUIDField(default=uuid.uuid4, unique=True, editable=False)` |
| `preference_id` | `CharField(max_length=64, blank=True)` |
| `init_point` | `URLField(max_length=500, blank=True)` |
| `expira` | `DateTimeField()` |
| `estado` | `CharField(max_length=12, choices=Estado.choices, default=Estado.CREADO)` |
| `payment_id` | `CharField(max_length=32, blank=True)` |
| `estado_mp` | `CharField(max_length=30, blank=True)` |
| `respuesta_mp` | `JSONField(default=dict, blank=True)` |
| `acreditado_en` | `DateTimeField(null=True, blank=True)` |
| `pago` | `OneToOneField("plataforma.PagoPlataforma", on_delete=PROTECT, null=True, blank=True, related_name="intento_de_cobro")` |
| `email_de_quien_pago` | `CharField(max_length=254, blank=True)` — quién apretó «Pagar»; texto y no FK, ver docstring |

`Meta`: `ordering = ["-creado", "-id"]`; `indexes = [Index(fields=["gimnasio", "estado"])]`;
`constraints` (con string literals en las condiciones, por el scope de la `Meta` anidada):
- `UniqueConstraint(fields=["gimnasio", "periodo_desde"], condition=Q(estado="creado"),
  name="suscripcion_un_intento_abierto_por_periodo")` — comentario: un solo link vivo por
  período; es lo que hace que el segundo click reuse el link en vez de crear un segundo
  cobro del mismo mes (calcado de `ingreso/models.py`, `UniqueConstraint` parcial).
- `UniqueConstraint(fields=["payment_id"], condition=~Q(payment_id=""),
  name="suscripcion_un_payment_id")`.
- `CheckConstraint(condition=Q(periodo_hasta__gte=F("periodo_desde")),
  name="suscripcion_periodo_no_invertido")`.

`__str__`: gimnasio, plan, período y estado.

**`suscripcion/admin.py`:** `IntentoDeCobroAdmin` de **sólo lectura** (`has_add_permission`,
`has_change_permission` y `has_delete_permission` → False; comentario: un cobro no se edita a
mano, se resuelve en el panel de Mercado Pago), `list_display` con gimnasio, plan, período,
monto ARS, estado, `estado_mp`, creado; `list_filter` por estado y plan;
`list_select_related = ("gimnasio",)`.

**`plataforma/facturacion.py`**, junto a `periodo_a_cobrar`:
- `exigir_anotado(gimnasio)`: `ValueError` con un mensaje que nombre `gimnasios_anotados()`
  si la instancia no tiene el atributo `cubierto_hasta` (usar `hasattr`: un anotado que
  nunca pagó tiene `cubierto_hasta = None` y **sí** pasa).
- `gimnasio_anotado(pk)`: `gimnasios_anotados().get(pk=pk)` (levanta `DoesNotExist` como
  cualquier `get`). Docstring: es el ÚNICO acceso desde `suscripcion` a un gimnasio para
  facturar.
- `periodo_a_cobrar(gimnasio, dias=precios.DIAS_CICLO)`: pasa `dias` a
  `precios.periodo_siguiente`. **Su `getattr` no se toca** (los tests con `Gimnasio` pelado
  del monitor dependen de él; el guard vive en `exigir_anotado`).

**Tests:**
- `suscripcion/tests_modelos.py`: dos `CREADO` del mismo gimnasio y `periodo_desde` chocan
  (`IntegrityError`, en su propio `atomic`); un `CREADO` + un `VENCIDO` del mismo período
  conviven; dos intentos con `payment_id=""` conviven; dos con el mismo `payment_id` no
  empty chocan; período invertido choca; `IntentoDeCobro` **no** es subclase de
  `core.models.TenantOwnedModel`; **`tenants/demo.py::vaciar_gimnasio` sobre un gimnasio
  demo con un intento colgado no levanta** (leé cómo arma el gimnasio demo
  `tenants/tests.py` en `VaciarGimnasioTests`). Para crear gimnasios usá
  `tenants.services.crear_gimnasio` o los helpers que ya usan los tests de `plataforma`.
- `plataforma/tests_facturacion.py`: `exigir_anotado` levanta con un
  `Gimnasio.objects.get(pk=...)` pelado y no levanta con uno de `gimnasios_anotados()`
  (con y sin pagos); `periodo_a_cobrar(g, dias=precios.DIAS_ANIO)` da 360 días y sin `dias`
  sigue dando 30.

**Listo cuando:** `makemigrations --check --dry-run` limpio,
`python manage.py test suscripcion plataforma tenants` en verde. Un commit.

---

### Task 5: Servicios del cobro (`crear_intento`, `acreditar`, reconciliación) y mails

**Dónde encaja:** es el camino de la plata. `suscripcion/services.py` es el **ÚNICO lugar
que escribe** sobre `IntentoDeCobro` y el único que crea un `PagoPlataforma` con
`origen=ONLINE`. Las vistas (Task 6) y los comandos (Tasks 7-8) sólo lo llaman. Leé antes:
`solicitudes/services.py` (el molde de transición idempotente con `select_for_update()` +
re-chequeo de estado, y de mails por `on_commit`), `solicitudes/emails.py` (el molde de
mails), `plataforma/facturacion.py`, `plataforma/precios.py`, `plataforma/cambio.py`
(`cotizacion_dolar()` y `pesos()`), `plataforma/views.py::PagoPlataformaCreateView` (cómo se
crea un pago hoy), y `CLAUDE.md` § «Panel de plataforma».

**Archivos:** `suscripcion/services.py`, `suscripcion/emails.py`,
`templates/suscripcion/email/cobro_acreditado.txt`,
`templates/suscripcion/email/cobro_con_problema.txt`, `suscripcion/tests_services.py`.

**Constantes (en `services.py`):**
```python
HORAS_VALIDEZ_LINK = 48
TOLERANCIA_MONTO = Decimal("0.01")
DIAS_RECONCILIACION = 7
ESTADOS_MP_APROBADO = frozenset({"approved"})
ESTADOS_MP_RECHAZADO = frozenset({"rejected", "cancelled"})
ESTADOS_MP_DEVUELTO = frozenset({"refunded", "charged_back"})
```
Docstring de `HORAS_VALIDEZ_LINK`: 48 y no 24 porque el aviso que llega un viernes a la
noche tiene que poder pagarse el domingo; y no una semana porque el tipo de cambio queda
congelado en el link.

**`class CobroNoDisponible(Exception)`** con atributo `motivo`, uno de:
`"integracion_apagada"`, `"no_aplica"`, `"sin_cotizacion"`, `"mercadopago"`. Y un dict
`MENSAJES` con el texto que ve el gimnasio para cada uno (el de `sin_cotizacion`: *«Ahora
mismo no podemos calcular el monto en pesos. Probá de nuevo en unos minutos.»*; el de
`mercadopago`: que Mercado Pago no respondió y que pruebe en unos minutos; el de
`no_aplica`: que a su cuenta no se le cobra la suscripción).

**`integracion_activa()`** → `bool(settings.COBRO_ONLINE_ENABLED)`.

**`vencer_intentos_abiertos(gimnasio, *, ahora)`** → int. `QuerySet.update(estado=VENCIDO,
modificado=ahora)` sobre los `CREADO` de ese gimnasio con `expira < ahora` (`update()` no
dispara `auto_now`: `modificado` va explícito). **Vencer es perezoso**: lo llama
`crear_intento`, en la misma transacción, antes de insertar. Docstring: con el índice
único parcial, un `CREADO` eterno bloquearía que el gimnasio genere un link nuevo, y el cron
de GitHub Actions llega 2-4 h tarde en el plan free; por eso vencer no puede depender de él.

**`crear_intento(gimnasio_pk, plan, *, url_retorno_para, url_notificacion,
email_de_quien_pago="")`** → `IntentoDeCobro`. `url_retorno_para` es un callable
`(referencia_externa) -> str` absoluto y `url_notificacion` un str absoluto: los arma la
vista (tiene el `request`), mismo criterio que `solicitudes`. Orden:
1. Sin `integracion_activa()` → `CobroNoDisponible("integracion_apagada")`.
2. `ahora = timezone.now()`. Todo lo que sigue dentro de `transaction.atomic()`.
3. `Gimnasio.objects.select_for_update().get(pk=gimnasio_pk)` para serializar dos clicks
   concurrentes del mismo gimnasio; después
   `gimnasio = facturacion.gimnasio_anotado(gimnasio_pk)` y
   `facturacion.exigir_anotado(gimnasio)`.
4. `not gimnasio.facturacion_aplica` → `CobroNoDisponible("no_aplica")`
   (`Gimnasio.facturacion_aplica` es el único lugar donde vive la regla: no la
   reimplementes).
5. `vencer_intentos_abiertos(gimnasio, ahora=ahora)`.
6. `dias = precios.DIAS_ANIO if plan == ANUAL else precios.DIAS_CICLO`;
   `desde, hasta = facturacion.periodo_a_cobrar(gimnasio, dias=dias)`.
7. Si hay un `CREADO` de ese gimnasio con ese `periodo_desde`: si es del **mismo plan** →
   devolverlo tal cual (se reusa el link vivo: el doble click no genera un segundo cobro);
   si es de **otro plan** → pasarlo a `CANCELADO` (con `update()` y `modificado`) y seguir.
8. `cotizacion = cambio.cotizacion_dolar()`; `None` → `logger.warning` con el slug del
   gimnasio (que un cliente no haya podido pagar es un evento que se quiere ver) y
   `CobroNoDisponible("sin_cotizacion")`. **Nunca un monto en 0 ni un tipo de cambio
   inventado.**
9. `monto_usd = precios.precio_usd_anual(a) if ANUAL else precios.precio_usd(a)` con
   `a = gimnasio.alumnos_activos`; `monto_ars = cambio.pesos(monto_usd, cotizacion)`;
   `tipo_cambio = cotizacion["venta"]`.
10. Crear el intento (`expira = ahora + timedelta(hours=HORAS_VALIDEZ_LINK)`).
11. `mercadopago.crear_preferencia(...)` con título `f"TuGimApp — plan {plan} del
    {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"`. `ErrorDeMercadoPago` →
    `CobroNoDisponible("mercadopago")` **levantado adentro del `atomic`, para que el rollback
    no deje un intento huérfano sin link**.
12. Guardar `preference_id` e `init_point` y devolver el intento.

**`acreditar(payment_id, *, url_panel_para=None)`** → `IntentoDeCobro | None`. Es el
**único punto de entrada** para acreditar: lo llaman el webhook, el retorno del navegador y
la reconciliación, y tiene que llegar al mismo estado final sin importar quién llegue
primero (el webhook llega antes que el navegador en el caso normal). **No recibe
`request`**: recibe `url_panel_para`, un callable `(gimnasio) -> str` opcional para el link
del mail al superadmin (el cron no tiene request y pasa `None`). Orden:
1. `pago = mercadopago.consultar_pago(payment_id)` **ANTES** de abrir la transacción (no se
   retiene un lock mientras se espera a la red). `ErrorDeMercadoPago` se propaga: quien
   llama decide (el webhook responde 500 para que MP reintente; el retorno muestra
   «confirmando»). **El estado sale SIEMPRE de esta consulta, nunca del cuerpo de un
   webhook**, que viaja sin firmar (la firma cubre el `data.id`, no el `action`).
2. `external_reference` del pago → UUID; si no parsea o no hay intento con esa referencia →
   `return None` (nada que hacer).
3. `with transaction.atomic()`: `intento = IntentoDeCobro.objects.select_for_update()
   .select_related("gimnasio").get(pk=...)`.
4. `pago_id = str(pago["id"])`; `estado_mp = pago.get("status", "")`.
5. **Un pago distinto del que el intento ya tiene guardado** (un link de preferencia se
   puede pagar más de una vez). Depende del estado del intento, y la distinción no es
   opcional:
   - Intento en `PAGADO`, `DUPLICADO`, `REVISAR` o `DEVUELTO` (el pago anterior movió
     plata): **no se pisa nada**; si el nuevo está aprobado, mail «con problema» al
     superadmin (`"pago_adicional"`: hay que devolverlo) y `return intento`. Una
     notificación vieja de un pago rechazado que llega tarde cae acá y no hace nada.
   - Intento en `CREADO`, `VENCIDO`, `CANCELADO` o `RECHAZADO` (el pago anterior no se
     acreditó): **se reemplaza el `payment_id` y se sigue**. Es el caso normal de una
     tarjeta rechazada seguida de un reintento con otra tarjeta en la misma pantalla de
     Mercado Pago: tratarlo como «pago adicional» dejaría sin acreditar un pago aprobado.
     Comentario obligatorio con esto.
6. **Cortocircuito por `(payment_id, estado_mp)`, no por `payment_id` solo**: si
   `intento.payment_id == pago_id and intento.estado_mp == estado_mp` → `return intento`.
   Comentario: Mercado Pago manda otra notificación con el MISMO `payment_id` cuando el
   pago pasa a `refunded`/`charged_back`; cortar por id solo se tragaría la devolución y el
   gimnasio se quedaría con el período cobrado.
7. Guardar `payment_id`, `estado_mp` y `respuesta_mp = mercadopago.resumen_de_pago(pago)`.
8. Si `estado_mp` está en `ESTADOS_MP_APROBADO` y el intento está en `CREADO`, `VENCIDO`,
   `CANCELADO` o `RECHAZADO` (**`VENCIDO` y `CANCELADO` incluidos a propósito**: la plata en
   la cuenta le gana a tu reloj; comentario obligatorio, porque un `filter(estado=CREADO)`
   acá se come un pago real):
   - **Validar, nunca escribir, el monto de MP**: si `currency_id != "ARS"` o
     `abs(Decimal(str(pago["transaction_amount"])) - intento.monto_ars) > TOLERANCIA_MONTO`
     → `REVISAR` + mail «con problema». Es `transaction_amount` y no `total_paid_amount`
     (con cuotas incluye el interés del comprador, que no es un pago de más) ni
     `net_received_amount` (resta la comisión: usarlo bajaría el KPI de ingresos ~2% en
     silencio). Comentario con esto.
   - **Solapamiento**: si existe un `PagoPlataforma` del gimnasio con
     `periodo_desde <= intento.periodo_hasta` y `periodo_hasta >= intento.periodo_desde` →
     `DUPLICADO` + mail «con problema» (el superadmin pudo haber cargado ese período a
     mano). Comentario: la `UniqueConstraint(gimnasio, periodo_desde)` sólo ve el mismo día
     de inicio.
   - Crear el `PagoPlataforma` **dentro de su propio `transaction.atomic()` con `try/except
     IntegrityError`** → ante el error, `DUPLICADO` + mail. **Nunca tragarlo en silencio**:
     el `except IntegrityError: return None` de `solicitudes` existe para no filtrar
     información; acá significaría quedarse con la plata sin decírselo a nadie.
     Campos: `gimnasio`, `fecha_pago` = la fecha LOCAL de `date_approved` (ISO con
     offset; si falta o no parsea, `timezone.localdate()`), `monto_usd`, `monto_ars` y
     `tipo_cambio` **del intento** (no de MP), `alumnos_activos`, `periodo_desde`,
     `periodo_hasta`, `notas = f"Mercado Pago, pago {pago_id}"`, `origen=ONLINE`,
     `registrado_por=None`.
   - Intento → `PAGADO`, `pago`, `acreditado_en = timezone.now()`.
   - **Descongelar** (ver abajo) y mail «acreditado» al superadmin.
9. Si `estado_mp` está en `ESTADOS_MP_RECHAZADO` y el intento está en `CREADO`/`VENCIDO` →
   `RECHAZADO` (sin mail: el gimnasio lo ve en pantalla y reintenta con un link nuevo; el
   índice parcial ya lo libera porque sólo mira `CREADO`).
10. Si `estado_mp` está en `ESTADOS_MP_DEVUELTO` y el intento está `PAGADO` → `DEVUELTO` +
    mail «con problema» que diga si el gimnasio está hoy en `NORMAL`. **No se borra el
    `PagoPlataforma` ni se recongela la cuenta**: cortar el acceso es siempre una decisión
    humana (`tenants/models.py`, docstring de `Gimnasio.EstadoCuenta`).
11. Cualquier otro status (`pending`, `in_process`, `authorized`…) sólo queda guardado.
12. Guardar el intento y devolverlo.

**La regla de descongelamiento** (paso 8, dentro de la misma transacción): se descongela si
y sólo si (a) el status aprobado vino de `consultar_pago`, (b) el monto coincidió, (c) el
`PagoPlataforma` se creó en esta transacción, y (d) recalculado
`PagoPlataforma.objects.filter(gimnasio=...).aggregate(Max("periodo_hasta"))`,
**`cubierto_hasta >= timezone.localdate()`**. Si el gimnasio no está en `NORMAL` y se
cumple → `Gimnasio.objects.filter(pk=...).update(estado_cuenta=NORMAL,
estado_cuenta_desde=None)` (**los dos campos en el mismo `update()`**: una fecha vieja
sobre una cuenta normal se lee como que sigue congelada; y `update()` por
`version_media`). Si no se cumple (tres meses de deuda pagando uno) no se descongela, y el
mail dice desde qué fecha sigue debiendo (`cubierto_hasta + 1 día`). Docstring con el
argumento que evita que el próximo lector lo borre por «violar la invariante»: la regla
de `Gimnasio.EstadoCuenta` («los escalones son MANUALES, nunca un cron») está escrita
entera sobre el peligro de **cortar** automático; restaurar tiene el riesgo inverso. La
regla es **«restauración automática, corte nunca»**. Riesgo aceptado, también en el
docstring: `estado_cuenta` no guarda motivo, así que un gimnasio congelado por otra razón
se descongela con un pago; el arreglo futuro es un campo `motivo`, no un caso especial acá.

**`reconciliar_pendientes(*, limite=50, url_panel_para=None)`** → int (acreditados).
Recorre hasta `limite` intentos en `CREADO`/`VENCIDO` creados en los últimos
`DIAS_RECONCILIACION` días, sin `payment_id`; por cada uno `buscar_pagos(referencia)` y, por
cada resultado, `acreditar(resultado["id"], ...)`. Un `ErrorDeMercadoPago` en un intento se
loguea y **no corta el resto**. Sin `integracion_activa()` → `CobroNoDisponible`.

**`suscripcion/emails.py`** — molde exacto de `solicitudes/emails.py` (texto plano,
`EmailMessage`, `reply_to` desde `EMAIL_REPLY_TO` si está, **`fail_silently=True`** con el
mismo comentario de por qué). Destino: `settings.COBROS_AVISO_EMAIL or
settings.DEFAULT_FROM_EMAIL`. Dos funciones:
- `enviar_cobro_acreditado(intento, *, descongelado, sigue_debiendo_desde, url_panel)`.
- `enviar_cobro_con_problema(intento, *, problema, url_panel)` con `problema` en
  `{"duplicado", "revisar", "devuelto", "pago_adicional"}` y un texto por cada uno que diga
  qué hacer (devolver la plata desde el panel de MP, revisar el monto, etc.).
Los dos templates `.txt` llevan gimnasio, plan, período, montos USD/ARS, `payment_id` y el
link al panel **sólo si hay** `url_panel`.

**Tests** (`suscripcion/tests_services.py`). Los mails salen por `on_commit`: usá
`TransactionTestCase` (bajo `TestCase` nunca corren; `CLAUDE.md` § «Datos de
demostración» cuenta la lección). Parcheá `suscripcion.mercadopago.crear_preferencia`,
`consultar_pago`, `buscar_pagos` y `plataforma.cambio.cotizacion_dolar`; usá
`override_settings(COBRO_ONLINE_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", COBROS_AVISO_EMAIL="alertas@x.com")`.
Uno por rama, como mínimo:
- `crear_intento`: gimnasio demo / exento / inactivo → `CobroNoDisponible("no_aplica")`;
  sin cotización → `"sin_cotizacion"` y no queda ningún intento; `ErrorDeMercadoPago` →
  `"mercadopago"` y **no queda ningún intento**; integración apagada →
  `"integracion_apagada"`; el período sale de `gimnasios_anotados()` (un gimnasio con un
  pago previo cobra el período siguiente, no el primero post-prueba); dos llamadas
  seguidas del mismo plan devuelven **el mismo** intento; cambiar de plan cancela el
  anterior; un `CREADO` vencido se vence solo y deja crear uno nuevo; el anual congela
  `precio_usd_anual` y 360 días.
- `acreditar`: dos veces con el mismo pago → **un** `PagoPlataforma` (`origen=ONLINE`, sin
  autor, montos del intento); webhook-primero y retorno-primero terminan igual (dos
  llamadas en cualquier orden); monto distinto → `REVISAR`, sin pago, con mail; moneda
  distinta → `REVISAR`; solapamiento con un pago manual de otra fecha de inicio →
  `DUPLICADO`, con mail, sin segundo pago; `rejected` → `RECHAZADO` y después se puede
  crear un intento nuevo; **un pago rechazado seguido de otro aprobado sobre el mismo
  intento → se acredita el aprobado**; un `VENCIDO` que aparece aprobado **sí** se acredita;
  un pago adicional sobre un intento ya pagado → mail «pago_adicional», nada pisado; la
  notificación tardía del pago rechazado sobre un intento ya pagado no cambia nada; `refunded`
  sobre un `PAGADO` → `DEVUELTO`, el `PagoPlataforma` sigue existiendo, mail; referencia
  desconocida → `None`.
- Descongelamiento: gimnasio `SUSPENDIDA` que queda al día → `NORMAL`,
  `estado_cuenta_desde` en `None` y **`modificado` intacto** (compará antes y después con
  `refresh_from_db`); gimnasio con tres períodos adeudados que paga uno → sigue congelado
  y el mail dice desde cuándo debe.
- `reconciliar_pendientes`: acredita lo que encuentra por `buscar_pagos`; un
  `ErrorDeMercadoPago` en un intento no impide acreditar el siguiente.
- **Verificación por mutación** (anotala en el reporte): cambiar temporalmente el
  cortocircuito a «por `payment_id` solo» tiene que romper el test del `refunded`; quitar
  `VENCIDO` de los estados acreditables tiene que romper el suyo; quitar la condición (d)
  tiene que romper el de los tres períodos. Revertí las mutaciones.

**Listo cuando:** `python manage.py test suscripcion plataforma` en verde y las tres
mutaciones anotadas. Un commit (o dos: servicios y mails).

---

### Task 6: Vistas, rutas, templates y middleware — el MVP

**Dónde encaja:** con esta tarea el dueño de un gimnasio puede pagar la suscripción desde
la app y se acredita sola. Las vistas **no escriben nada**: llaman a
`suscripcion.services` (Task 5: `crear_intento`, `acreditar`, `integracion_activa`,
`CobroNoDisponible` con su dict `MENSAJES`, `mercadopago.firma_valida`,
`mercadopago.ErrorDeMercadoPago`). Leé antes: `ingreso/views.py::EventosApiView` (el molde
de API sin sesión con `csrf_exempt`), `plataforma/middleware.py` entero,
`templates/plataforma/cuenta_bloqueada.html`, `templates/base.html` (el `banner-atraso` y
el `nav-staff`), `templates/tenants/gimnasio_form.html` (las secciones
`#control-de-ingreso` y `#tus-datos`), `tenants/context_processors.py`, y en `CLAUDE.md` las
secciones «PlataformaMiddleware», «Control de ingreso» (la de los secretos y el boost) y
«Login por gimnasio» (el gotcha de `hx-boost`).

**Archivos:** `suscripcion/views.py`, `suscripcion/forms.py`, `suscripcion/urls.py`,
`suscripcion/context_processors.py`, `config/urls.py`, `config/settings.py` (sólo registrar
el context processor), `plataforma/middleware.py`,
`templates/suscripcion/mi_suscripcion.html`, `templates/suscripcion/retorno.html`,
`templates/plataforma/cuenta_bloqueada.html`, `templates/base.html`,
`templates/tenants/gimnasio_form.html`, `suscripcion/tests_vistas.py`.

**Rutas** (`suscripcion/urls.py`, `app_name = "suscripcion"`; en `config/urls.py`
`path("suscripcion/", include("suscripcion.urls"))`):
| ruta | vista | nombre |
|---|---|---|
| `""` | `MiSuscripcionView` | `mi_suscripcion` |
| `"pagar/"` | `IniciarPagoView` | `iniciar_pago` |
| `"retorno/<uuid:referencia>/"` | `RetornoView` | `retorno` |
| `"v1/webhook/mercadopago/"` | `WebhookView` | `webhook` |
Comentario en `urls.py`: la versión va en la ruta del webhook porque esa URL queda cargada
en el panel de Mercado Pago y cambiarla no es atómico (mismo criterio que `/ingreso/v1/`).

**`suscripcion/context_processors.py`:** `cobro_online(request)` →
`{"COBRO_ONLINE_DISPONIBLE": settings.COBRO_ONLINE_ENABLED}` (molde:
`tenants.context_processors.google_staff_login_disponible`; cero queries). Registrarlo en
`TEMPLATES[...]["context_processors"]`.

**`suscripcion/forms.py`:** `ElegirPlanForm(forms.Form)` con
`plan = forms.ChoiceField(choices=IntentoDeCobro.Plan.choices, widget=forms.RadioSelect,
initial=IntentoDeCobro.Plan.MENSUAL)`.

**`MiSuscripcionView`** (`StaffRequiredMixin` de `tenants.mixins`, `TemplateView`,
`never_cache` sobre `dispatch`): el gimnasio sale de
`facturacion.gimnasio_anotado(self.request.user.perfil.gimnasio_id)` — **nunca de
`request.user.perfil.gimnasio`**, que es una instancia pelada y cobraría el período
equivocado (ése es el bug que esta tarea más fácilmente reintroduce). Contexto:
`fila = facturacion.fila_de_gimnasio(gimnasio)` (estado, vencimiento, cubierto hasta),
precio mensual y anual en USD y, si hay cotización (una sola llamada a
`cambio.cotizacion_dolar()` por request), en pesos; el intento `CREADO` vivo (`expira >
ahora`) si hay; el form. Casos que la pantalla explica **con un 200, nunca un 4xx** (bajo el
`hx-boost` global un 4xx es un click muerto): integración apagada («el pago online todavía
no está disponible» + `SOPORTE_CONTACTO`); `es_demo` (precio y planes con el **botón
deshabilitado** y una nota: es la cuenta que se les pasa a prospectos); `not
facturacion_aplica` («tu cuenta no tiene cobro»).

**`IniciarPagoView`** (`StaffRequiredMixin`, `View`, `http_method_names = ["post"]`): valida
el form, llama a `services.crear_intento(perfil.gimnasio_id, plan, url_retorno_para=lambda
ref: request.build_absolute_uri(reverse("suscripcion:retorno", args=[ref])),
url_notificacion=request.build_absolute_uri(reverse("suscripcion:webhook")),
email_de_quien_pago=request.user.email)` y redirige al `init_point`
(`HttpResponseRedirect`). `CobroNoDisponible` → re-render de `mi_suscripcion.html` con 200 y
el mensaje de `MENSAJES[motivo]` bien visible. **El form de `mi_suscripcion.html` que postea
acá lleva `hx-boost="false"`** (comentario: el redirect a mercadopago.com.ar es
cross-origin; htmx no puede seguirlo y el click quedaría tragado sin error, mismo caso que
«Conectar Google Calendar»). El form se renderiza campo por campo con
`partials/campo_form.html` (si ese partial no soporta un `RadioSelect`, renderizá los
radios a mano con su error debajo, en el estilo de `.form-campo`). El botón se deshabilita
al enviar (patrón de `plataforma/pago_form.html`).

**`RetornoView`** (`StaffRequiredMixin`, `TemplateView`, `never_cache`): `intento =
get_object_or_404(IntentoDeCobro, referencia_externa=referencia,
gimnasio_id=request.user.perfil.gimnasio_id)` (la referencia viaja por querystring desde el
navegador del gimnasio: es dato del cliente; con UUID no se adivina la del vecino y aun así
se valida el gimnasio). `payment_id = request.GET.get("payment_id") or
request.GET.get("collection_id")`; si hay y `integracion_activa()`, `services.acreditar(
payment_id, url_panel_para=...)` y un `ErrorDeMercadoPago` **no** es un error en pantalla.
**El `status` del querystring se ignora** (es dato del cliente; la autoridad es la consulta
a MP). Recargar el intento y renderizar según su estado: `PAGADO` → «¡Listo! Tu
suscripción está paga hasta DD/MM/AAAA»; `RECHAZADO` → «El pago fue rechazado» + volver a
intentar; `CREADO`/`VENCIDO` → «Estamos confirmando tu pago, se acredita en un minuto» (un
lookup fallido no puede parecerse a un pago fallido); `REVISAR`/`DUPLICADO` → «Recibimos tu
pago y lo estamos revisando; te escribimos»; `DEVUELTO` → «Este pago fue devuelto»;
`CANCELADO` → volver a la página de la suscripción. `url_panel_para` arma
`request.build_absolute_uri(reverse("plataforma:gimnasio_detalle", args=[g.pk]))`.

**`WebhookView`** (`View`, `@method_decorator(csrf_exempt, name="dispatch")`,
`http_method_names = ["post"]`). **No mira `request.user` nunca** (es lo que hace seguro el
`csrf_exempt`; docstring). Respuestas `JsonResponse`:
1. Sin `integracion_activa()` → **503** (Mercado Pago reintenta cada 15 minutos hasta que se
   cargue la configuración; con 200 se perderían esas notificaciones).
2. `data_id = request.GET.get("data.id") or request.GET.get("id")`; si falta, del cuerpo JSON
   (`["data"]["id"]`). El cuerpo se parsea tolerante: JSON inválido, no-dict o anidado de más
   (`RecursionError`) → cuerpo vacío, **nunca un 500**. `tipo = request.GET.get("type") or
   request.GET.get("topic") or cuerpo.get("type")`.
3. `mercadopago.firma_valida(settings.MERCADOPAGO_WEBHOOK_SECRET, data_id=data_id,
   request_id=request.headers.get("x-request-id", ""), x_signature=request.headers.get(
   "x-signature", ""))` → si no, **401** sin tocar la base, con un `logger.warning` sin
   secretos (401 en serie en el panel de MP = el secret está mal cargado).
4. `tipo != "payment"` → **200** (nada que hacer).
5. `services.acreditar(data_id, url_panel_para=...)`; `ErrorDeMercadoPago` → **500** a
   propósito (MP reintenta a los 15 minutos, que es muchísimo mejor que el cron). Cualquier
   otro resultado, incluido `None` y un intento ya pagado → **200**.
Docstring con la tabla de las tres respuestas y el porqué de cada una. **Anotá en el
docstring que hay que confirmar en el sandbox que las notificaciones de `notification_url`
llegan firmadas**; si llegaran sin firma, se responderían 401, MP reintentaría y la
reconciliación las levantaría igual.

**`plataforma/middleware.py`:** un tercer nivel de allowlist.
```python
#: Apps que el middleware no BLOQUEA para el staff, pero SÍ registra. ...
APPS_SIN_BLOQUEO_CON_REGISTRO = frozenset({"suscripcion"})
```
En `process_view`, después de `registrar_dia_activo(request)` y de `perfil =
perfil_de(request)`, y **antes** de `if not sin_acceso(perfil)`: si `match is not None and
match.app_name in APPS_SIN_BLOQUEO_CON_REGISTRO and perfil is not None and perfil.rol ==
Perfil.Rol.STAFF` → `return None`. Comentario obligatorio con las tres ideas: (1) es la
pantalla donde el gimnasio paga para salir del bloqueo, bloquearla es tirar la llave
adentro de la casa cerrada; (2) no va en `APPS_SIN_BLOQUEO` porque esa lista corre antes de
tocar `request.user` (existe para rutas cacheables y rutas que pide la máquina) y se
perdería el registro del día, que es justo la señal de «el gimnasio sigue vivo del otro lado
de la deuda»; (3) sólo para el staff: el alumno de un gimnasio bloqueado que tipee la URL
tiene que ver el cartel, no un 403. **Y**: el webhook no depende de esto (es anónimo: el
middleware corta antes), escribilo para que nadie crea lo contrario.

**Templates:**
- `templates/suscripcion/mi_suscripcion.html` y `retorno.html` (extienden `base.html`,
  clases existentes `.tarjeta`/`.boton`/`.boton-secundario`/`.badge--*`; nada de clases
  utilitarias nuevas que exijan recompilar Tailwind; si hace falta una clase nueva, avisá
  en el reporte en vez de agregarla).
- `cuenta_bloqueada.html`, rama staff: si `COBRO_ONLINE_DISPONIBLE`, un botón «Pagar la
  suscripción» que lleva a `suscripcion:mi_suscripcion` (link interno) arriba del texto de
  soporte; el soporte queda como segunda opción. Actualizá el `{% comment %}` del template:
  hoy dice que no hay links a ninguna sección, y éste es el único que existe a propósito.
- `base.html`, `banner-atraso`: si `COBRO_ONLINE_DISPONIBLE`, un link «Pagar ahora» a
  `suscripcion:mi_suscripcion`.
- `gimnasio_form.html` («Mi gimnasio»): una sección `<section class="tarjeta"
  id="suscripcion">` con un párrafo y un link a `suscripcion:mi_suscripcion`, al lado de
  `#control-de-ingreso`, sin queries nuevas. **El nav del staff NO gana ningún ítem** (se
  queda en 8: el proyecto lo bajó de 10 a propósito).

**Tests** (`suscripcion/tests_vistas.py`):
- Con la cuenta `SUSPENDIDA`, el staff **entra** a `suscripcion:mi_suscripcion` (200 y NO
  el cartel) **y ese request deja su `plataforma.ActividadDiaria`**; los dos asserts juntos
  (molde: `ingreso/tests_vistas.py::PantallasConLaCuentaSuspendidaTests`).
- Con la cuenta `SUSPENDIDA`, el resto del panel sigue mostrando el cartel.
- Un **alumno** de un gimnasio bloqueado que pide `/suscripcion/` ve el cartel, no un 403.
- El webhook: firma mala → 401 y ningún intento tocado; tipo distinto de `payment` → 200;
  `acreditar` levanta `ErrorDeMercadoPago` → 500; cuerpo que no es JSON → nunca 500;
  integración apagada → 503; **con el cliente de test SIN login** (Mercado Pago nunca tiene
  sesión; logueado el test pasaría por el motivo equivocado) y un test que fija que la
  vista **ignora `request.user`** (molde: `ingreso/tests_api.py`). Calculá la firma válida
  en el test con `hmac` sobre el manifest.
- `IniciarPagoView`: GET → 405; POST con `crear_intento` parcheado redirige al
  `init_point`; con `CobroNoDisponible` responde 200 con el mensaje.
- `MiSuscripcionView` usa un gimnasio anotado: con un pago previo muestra el período
  siguiente, no el primero post-prueba.
- `RetornoView`: la referencia de otro gimnasio → 404; con `acreditar` levantando
  `ErrorDeMercadoPago` muestra «confirmando» con 200; con el intento `PAGADO` muestra la
  fecha.
- Demo: el botón sale `disabled`.
- Con `COBRO_ONLINE_ENABLED=False` no aparece el link del banner ni el botón del cartel, y
  `mi_suscripcion` responde 200 explicando.
- El form que postea a `iniciar_pago` lleva `hx-boost="false"` (assert sobre el HTML).
- **`test_toda_vista_declara_su_publico`**: recorre las clases de vista de
  `suscripcion.views` y exige que cada una herede de **exactamente uno** de
  `tenants.mixins.StaffRequiredMixin` / `plataforma.mixins.SuperadminRequiredMixin`, salvo
  una lista explícita de excepciones que sólo contiene `WebhookView` (se autentica por
  firma). Es el reemplazo de la invariante «toda vista de `plataforma` es superadmin» para
  una app con dos públicos.

**Listo cuando:** `python manage.py test suscripcion plataforma tenants ingreso` en verde,
`python manage.py check` limpio, y `ComentariosDeTemplateTests` (barre todos los templates)
en verde. Un commit o dos.

---

### Task 7: Reconciliación diaria (`reconciliar_cobros`)

**Dónde encaja:** red de seguridad para el caso «el webhook se perdió y el usuario cerró la
pestaña». Usa `suscripcion.services.reconciliar_pendientes(limite=..., url_panel_para=None)`
(Task 5) y `services.integracion_activa()` / `services.CobroNoDisponible`.

**Archivos:** `suscripcion/management/__init__.py`,
`suscripcion/management/commands/__init__.py`,
`suscripcion/management/commands/reconciliar_cobros.py`,
`.github/workflows/reconciliar-cobros.yml`, tests en `suscripcion/tests_comandos.py`.

**Comando:** `python manage.py reconciliar_cobros [--limite 50]`. Sin
`integracion_activa()` → **`CommandError`** (sale con error): un cron que no hace nada en
silencio porque faltan los secrets es indistinguible del estado sano, y así el workflow
queda en rojo y Healthchecks no recibe el ping. Imprime cuántos acreditó.

**Workflow:** copiá `.github/workflows/expirar-solicitudes.yml` (leelo entero: SHAs
pineados de las actions con el tag en comentario, `permissions: contents: read`,
`concurrency.group: reconciliar-cobros-production` con `cancel-in-progress: false`,
`python-version: "3.14"`, ping a Healthchecks con el guard **adentro** del `run`). Cron
`"0 */6 * * *"` (comentario: GitHub los retrasa 2-4 h en el plan free; esto es red, no el
camino principal). Env del paso: `DATABASE_URL: ${{ secrets.NEON_DATABASE_URL_POOLED }}`,
`DJANGO_DEBUG: "False"`, `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET`, y —porque
el comando manda mails— `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`DEFAULT_FROM_EMAIL`, `EMAIL_REPLY_TO`, `COBROS_AVISO_EMAIL`, todos desde `secrets.*`, con un
comentario de por qué éste sí necesita más secrets que `expirar-solicitudes` (sin las
`EMAIL_*` el mail cae a la consola en silencio). Secret del ping:
`HEALTHCHECKS_URL_RECONCILIAR_COBROS`.

**Tests:** el comando llama a `reconciliar_pendientes` con el límite; sin integración
levanta `CommandError`.

**Listo cuando:** `python manage.py test suscripcion` en verde y el YAML parsea (no hay
`pyyaml` en el proyecto; usá el Ruby del sistema:
`ruby -ryaml -e 'YAML.load_file(".github/workflows/reconciliar-cobros.yml")'`). Un commit.

---

### Task 8: Aviso de vencimiento al gimnasio (`avisar_cobros`)

**Dónde encaja:** sin esto nadie visita una pantalla de la que no se enteró. Manda un mail
a los usuarios staff del gimnasio cuando la suscripción está por vencer, vence hoy o
venció. Acá el retraso del cron es inocuo (un mail a las 11 en vez de las 9 da igual).

**Archivos:** `suscripcion/models.py` (modelo nuevo), `suscripcion/migrations/0002_*.py`,
`suscripcion/avisos.py`, `suscripcion/emails.py` (una función más),
`templates/suscripcion/email/aviso_cobro.txt`,
`suscripcion/management/commands/avisar_cobros.py`, `.github/workflows/avisar-cobros.yml`,
`suscripcion/tests_avisos.py`.

**Modelo `AvisoDeCobro(models.Model)`** (no `TenantOwnedModel`, sin FK a `User`):
`gimnasio` (FK `tenants.Gimnasio` PROTECT, `related_name="avisos_de_cobro"`),
`periodo_desde` (DateField), `tipo` (`TextChoices`: `POR_VENCER = "por_vencer"`,
`VENCE_HOY = "vence_hoy"`, `VENCIDO = "vencido"`), `enviado` (`DateTimeField(auto_now_add
=True)`); `UniqueConstraint(gimnasio, periodo_desde, tipo, name="suscripcion_un_aviso_por_periodo_y_tipo")`.
Docstring: por qué no se reusa `notificaciones.RecordatorioEnviado` (es
`TenantOwnedModel`, dato del gimnasio; esto es un dato de la plataforma sobre el gimnasio).

**`suscripcion/avisos.py`:** `URL_SITIO = "https://www.tugimapp.com"` (el cron no tiene
`request`; mismo criterio que el `USER_AGENT` de `cambio.py`, que ya escribe el dominio) y
`avisar(hoy=None)` → int:
1. `hoy = hoy or timezone.localdate()`; `filas = facturacion.filas_del_monitor(hoy)` (UNA
   query para todos los gimnasios; no hacer una por gimnasio).
2. Para cada fila con `fila.factura` y `fila.vencimiento`: `dias = (vencimiento - hoy).days`;
   `1 <= dias <= precios.DIAS_AVISO_COBRO` → `POR_VENCER`; `dias == 0` → `VENCE_HOY`;
   `dias < 0` → `VENCIDO`; otro → nada.
3. Destinatarios en **una** query para todos los gimnasios candidatos:
   `Perfil.objects.filter(rol=STAFF, gimnasio_id__in=..., usuario__is_active=True)
   .exclude(usuario__email="").values_list("gimnasio_id", "usuario__email")`.
4. Por cada (gimnasio, tipo): `AvisoDeCobro.objects.get_or_create(...)` dentro de un
   `atomic` propio; si ya existía, no se manda nada (dedup); si se creó, el mail va por
   `on_commit`.
5. Devuelve cuántos avisos nuevos mandó.

**Mail** (`emails.enviar_aviso_cobro(gimnasio, destinatarios, *, tipo, vencimiento,
precio_usd)`, mismo molde que los otros, **destinatarios = el staff del gimnasio**, no el
superadmin): texto por tipo, fecha de vencimiento, precio, y el link
`f"{URL_SITIO}{reverse('suscripcion:mi_suscripcion')}"`.

**Comando** `avisar_cobros`: sin `integracion_activa()` → `CommandError` (el mail invita a
pagar online; sin integración no hay dónde). Imprime cuántos mandó.

**Workflow** `avisar-cobros.yml`, mismo molde que el de la Task 7, cron `"0 12 * * *"`
(09:00 de Argentina), mismos secrets de mail + `MERCADOPAGO_*`, ping
`HEALTHCHECKS_URL_AVISAR_COBROS`, `concurrency.group: avisar-cobros-production`.

**Tests** (`TransactionTestCase` por el `on_commit`): cada tipo en su día; el mismo día dos
veces manda un solo mail; gimnasio demo/exento no recibe nada; al día (vence en 20 días) no
recibe nada; el destinatario es el staff activo con email, no un alumno ni un staff
inactivo; costo de queries **constante** con 2 y con 6 gimnasios candidatos (comparar dos
tamaños con `CaptureQueriesContext`, no un número fijo).

**Listo cuando:** `makemigrations --check --dry-run` limpio y
`python manage.py test suscripcion` en verde. Un commit.

---

### Task 9: «Cobros con problema» en el panel del superadmin

**Dónde encaja:** los intentos que quedaron en `REVISAR`, `DUPLICADO` o `DEVUELTO` necesitan
una acción humana (devolver plata, revisar un monto). Hoy sólo llegan por mail; esto los
deja a la vista.

**Archivos:** `suscripcion/views.py` (una vista más), `suscripcion/urls.py`,
`templates/suscripcion/cobros_con_problema.html`, `templates/plataforma/inicio.html` (sólo
un link), `suscripcion/tests_vistas.py`.

**Vista** `CobrosConProblemaView` (`plataforma.mixins.SuperadminRequiredMixin`,
`ListView`), ruta `"cobros/"`, nombre `cobros_con_problema`: intentos en
`REVISAR`/`DUPLICADO`/`DEVUELTO` (todos) más los `RECHAZADO` de los últimos 30 días,
`select_related("gimnasio")`, ordenados del más nuevo al más viejo. Columnas: gimnasio (link
a `plataforma:gimnasio_detalle`), estado (badge), plan, período, monto ARS, `payment_id`,
fecha. **Una query**, sin anotaciones nuevas en `facturacion.gimnasios_anotados()`.

**`plataforma/inicio.html`:** un link «Cobros con problema» junto a los accesos que ya
tiene. **Sin contador** (evita una query más en cada carga del monitor).

**Tests:** 403 para un staff; 200 para el superadmin; muestra un `REVISAR` y no un `PAGADO`;
costo de queries constante con 2 y con 8 intentos; `test_toda_vista_declara_su_publico`
sigue pasando.

**Listo cuando:** `python manage.py test suscripcion plataforma` en verde. Un commit.

---

### Task 10: Documentación

**Dónde encaja:** el repo documenta cada decisión no obvia en `CLAUDE.md`, las fases en
`ROADMAP.md` y los problemas y riesgos aceptados en `ISSUES.md`. Insumos: la spec
(`docs/superpowers/specs/2026-09-29-cobro-online-suscripcion-design.md`, en especial su
sección final «Decisiones de ejecución») y el diff de la rama (`git diff main...HEAD`).

**Archivos:** `CLAUDE.md`, `ROADMAP.md`, `ISSUES.md`, `.env.example` (si falta algo).

**`CLAUDE.md`:**
- Sección nueva «Cobro online de la suscripción (`suscripcion/`, 2026-09-29)» después de
  «Panel de plataforma», en el estilo del archivo (decisiones con su porqué, no una lista
  de archivos). Tiene que cubrir: por qué app aparte (dos públicos; `plataforma` es
  superadmin-only); por qué `mercadopago.py` levanta al revés de `cambio.py`; las tres
  respuestas del webhook y por qué 500 a propósito; la idempotencia por `(payment_id,
  estado_mp)`; `acreditar` acepta `VENCIDO`/`CANCELADO`; el monto de MP se valida y nunca se
  escribe (`transaction_amount`, no `total_paid`/`net_received`); las cuatro condiciones
  del descongelamiento y por qué no violan «nunca un cron» («restauración automática, corte
  nunca»); `crear_intento` estricto / `acreditar` permisivo; vencer perezoso; el índice
  único parcial que reusa el link; `DIAS_ANIO = 360`; sin FK a `User`; el tercer nivel del
  middleware; los guards `exigir_anotado`/`gimnasio_anotado`; el `hx-boost="false"`; y qué
  se prueba sólo en el sandbox.
- `CLAUDE.md` § «Principios no negociables» punto 3 y § «Qué NO construir todavía»: matizar
  con fecha que Mercado Pago entró **sólo** para la suscripción plataforma→gimnasio, y que el
  cobro del gimnasio a sus alumnos (`pagos`) sigue igual. Sin esa aclaración el próximo
  lector cree que la puerta quedó abierta para los dos.
- § «Comandos»: `reconciliar_cobros` y `avisar_cobros`.
- § «Deploy»: las tres variables nuevas y los dos workflows nuevos en la lista de trabajos
  programados (con sus secrets de Healthchecks).
- § «Qué se guarda en R2»: nada cambia (no hay archivos nuevos); no lo toques.

**`ROADMAP.md`:** donde lista «Mercado Pago» en «No construir todavía» y en la prioridad
baja, un bloque `> **Actualizado.**` con fecha y motivo, **acotado** — mismo formato exacto
que el bloque del control de ingreso del 2026-09-28 que ya está en el archivo.

**`ISSUES.md`:** una entrada `[2026-09-29]` arriba de la última, en el formato del archivo
(Estado / Impacto / Resolución), con los riesgos aceptados: el descongelamiento automático
sin campo `motivo`; la devolución y el contracargo no recongelan ni borran el pago; se pierde
al que paga en efectivo (`ticket`/`atm` excluidos); el plan anual congela el escalón 12 meses
y el descuento de 2 meses está pendiente de confirmar; sin prueba en el sandbox de MP todavía
(los pasos manuales pendientes: credenciales, webhook con barra final, acreditación a 30
días, secrets de Render y GitHub, Healthchecks); y la facturación AFIP/ARCA fuera de alcance.

**Listo cuando:** los tres archivos actualizados, `ComentariosDeTemplateTests` y la suite
completa en verde (`python manage.py test`). Un commit.
