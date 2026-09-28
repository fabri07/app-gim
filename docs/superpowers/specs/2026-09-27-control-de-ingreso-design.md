# Control de ingreso al gimnasio (QR y lector facial en la puerta)

## Context

Varios establecimientos de entrenamiento le pidieron al dueño del producto que el
alumno pueda entrar al gimnasio con **Face ID** o con un **QR** por un molinete.
El valor es doble:

1. **Comercial**: es un pedido repetido de clientes que ya pagan, o sea retención.
   `ROADMAP.md:79` y `:338` lo listan en Fase 9 ("QR de ingreso, control de
   asistencia") como *prioridad baja, solo con clientes pagos*; ese portón ya está
   abierto y el pedido viene de esos clientes.
2. **De producto**: hoy los tres indicadores de asistencia del panel
   (`tenants/analitica.py:49`, `:406`, `:437`) cuentan `Reserva`, que es la
   **intención** de venir y se *borra* al cancelar. Con esto el dueño ve por
   primera vez quién vino de verdad.

**No hay hardware todavía.** Se construye el lado de la app: un contrato HTTP que
cualquier equipo comercial (Hikvision, ZKTeco, Dahua, Suprema) o un puente
corriendo en la PC del gimnasio pueda consumir, más las pantallas de staff y del
alumno.

Decisiones tomadas con el usuario:

- **La app nunca almacena ni procesa biometría.** El lector guarda la plantilla
  facial en su propia memoria, identifica a la persona y reporta "entró el usuario
  42"; la app guarda solo el id que esa persona tiene en el equipo. Es lo que hace
  todo el hardware comercial, permite soportar cualquier marca, y evita guardar
  **dato sensible** (Ley 25.326). Correr un modelo de embeddings en el único worker
  de gunicorn, con 30 s de timeout, no es viable.
- **La cara se da de alta EN EL EQUIPO** (todos tienen modo enrolamiento) y el
  staff después anota ese id en la ficha del alumno. Es la consecuencia operativa
  de la decisión anterior y va documentada para el instalador.
- **No se construye modo kiosco** (tablet o celular con cámara en la puerta): solo
  la API y el documento de integración. Consecuencia aceptada explícitamente: sin
  hardware la puerta no se ejercita a mano, se ejercita con `curl`. El kiosco queda
  como siguiente paso natural, no construido.
- **La puerta bloquea al deudor reusando `pagos/acceso.py`**, con interruptor propio
  por gimnasio y apagado por default: una sola verdad con el portal.
- **Exigir reserva vigente para abrir es opcional y apagado por default.**
- **El QR es fijo por alumno, regenerable por el staff.** Lo decisivo es que
  **sirve impreso**: una tarjeta o un llavero para el alumno que no tiene
  smartphone. Si se filtra, el staff lo regenera y el viejo muere.
- **Si el gimnasio no le paga la app al dueño del producto, la puerta SIGUE
  ABRIENDO.** Se le corta el panel; el torniquete no. Dejar ochenta personas
  afuera de un edificio no es una palanca de cobro.
- **La sección se llama «Control de ingreso»**, la app `ingreso`, y **ningún modelo
  nuevo lleva "Acceso" en el nombre**: esa palabra ya significa dos cosas en el
  producto (`alumnos:accesos` es usuario+contraseña; `pagos/acceso.py` es el
  bloqueo del portal por deuda) y un tercer significado la vuelve inservible.

## Diseño

### 1. App nueva `ingreso`

Registrada en `config/settings.py::INSTALLED_APPS` (después de las apps de dominio
que lee: `alumnos`, `pagos`, `turnos`) y en `config/urls.py` como
`path("ingreso/", include("ingreso.urls"))`, con `app_name = "ingreso"`.
Templates en `templates/ingreso/`.

**No hace falta ninguna variable de entorno nueva**: nada que cargar en Render,
ningún `_bandera_todo_o_nada`, y por lo tanto nada que pueda quedar apagado en
silencio. `config/tests.py::BlueprintDeclaraLoQueSettingsLeeTests` no se toca.

**No hay migración de datos ni backfill** (ver §3), así que el deploy es un
`migrate` que solo crea tablas y la vuelta atrás es revertir el código.

### 2. Modelos — `ingreso/models.py`

Los cuatro son `TenantOwnedModel`: son datos *del* gimnasio (su staff los ve, le
pertenecen, se los lleva al exportar, se borran al vaciar la demo).

**`ConfiguracionIngreso`** — una fila por gimnasio. Molde `turnos/models.py:31-56`:
`UniqueConstraint(fields=["gimnasio"])` más `obtener_configuracion(gimnasio)` con
`get_or_create` como **única** vía de acceso. Campos: `bloquea_por_deuda` (bool,
default `False`), `exige_reserva` (bool, default `False`), `minutos_antipassback`
(`PositiveSmallIntegerField`, default 2, 0 = apagado).

*Por qué modelo propio y no cuatro campos en `Gimnasio`*: `Gimnasio.version_media`
deriva de `modificado` y versiona la URL del logo, la del fondo y la del ícono de
la PWA. Guardar una preferencia de la puerta le haría volver a bajar todo eso a
cada celular del gimnasio. Es la misma razón por la que las tres escrituras del
panel de plataforma evitan tocar `modificado`.

**`Dispositivo`** — un lector o molinete. `nombre` (unique por gimnasio: dos
"Puerta principal" hacen ilegible el listado de eventos), `tipo`
(`TextChoices`: `QR`/`FACIAL`/`MIXTO`), `token_hash` (`CharField(64)`, `unique`,
`editable=False`, ver §5), `activo`, `ultimo_contacto` (`DateTimeField`,
`null=True`, `editable=False`).

**Sin `DeleteView`**: se desactiva destildando `activo`, y el `PROTECT` de los
eventos lo respalda — mismo criterio que `Ejercicio` y `CategoriaEjercicio`.

**`Credencial`** — lo que identifica al alumno en la puerta.
`alumno` FK **CASCADE** (`related_name="credenciales"`): una credencial no
significa nada sin su alumno, y con `PROTECT` no se podría borrar nunca a un
alumno cargado por error que ya pasó por la puerta. `core/borrado.py` lee
`_meta.related_objects`, así que la lista sola en el aviso de borrado.
Campos: `tipo` (`QR`/`FACIAL`), `valor` (`CharField(64)`; QR:
`secrets.token_urlsafe(32)`; FACIAL: el id que el staff copia del equipo),
`activa`.

Constraints: `UniqueConstraint(gimnasio, tipo, valor)` — el scope es por gimnasio
porque un id facial "42" se va a repetir entre gimnasios — y
`UniqueConstraint(gimnasio, alumno, tipo)`: un QR y un id facial por alumno como
máximo, así que **regenerar es un `UPDATE` del `valor` en la misma fila**.
`clean()` con `validar_gimnasio_de(gimnasio, alumno=alumno)`, igual que
`Reserva.clean`.

*Consecuencia asumida de regenerar in-place*: no queda rastro de que el código
anterior existió. Alcanza para este producto (el evento igual guarda **cuál**
credencial se usó), y la alternativa —una fila nueva marcada inactiva— rompe la
unicidad por alumno y obliga a resolver "cuál es la vigente" en cada lectura.

**`EventoIngreso`** — cada intento, permitido o rechazado.
`dispositivo` FK **PROTECT** (borrar un lector no puede borrar el historial de
asistencia), `alumno` FK **CASCADE** `null=True` (nulo cuando la credencial no se
reconoció), `credencial` FK **SET_NULL** (si el staff *borra* una credencial, el
historial no se va con ella), `resultado` (`TextChoices`: `PERMITIDO`,
`SIN_CREDENCIAL`, `CREDENCIAL_INACTIVA`, `ALUMNO_INACTIVO`, `DEUDA`,
`SIN_RESERVA`, `DUPLICADO`), `momento` (`DateTimeField`, **no** `auto_now_add`:
un lote en diferido trae el suyo), `sentido` (`ENTRADA`/`SALIDA`, default
`ENTRADA`), `en_diferido` (bool), `id_evento_equipo` (`CharField(64)`, `blank`:
el id que el evento tiene **en el equipo**, para la idempotencia del lote — se
llama así y no `id_externo` para no confundirlo con el id facial que guarda
`Credencial.valor`), `detalle` (`CharField(200)`, `blank`).

`Meta`: `ordering = ["-momento", "-id"]`; `Index(gimnasio, -momento, -id)` **con
el `-id`, para que el índice cubra el `ordering`** (mismo criterio que el
`Meta.indexes` de `RutinaAsignada`); `Index(gimnasio, alumno, -momento)` para el
antipassback; `UniqueConstraint(dispositivo, id_evento_equipo,
condition=~Q(id_evento_equipo=""))`, parcial para que los eventos sin id no
choquen entre sí.

**El valor leído NO se guarda en el evento.** De una credencial desconocida se
guardan los últimos 6 caracteres en `detalle`: alcanza para diagnosticar "esta
tarjeta no anda" sin duplicar miles de veces la llave de la puerta en una tabla
que además se exporta. Para las FACIAL, cuyos ids son cortos, eso suele ser el id
completo, que no es secreto.

*Consecuencia asumida de `alumno` CASCADE*: borrar un alumno se lleva su historial
de ingresos y los números históricos del panel bajan. Es exactamente cómo se
comporta `Reserva` hoy, y `core/borrado.py` lo lista en el aviso antes de borrar.
`Cuota` es `PROTECT` porque es el registro de lo que se facturó; la asistencia no
es plata.

#### Crecimiento, y una consecuencia que no es teórica

300 alumnos × ~20 visitas/mes ≈ 72.000 filas/año, más rechazos y dobles lecturas:
mismo orden que `plataforma.ActividadDiaria` (~110.000/año). Los dos índices
cubren las dos consultas y la ventana del listado es corta, así que el panel no
sufre. **Lo que sí se rompe es el exportador**:
`tenants/exportacion.py::MAX_FILAS_WEB` es 60.000 y por encima de eso la descarga
no se genera. Es la primera feature del proyecto que empuja la exportación contra
su techo, y el síntoma le aparecería a un cliente pago justo cuando se quiere ir.
Por eso la purga (§11) **se construye ahora** y no queda como "la salida si alguna
vez molesta".

### 3. Cómo nace el QR de cada alumno

Nadie lo tiene al desplegar, y **no hay backfill a propósito**: una migración de
datos que le cree una credencial a cada alumno de cada gimnasio le estaría
generando llaves de puerta a todos los gimnasios que no pidieron la feature,
incluida la cuenta de demostración.

- **Por alumno**: botón "Generar código" en la ficha.
- **En bloque**: acción "Generar códigos para los alumnos activos" en el listado,
  con **un solo `bulk_create`** (el gimnasio con 200 alumnos no puede clickear 200
  veces). Idempotente: saltea a los que ya tienen.
- El portal del alumno sin credencial muestra un estado vacío ("todavía no tenés
  código, pedíselo al gimnasio"), nunca un error.

### 4. El árbol de decisión — `ingreso/decision.py` (nuevo)

Molde `pagos/acceso.py`: un módulo, resultado en `dataclass(frozen=True)`, y un
docstring que enumera las decisiones que no hay que deshacer.

    @dataclass(frozen=True)
    class Decision:
        permitido: bool
        resultado: str          # EventoIngreso.Resultado
        mensaje: str            # castellano, para la pantalla del lector
        alumno: Alumno | None
        credencial: Credencial | None

    def contexto_de(dispositivo, lecturas) -> Contexto   # la ÚNICA que toca la base
    def decidir(lectura, contexto) -> Decision           # pura, sobre lo ya traído

`contexto` es obligatorio y no tiene default: la vista llama siempre a
`contexto_de` primero, incluso para una sola lectura. Un camino, sin ramas — un
`contexto=None` "que se lo arma solo" es justo la puerta por la que volvería a
entrar una query por evento.

Orden de las ramas, y **el orden importa**:

1. Credencial no encontrada en el gimnasio del dispositivo → `SIN_CREDENCIAL`.
2. `not credencial.activa` → `CREDENCIAL_INACTIVA`.
3. `alumno.estado != ACTIVO` → `ALUMNO_INACTIVO`.
4. `config.bloquea_por_deuda` y el alumno está en el conjunto de bloqueados →
   `DEUDA` (el mensaje usa `dias_de_atraso`).
5. `config.exige_reserva` y no hay reserva vigente → `SIN_RESERVA`.
6. Antipassback → `DUPLICADO`.
7. `PERMITIDO`.

El antipassback va **último** a propósito: significa "podés entrar pero ya
entraste". Adelantarlo haría que el segundo scan de un deudor diga "ya pasaste" en
vez de "tenés una cuota vencida". Se mide contra el `momento` **de esa lectura**
(no contra `now()`, que daría cualquier cosa en un lote en diferido) y **solo
contra eventos del mismo `sentido`**, o una salida bloquearía el reingreso.

**"El gimnasio le debe a la plataforma" no figura en el árbol, deliberadamente**:
este módulo no conoce `estado_cuenta` y no tiene por qué. La invariante se fija un
nivel más arriba (§6), que es donde el bloqueo podría colarse.

**Trampa heredada que hay que mostrar en pantalla**: `pagos/acceso.py::_umbral`
hace *fail-open* si `dias_tolerancia_pago` o `fecha_activacion_bloqueo` están
vacíos. Un gimnasio que prenda `bloquea_por_deuda` **sin** tener la tolerancia
cargada obtiene un interruptor que no hace nada, sin ningún síntoma — la misma
familia de bug que el proyecto ya pagó con el Web Push apagado. El formulario de
configuración lo avisa (§9).

#### El costo en queries no puede depender de la cantidad de eventos

Es la regla más dura de `CLAUDE.md` y la que un lote de 100 eventos violaría sin
pensarlo (5 queries por evento = 500 queries en un request, justo el patrón que ya
causó un 502 con el importador). Por eso `decidir` no toca la base:
**`contexto_de()` resuelve todo el lote de una vez** con un número fijo de
queries, traiga 1 lectura o 100 — las credenciales con un `in_bulk` por valor
(`select_related("alumno")`), la configuración, el conjunto de alumnos bloqueados
con el **`Exists(OuterRef)` de `contar_bloqueados`** (`pagos/acceso.py:129-141`)
en vez de `bloqueo_de` por fila, las reservas vigentes del rango en una query, y
los últimos eventos por alumno para el antipassback en otra.

#### `turnos/services.py::reserva_vigente` (nueva, en app existente)

`reserva_vigente(gimnasio, alumno, ahora=None)` vive ahí porque es donde vive el
dominio de reservas, `_ahora_local()` y `duracion_minutos`. `Reserva` no guarda
`hora_fin`: se deriva con `ConfiguracionTurnos.duracion_minutos`, como ya hace
`turnos/services.py:364-365`. La ventana cruza medianoche, y el precedente que lo
resuelve con dos `Q` es
`notificaciones/management/commands/enviar_recordatorios.py:109-127`. Admite una
tolerancia `TOLERANCIA_INGRESO = 15 min` antes del inicio: el que llega unos
minutos antes no puede quedar afuera. Una query, más su hermana en bloque para el
lote.

### 5. Contrato HTTP — `ingreso/views.py`, `ingreso/urls.py`

Base **`/ingreso/v1/`**. La versión va en la ruta desde el día 1: el instalador la
deja cableada en un equipo que no podemos actualizar.

**Autenticación**: `Authorization: Bearer <token>`, donde el token son 32 bytes de
`secrets.token_urlsafe` (mismo generador que `solicitudes/services.py:50`) y la
base guarda **solo su `sha256`** en `Dispositivo.token_hash`. Se verifica buscando
por ese hash: una query indexada, tiempo constante, y un volcado de la base no
contiene ninguna llave usable.

*Por qué así y no de las otras dos formas obvias.* En claro con `unique` (lo que
hace `solicitudes.SolicitudAccesoToken`) deja la llave de la puerta legible en la
base y en cualquier backup, y ese precedente es un token de un solo uso con
vencimiento, no una llave de larga vida. Con `make_password`/`check_password`
(PBKDF2) se pagarían ~100 ms de CPU **bloqueante** por request en un gunicorn de
un solo worker, y entre los scans y el poll del padrón son miles de requests por
día. Con un token de 256 bits de entropía un `sha256` pelado no es atacable por
diccionario, así que la derivación lenta no compra nada. `EncryptedTextField` no
sirve: Fernet no es determinístico, no se puede filtrar por él.
**Sin "pimienta" con `SECRET_KEY`** —aunque `solicitudes/antispam.py:75-79` la use
para otra cosa— porque rotar `SECRET_KEY` dejaría sin funcionar, en silencio y de
golpe, la puerta de todos los gimnasios.

**Status**: **401** sin token o con token que no matchea, sin distinguir "no
existe" de "está mal" (mismo criterio anti-enumeración del resto del proyecto).
**403** solo cuando el token **es válido** y el dispositivo está desactivado, así
que no le revela nada a quien no tiene la llave y sí le dice al instalador lo que
necesita: 401 = revisá el token, 403 = el gimnasio apagó este lector.

#### `POST /ingreso/v1/eventos/`

    {"eventos": [{"tipo": "qr", "valor": "…", "sentido": "entrada",
                  "momento": "2026-09-27T19:03:11-03:00",
                  "id_evento_equipo": "8821", "abierto_por_el_equipo": true}]}

    200 {"resultados": [{"id_evento_equipo": "8821", "permitido": true,
         "resultado": "permitido", "mensaje": "Hola, Juan. Pasá.",
         "alumno": "Juan P."}]}

- **El modo lo define `abierto_por_el_equipo`, no el largo de la lista**: ausente
  es online (el equipo espera el veredicto para abrir), presente es diferido (el
  equipo ya abrió con su padrón cacheado). Un request online naturalmente trae un
  solo evento, pero eso es consecuencia, no la regla.
- En diferido se registra igual qué habría opinado la app (`en_diferido=True`),
  así el reporte no miente: "entró, y tenía deuda".
- `momento` ausente es ahora; presente es lo que dice el equipo, y es lo que se
  usa para el antipassback y para la reserva vigente.
- **Idempotencia**: con `id_evento_equipo` se busca primero por `(dispositivo,
  id_evento_equipo)` y si existe se devuelve el veredicto guardado sin decidir de
  nuevo. La carrera del reintento simultáneo la cubre un `try/except
  IntegrityError` dentro de un `atomic()` (precedente: el doble submit de
  `PagoPlataforma`).
- `MAX_EVENTOS_POR_LOTE = 100`, por encima 400. Con `contexto_de` el costo son
  unas pocas queries fijas más **un solo `bulk_create`** con
  `ignore_conflicts=True` (mismo patrón que `ActividadDiaria`), no 500 queries.
- Body inválido → 400, con el `except (KeyError, ValueError, TypeError,
  json.JSONDecodeError)` de `notificaciones/views.py:142`.

#### `GET /ingreso/v1/padron/`

    {"version": "…", "reglas_no_incluidas": ["reserva", "antipassback"],
     "alumnos": [{"id": 12, "nombre": "Juan P.", "habilitado": true,
                  "qr": "…", "id_facial": "42"}]}

Con `ETag` igual a `version` y `304` ante `If-None-Match`. Es lo que hace
funcionar al lector **offline** y lo que el puente usa para dar de alta y de baja
usuarios en el equipo facial. `habilitado` es la decisión de la puerta **menos**
antipassback y reserva, que son momentáneos y no se pueden precomputar.

**Una promesa que la app no puede cumplir, y que va escrita en la pantalla de
configuración**: mientras el lector está offline no hay forma de exigir reserva
vigente ni de aplicar antipassback, y un equipo con el padrón cacheado va a dejar
entrar a alguien que se atrasó con la cuota *después* del último poll. El JSON lo
declara (`reglas_no_incluidas`), el documento de integración lo explica, y el
texto va **al lado de los dos checkboxes**, no en una nota al pie.

**Requisito de performance**: `habilitado` se anota con el `Exists(OuterRef)` de
`contar_bloqueados`, **nunca** llamando a `bloqueo_de` por fila.

**El riesgo técnico más grande de la feature, y el único que no se puede medir sin
hardware**: Neon suspende el compute por inactividad (scale-to-zero). El primer
scan después de un rato de calma paga ese cold start, y un molinete tiene un
timeout de un par de segundos. El padrón offline está en `v1` por esto, no por
completitud: es la mitigación, no un extra.

Este endpoint entrega llaves: el token del dispositivo es tan sensible como el
llavero del gimnasio, y el documento de integración lo dice.

**Heartbeat**: no hay endpoint aparte. Cualquier request autenticado actualiza
`ultimo_contacto` con `QuerySet.update()` (para no tocar `modificado`), y el poll
del padrón hace de latido; el panel muestra "sin contacto desde …". Es **un
`UPDATE` por request sobre la misma fila**: fila caliente, sin contención real más
allá de sus propios scans, que el molinete ya serializa.

### 6. `csrf_exempt` y `APPS_SIN_BLOQUEO`

**`csrf_exempt` (el primero del proyecto)**, sobre las dos vistas de la API. CSRF
protege una sesión de navegador de ser montada por otro sitio; estas dos vistas no
tienen sesión ni cookie y autentican con un header que un form cross-site no puede
setear. Dejarlo prendido las haría imposibles de llamar desde cualquier cliente
HTTP. **Ignoran `request.user` por completo**, y hay un test que lo fija: una
sesión de staff logueada no alcanza para usarlas sin token.

**`ingreso` NO se agrega a `APPS_SIN_BLOQUEO`** de `plataforma/middleware.py`, y
eso es lo que hace que las dos mitades se comporten distinto, que es justo lo que
se quiere:

- Las **pantallas de staff** quedan alcanzadas por el bloqueo: con la cuenta
  suspendida el dueño ve el cartel, igual que en el resto del panel.
- La **API** no la toca el middleware igual, porque su allowlist corre antes de
  resolver el usuario y un request sin cookie de sesión corta en anónimo
  (`plataforma/middleware.py:235-237`). La puerta sigue abriendo sin pedirle
  permiso a nada.

Dos tests lo fijan por separado: con un gimnasio `SUSPENDIDA`, la API responde el
veredicto normal **y** la pantalla de staff muestra el cartel. Sin el primero,
alguien "ordena" el middleware y le corta la puerta a un gimnasio.

### 7. Rate limiting, honesto sobre sus límites

Camino de fallo (sin token o token inválido): límite por IP con el patrón de
`solicitudes/antispam.py` y 429 al excederlo. **El cache es `LocMemCache`, por
proceso y volátil** (no hay setting `CACHES` en `config/`), así que es un badén y
no una garantía, y encima deja de funcionar *en silencio* el día que alguien
arranque gunicorn con `--workers 2`. La defensa real es Cloudflare adelante
(`ip_de()` ya lee `CF-Connecting-IP` y descarta `X-Forwarded-For` a propósito), y
el respaldo durable es un tope **diario por dispositivo contado en la base**, que
sobrevive a los reinicios y a los workers.

En el camino autenticado no se pone 429: un gimnasio a las 19:00 es legítimamente
a ráfagas. El tope es el de eventos por lote, y contra un token robado la
mitigación es rotarlo.

### 8. QR: `segno` (dependencia nueva) y tarjetas imprimibles

`segno` es Python puro y **sin dependencias binarias** (importa en Render), emite
SVG inline y PNG en bytes. `qrcode` necesitaría Pillow y solo da raster; una
librería JS por CDN no sirve para el PDF.

- `ingreso/qr.py::svg_de(valor)` → string SVG. Django-free, `SimpleTestCase`. En
  el portal va inline: escala perfecto, sin request extra, sin R2 y sin problema
  de caché.
- `ingreso/tarjetas.py::generar_pdf_credenciales(gimnasio, alumnos)` → bytes, con
  fpdf2, siguiendo el reparto de `rutinas/pdf.py` ↔ `RutinaAsignadaPdfView`
  (Django-free; la vista es la única capa que conoce `HttpResponse`). El proyecto
  **no tiene ningún estilo de impresión**, así que la tarjeta imprimible es un PDF
  y no CSS de print.

### 9. Pantallas (el nav se queda en 8 ítems)

- **Tarjeta en el dashboard** (`templates/tenants/home.html` +
  `tenants/views.py::_metricas_dashboard`): "Control de ingreso — 34 ingresos hoy ·
  2 rechazos", con link al listado; sin ningún dispositivo cargado invita a
  configurarlo. Es el único lugar donde el dueño descubre la feature (mismo
  criterio que la tarjeta de plan por vencer). Suma **dos** queries y ni una más:
  un `aggregate` con `Count` condicional sobre los eventos de hoy y un `count()`
  de dispositivos activos. El "hoy" va con `timezone.localdate()`.
- `ingreso:listado` — ingresos con filtros (fecha, alumno, resultado),
  `select_related("alumno", "dispositivo")`: es justo donde vive un N+1.
- `ingreso:dispositivos` / `dispositivo_crear` / `dispositivo_editar` — el token se
  muestra **una sola vez**, en un 200 que no redirige, con `never_cache` sobre el
  `dispatch` entero (precedente `plataforma/views.py:429`; `messages` no sirve
  porque se serializa en la sesión, que vive en la base).
- `ingreso:configuracion` — `UpdateView` sobre la fila única, molde
  `ConfiguracionTurnosView` (`turnos/views.py:112-148`). Dos avisos **en
  pantalla**: si `bloquea_por_deuda` está prendido con `dias_tolerancia_pago`
  vacío (el interruptor muerto, §4), y qué reglas no se aplican con el lector
  offline (§5).
- Ficha del alumno (`templates/alumnos/alumno_detail.html`): `.tarjeta` nueva
  **"Credencial de ingreso"** — QR, "Generar código"/"Regenerar", campo del id
  facial, link a imprimir. Ojo: esa ficha ya tiene un `<h2>Acceso</h2>` que
  significa usuario+contraseña, así que el título nuevo tiene que ser distinto.
- Portal del alumno: "Tu código para entrar", con el QR, y el estado vacío de §3.
  **Se muestra igual estando bloqueado por deuda**: lo que cambia es el mensaje, la
  puerta decide.
- Entrada a la sección: la tarjeta del dashboard, más el link de configuración
  desde "Mi gimnasio".

Todo campo renderizado sin `form.as_p` va con el partial `partials/campo_form.html`,
porque `core.tests.ErroresDeFormularioVisiblesTests` barre todos los templates y
falla si un campo no muestra sus errores.

### 10. Los tres lugares

| Modelo | `vaciar_gimnasio` | `_ensuciar` | Exportador |
|---|---|---|---|
| `EventoIngreso` | 1º | sí | `HOJAS` — el historial de asistencia es lo más valioso que el gimnasio se lleva |
| `Credencial` | 2º | sí | `HOJAS` **sin la columna `valor`** |
| `Dispositivo` | 3º | sí | `HOJAS` **sin `token_hash`** |
| `ConfiguracionIngreso` | 4º | sí | `HOJAS` |

El orden lo impone el `PROTECT` de `EventoIngreso.dispositivo` y **no es
reordenable**. Los dos tests de cobertura por introspección se actualizan juntos:
solo sirven de a pares.

Los dos modelos con una columna sensible se tratan **igual** (la hoja va sin esa
columna y la `descripcion` dice por qué), en vez de excluir un modelo entero por
tener un secreto adentro: `Hoja.columnas` es una lista explícita, así que omitir
una columna no cuesta nada, y `nombre`/`tipo`/`activo`/`ultimo_contacto` de un
dispositivo son datos legítimos del gimnasio. El molde de la `descripcion` es la
hoja de alumnos, que ya aclara que las contraseñas no se exportan.

### 11. Purga — `manage.py purgar_ingresos`

`--gimnasio <slug>` obligatorio (molde
`tenants/management/commands/exportar_gimnasio.py`), `--meses N`, `--dry-run`,
borrando por tandas de pk. Existe por el techo del exportador (§2), no "por si
alguna vez molesta".

### 12. Documento de integración

`docs/integracion-control-de-ingreso.md`, molde `docs/runbook-respaldos.md`: el
contrato completo, cómo se saca el token, el flujo de enrolamiento facial (dar de
alta la cara en el equipo y copiar el id a la ficha), qué hace el puente, qué
reglas **no** se aplican offline, y que el token es tan sensible como el llavero.

Script de ejemplo del puente en `scripts/`, **solo stdlib**: tiene que correr en
la PC de un gimnasio sin `pip install`.

**Test de alambre trampa**: un `SimpleTestCase` que extrae las rutas
`/ingreso/v1/…` del documento y del script con un regex y exige que cada una
resuelva con `resolve()`, así el documento no puede quedar desfasado del código en
silencio. Mismo criterio que `BlueprintDeclaraLoQueSettingsLeeTests`, sin
dependencia nueva.

### 13. Verificación

- La suite completa en verde.
- **`curl` contra `runserver`**, que es el end-to-end real de la puerta: crear
  gimnasio, alumno y dispositivo, y ejercitar 401 → veredicto online → lote
  diferido → idempotencia (postear el mismo lote dos veces) → padrón con y sin
  `If-None-Match`. Va como script en `scripts/` para que sea repetible, no como una
  sesión manual irrepetible.
- **Navegador**: las cuatro pantallas de staff, la tarjeta del dashboard, la ficha
  del alumno y el QR del portal. El `/code-review` no reemplaza esto.
- **La tarjeta impresa se decodifica con la cámara del celular** y se confirma que
  el string leído es idéntico a `Credencial.valor`. Es la única parte física
  verificable hoy, y si el QR sale ilegible impreso se cae toda la decisión del
  "QR fijo para el que no tiene smartphone".
- **Explícitamente sin verificar hasta que exista hardware**: que un lector real
  hable este dialecto, que el flujo de enrolamiento facial coincida con la UI de un
  fabricante concreto, la latencia de un molinete de verdad, y si el cold start de
  Neon entra en su timeout. Queda anotado en `ISSUES.md` como pendiente abierto
  hasta el primer cliente con equipo.

## Riesgos aceptados a propósito

1. **`sha256` en vez de un hasher lento** para el token del dispositivo. El
   argumento es sólido (256 bits de `secrets`, el key-stretching no compra nada y
   cuesta ~100 ms del único worker por request), pero es la decisión que más merece
   una segunda mirada. Si alguna vez se revierte a PBKDF2, el camino de "token no
   encontrado" **necesita un hash dummy** o la diferencia de tiempo delata qué
   dispositivos existen.
2. **El primer `csrf_exempt` del proyecto**, acotado a las dos vistas de la API,
   con el test de "una sesión de staff no alcanza" como defensa real.
3. **No exportar el `valor` del QR**: un gimnasio que migra tiene que reemitir las
   tarjetas. La alternativa es que un CSV en la carpeta de Descargas sea el llavero
   del gimnasio. Revisable.
4. **Offline no puede exigir reserva ni antipassback**, y el padrón cacheado deja
   entrar a quien se atrasó después del último poll.
5. **Desactivar un dispositivo antes de que suba su backlog offline pierde ese
   backlog** (el 403 rechaza el lote entero). Es raro y el dato perdido es
   histórico, no operativo.
6. **Regenerar un QR no deja rastro del código anterior** (es un `UPDATE`
   in-place).
7. **Borrar un alumno se lleva su historial de ingresos** y baja los números
   históricos del panel, igual que ya pasa con `Reserva`.
8. **`segno` es una dependencia nueva**: Python puro, sin wheel binario, pero una
   más.
9. **No se notifica al staff un intento rechazado.** Hay un caso real ahí (alguien
   insistiendo en la puerta con una tarjeta revocada), pero el proyecto tiene 7
   eventos de push y la regla de no sumar el octavo sin que alguien lo pida.

## Fuera de alcance

- **Modo kiosco** (tablet o celular con cámara en la puerta). Es el siguiente paso
  natural y reusa el mismo endpoint; no se construye ahora por decisión del dueño
  del producto.
- **QR rotativo**: descartado a favor del fijo, que se puede imprimir.
- **Reconocimiento facial del lado de la app**: descartado, ver Context.
- **"Quién está adentro ahora"**: `sentido` viaja en el contrato desde el día 1 para
  no romperlo después, pero la feature no se construye.
- **Integración con un fabricante puntual** (ISAPI de Hikvision, SDK de ZKTeco):
  eso vive en el puente, afuera de este repo, y sin hardware no se puede verificar.
