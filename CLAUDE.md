# CLAUDE.md

Guía para Claude Code (y para cualquier humano) trabajando en este repo.

## Qué es esto

SaaS simple, multi-tenant y white-label para gimnasios y entrenadores locales
(Argentina). Reemplaza Excel + papel + WhatsApp: alumnos, planes, rutinas con
video, pagos mensuales, comprobantes, novedades, personalización de
logo/nombre/colores por gimnasio.

El plan completo (fases, modelo de datos, criterios de salida, timeline
comercial) vive en **`ROADMAP.md`** — léelo antes de tocar código nuevo. Este
archivo es la foto rápida de "cómo está armado hoy", no reemplaza al roadmap.

**Fase actual: código de Fases 0-6 completo en `main`, deploy real
pendiente.** El código de producción está listo (Postgres, WhiteNoise, R2,
`render.yaml`) pero falta la parte que no puedo hacer yo: crear el bucket de
R2 en Cloudflare, aplicar el Blueprint en el dashboard de Render, y cargar
las env vars `GOOGLE_*` (cuentas/pagos de terceros). Ver "Deploy (Fase 5)"
más abajo para el estado exacto y los pasos manuales que quedan. Fases 0-4
(esqueleto, modelos, vistas de staff, portal del alumno, UX/white-label)
completas — un dueño puede usar el sistema de punta a punta desde el panel
web sin tocar `/admin/`. Además del scope original del ROADMAP ya están
mergeadas: agenda de turnos/reservas con cupos, read-receipts de novedades,
medios de cobro configurables, una integración opcional con Google
Calendar por alumno (ver "Turnos, reservas y Google Calendar" más abajo), y
un importador de planes/ejercicios desde Excel (ver "Importador de Excel
(Proyecto 2)" más abajo) — el ROADMAP.md no las documenta todavía como
fases propias, viven en `ISSUES.md` y en los mensajes de commit ("Fase 6,
Task N", "Parte A/B/C", "Proyecto 2, Task N").

**Nota:** el acceso del alumno NO es magic-link como decía la primera versión
del ROADMAP — el dueño del producto pidió que el staff asigne usuario y
contraseña directamente. Ver `ISSUES.md` (2026-07-01) y `ROADMAP.md` Fase 3,
ya actualizados.

## Principios no negociables (resumen — el detalle está en ROADMAP.md)

1. Una sola app, múltiples gimnasios. Nunca se copia el repo por gimnasio.
2. La app es la fuente de verdad; nada de sync en vivo con Excel.
3. Pagos simples: sin Mercado Pago ni integraciones financieras en el MVP.
   Los pendientes del mes se autogeneran por cron; el dueño confirma.
4. Aislamiento por gimnasio desde el modelo de datos: ningún registro
   operativo existe sin `gimnasio`.
5. Archivos de usuario (comprobantes, logos) van a Cloudflare R2, nunca al
   filesystem de Render (es efímero).
6. Sin subdominios por gimnasio en el MVP; el tenant se resuelve por el
   usuario logueado.
7. Primero se cobra, después se sofistica — no construir features que no
   ayuden a conseguir/retener los primeros tres gimnasios pagos.

## Stack

- Django 5.2 (templates + vistas basadas en clases, sin DRF).
- SQLite en dev; Postgres en producción vía `DATABASE_URL` (Render la inyecta).
- Tailwind CSS (build local con Node, ver "UI y white-label" abajo) + HTMX
  (`hx-boost`) + Alpine.js (CDN, solo el toggle de nav mobile). **Nada de
  React/Next** en el MVP.
- Deploy: Render (`render.yaml`, ver "Deploy (Fase 5)") + Cloudflare R2 para
  media (comprobantes, logos) vía `django-storages`.

## Arquitectura multi-tenant

Estrategia: **base de datos compartida + aislamiento por fila** vía FK
`gimnasio` en cada modelo operativo (no schema-per-tenant ni db-per-tenant —
KISS/YAGNI para esta etapa). El patrón viene de `~/gestor-pedidos` (ver
`REUSO.md`), no de Vektor (que es FastAPI, sin este patrón).

- `core/models.py` — `TimeStampedModel`, `TenantQuerySet.for_gimnasio()`,
  `TenantOwnedModel` (abstracto; todo modelo operativo hereda de acá y
  obtiene el FK `gimnasio` con `on_delete=PROTECT`).
- `core/mixins.py` — `TenantScopedMixin`: en toda vista basada en clases,
  resuelve `request.user.perfil.gimnasio`, filtra el queryset, y **stampea
  `gimnasio` del lado del servidor** al guardar (nunca viene del cliente).
- `core/forms.py` — `TenantScopedModelForm`: acota automáticamente cualquier
  FK a otro `TenantOwnedModel` dentro del mismo gimnasio (cierra el hueco de
  FK-injection — sin esto, un form con FK a otra entidad tenant-owned
  permitiría enviar el id de un registro de otro gimnasio).
- `tenants/models.py` — `Gimnasio` (el tenant) y `Perfil` (vínculo 1:1
  User↔Gimnasio + `rol`: `staff` o `alumno`).

**Regla al agregar cualquier modelo de dominio (Fase 1+):** heredar de
`TenantOwnedModel`, y si tiene FK a otro modelo tenant-owned, el form debe
heredar de `TenantScopedModelForm`. Las vistas de gestión van con
`TenantScopedMixin`.

## Apps de dominio

- **`alumnos`** — `Alumno(TenantOwnedModel)`.
- **`ejercicios`** — `Ejercicio(TenantOwnedModel)`, biblioteca por gimnasio
  (no global; ver docstring del módulo).
- **`rutinas`** — `RutinaPlantilla`/`RutinaPlantillaItem` (editable) y
  `RutinaAsignada`/`RutinaAsignadaItem` (snapshot congelado). La copia se
  hace con `RutinaAsignada.crear_desde_plantilla(...)` y
  `RutinaPlantilla.duplicar()` — ambas transaccionales. Los modelos "Item" NO
  son `TenantOwnedModel` (se acceden vía su padre, que ya está scopeado).
- **`pagos`** — `PagoMensual(TenantOwnedModel)` y `MedioCobro(TenantOwnedModel)`
  (alias/CBU/lo que el gimnasio muestra al alumno para pagar, editable por
  staff). `pagos/models.py` expone `generar_pagos_pendientes(mes, anio)` y
  `marcar_vencidos(mes, anio)`; `python manage.py generar_pagos` corre ambas
  para el mes actual — es el comando que Fase 5 programa como Render Cron Job.
- **`novedades`** — `Novedad(TenantOwnedModel)` con `NovedadQuerySet.visibles()`
  (activa + publicada + no vencida), y `NovedadLeida` (read-receipt por
  alumno; no es `TenantOwnedModel`, se scopea vía su FK a `Novedad`/`Alumno`
  que ya está acotada) para el badge "Nueva" del portal y el conteo de
  lecturas que ve el staff.
- **`turnos`** y **`calendario`** — agenda de reservas con cupos y su
  integración opcional con Google Calendar; ver sección propia abajo.
- **`importaciones`** — importador de planes de entrenamiento y biblioteca de
  ejercicios desde Excel; ver "Importador de Excel (Proyecto 2)" abajo.

## Vistas de staff (Fase 2)

Cada app de dominio tiene `forms.py`/`views.py`/`urls.py` (namespace propio,
p.ej. `alumnos:listado`, `rutinas:asignar`) y templates bajo
`templates/<app>/`. Todas las vistas de gestión combinan
`tenants.mixins.StaffRequiredMixin` (autorización por rol — solo `staff`,
403 para `alumno` o sin `Perfil`) con `core.mixins.TenantScopedMixin`
(aislamiento por tenant), `StaffRequiredMixin` primero en el MRO.

- **Nav**: `templates/base.html` muestra el menú de secciones solo si
  `user.perfil.rol == "staff"`.
- **Dashboard**: `tenants.views.HomeView` (ruta `home`) — bifurca por
  `perfil.rol`. Para `staff`: métricas de Fase 2 §1 (alumnos activos, alumnos
  con pago pendiente, pagos del mes, rutinas activas, últimas novedades).
  Para `alumno`: el portal de Fase 3 (su rutina activa, su cuota del mes,
  últimas novedades) — ver más abajo.
- **`RutinaPlantillaItem`/`RutinaAsignadaItem`** no son `TenantOwnedModel`
  (no tienen `gimnasio` propio): sus vistas resuelven el aislamiento
  buscando primero el padre vía `for_gimnasio()` antes de tocar el item — ver
  `rutinas/views.py` (`ItemPlantillaMixin`).
- `PagoMensual` sigue sin vista de "crear" — el staff solo confirma pagos ya
  autogenerados (principio no negociable §3).

## Portal del alumno y acceso (Fase 3)

- **Acceso**: `Alumno.perfil` (`OneToOneField` a `tenants.Perfil`, nullable)
  vincula un alumno con su login. El staff lo crea/resetea desde la ficha del
  alumno (`alumnos:acceso_crear` / `alumnos:acceso_cambiar_password`,
  `alumnos/views.py::CrearAccesoView`/`CambiarPasswordAlumnoView`) — un form
  plano (no `ModelForm`), con la contraseña en texto plano en pantalla
  (`help_text` lo explica: es la única vez que se puede leer, el staff la
  tiene que copiar para pasársela al alumno). `username` es único GLOBAL
  (`auth.User`, sin namespacing por gimnasio) — el form lo valida y sugiere
  uno libre (mismo patrón que `RegisterView._slug_disponible`).
- **`fecha_activacion`**: se registra en el PRIMER login exitoso del alumno,
  no al crear el acceso — vía la señal `user_logged_in` en
  `alumnos/signals.py`, conectada en `AlumnosConfig.ready()`. Mide adopción
  real, no alta administrativa.
- **Portal**: `HomeView._portal_alumno` (mismo patrón de import tardío que
  `_metricas_dashboard`) resuelve `perfil.alumno` y agrega su rutina activa
  (con items), la cuota del mes actual y las novedades visibles al contexto;
  la plantilla renderiza todo en una sola pantalla mobile-first (ROADMAP
  Fase 3: "entiende su rutina sin explicación adicional"). Si el `Perfil` de
  rol `alumno` todavía no está vinculado a un `Alumno`, se muestra un estado
  vacío, no un error 500.

## Turnos, reservas y Google Calendar (más allá del ROADMAP original)

Agregado después de Fase 4, fuera del scope que describe `ROADMAP.md` (que
llama "Fase 6" al primer piloto pago, no a esto) — el detalle real vive en
`ISSUES.md` y en los commits ("Fase 6, Task N" para turnos; "Parte A/B/C"
para la migración de reservas desencajadas y Google Calendar).

- **`turnos`** (`turnos/models.py`, `turnos/services.py`): agenda de clases
  con cupo. `ConfiguracionTurnos` (duración + cupo default, una fila por
  gimnasio) + `HorarioAtencion` (franjas por día de semana) + `CupoExcepcion`
  (pisa el cupo un día/horario puntual, incluso a 0) generan la grilla;
  `Reserva` es lo que un alumno ocupa. Toda la lógica de negocio (crear
  reserva, cancelar, calcular la grilla semanal) vive en `services.py`, no en
  las vistas ni en los modelos — `crear_reserva()` toma
  `select_for_update()` sobre `ConfiguracionTurnos` para serializar altas
  concurrentes contra el cupo.
- **Reservas desencajadas**: cuando el staff cambia horarios/duración,
  reservas existentes pueden quedar fuera de cualquier franja vigente.
  `reconciliar_reservas_desencajadas()` las reubica en la franja vigente más
  cercana (o las cancela si no hay ninguna) y llama a
  `_generar_novedades_personales()` para avisarle a cada alumno afectado vía
  una `Novedad` dirigida a él. **Riesgo aceptado a propósito**: esta función
  NO toma lock (a diferencia de `crear_reserva()`) porque solo corre cuando
  el staff edita su propia grilla — ver la entrada `[2026-07-06]` en
  `ISSUES.md` para el razonamiento y cómo cerrarlo si hiciera falta.
- **`calendario`** (`calendario/models.py`, `calendario/services.py`):
  integración **opcional** con Google Calendar, por alumno (no por
  gimnasio). `GOOGLE_CALENDAR_ENABLED` en `settings.py` se activa solo si las
  4 env vars `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI` +
  `GOOGLE_TOKEN_ENCRYPTION_KEY` están seteadas (todas o ninguna — falla al
  arrancar si están parciales). Scope usado:
  `calendar.app.created` (NO da acceso al calendario principal del alumno,
  solo a un calendario secundario que la app crea, "Turnos de {gimnasio}").
  Tokens (`refresh_token`/`access_token`) se guardan cifrados con
  `EncryptedTextField` (`calendario/fields.py`, usa `cryptography` +
  `GOOGLE_TOKEN_ENCRYPTION_KEY`), nunca en texto plano. La sync
  reserva→evento es **síncrona** vía `transaction.on_commit` (no hay
  Redis/Celery/django-q en este proyecto).
  - Ninguno de los dos modelos de `calendario` es `TenantOwnedModel`: se
    scopean a través de su FK (`alumno`/`reserva`), que ya está acotada por
    gimnasio — mismo precedente que `NovedadLeida`.
  - **Gotcha de PKCE**: `google-auth-oauthlib` activa PKCE por defecto;
    `build_authorization_url()` tiene que devolver y persistir el
    `code_verifier` en la sesión (no solo `state`) para que el callback
    pueda reconstruir el mismo `Flow` — si armás un `Flow` nuevo en el
    callback sin pasarle el verifier original, Google devuelve
    `invalid_grant: Missing code verifier`. No lo detectan los tests que
    mockean `build_authorization_url`/`intercambiar_code`: hace falta un
    test que ejercite connect→sesión→callback sin mockear ninguna de las
    dos puntas. Ver `ISSUES.md` `[2026-07-08]`.
  - **Gotcha de hx-boost**: cualquier link que dispare un redirect
    cross-origin (como "Conectar Google Calendar", que redirige a
    `accounts.google.com`) necesita `hx-boost="false"` explícito — htmx
    intercepta el click, hace el GET por XHR, y no puede seguir un redirect
    cross-origin, así que el click queda tragado sin error visible. Mismo
    criterio que los forms de upload de archivo (ver "UI y white-label"
    abajo).

## Importador de Excel (Proyecto 2)

Agregado después de Fase 6, fuera del scope original de `ROADMAP.md` (lo
llama "Proyecto 2" en el spec/plan de `docs/superpowers/`) — deja que el
staff cargue rutinas y ejercicios en lote desde un `.xlsx` en vez de
hacerlo fila por fila desde el panel. Spec y plan completos en
`docs/superpowers/specs/2026-07-27-importador-planes-entrenamiento-design.md`
y `docs/superpowers/plans/2026-07-27-importador-planes-entrenamiento-plan.md`
(14 tareas, cada una con su propia revisión — la Tarea 14 y el fix wave post
revisión-final surgieron de una revisión de rama completa, no estaban en el
plan original).

- **`importaciones`** (`models.py`, `parsing.py`, `matching.py`,
  `services.py`, `forms.py`, `views.py`): dos flujos independientes,
  namespace `importaciones:` — `plantillas_subir`/`plantillas_preview` (crea
  `RutinaPlantilla`) y `biblioteca_subir`/`biblioteca_preview` (crea
  `Ejercicio`). Mismo patrón subir → previsualizar → confirmar en los dos:
  `previsualizar_importacion_*` parsea el archivo y crea una fila
  `Importacion` (`TenantOwnedModel`, `resultado` es un `JSONField` con todo
  lo necesario para el preview y el confirm — nunca vuelve a abrirse el
  archivo original) sin tocar `RutinaPlantilla`/`Ejercicio`;
  `confirmar_importacion_*` recién ahí escribe, adentro de una transacción
  con `select_for_update()` sobre la `Importacion` (mismo patrón anti-TOCTOU
  que el resto del repo — evita doble confirmación concurrente).
- **Entrada al importador desde el listado, no desde el nav**: `Importar
  rutinas`/`Importar ejercicios` ya no son items propios del nav-staff
  (`base.html`) — el alta manual y la importación desde Excel de cada
  dominio quedan juntas en su pantalla de listado (`rutinas/plantilla_list.
  html`, `ejercicios/ejercicio_list.html`), un botón `.boton` ("Nueva
  plantilla"/"Nuevo ejercicio") junto a uno `.boton-secundario` ("Importar
  desde Excel") en el mismo `.acciones-lista`, mismo patrón que ya usaba
  `pagos/pago_list.html` con dos acciones secundarias. Acorta el nav de 10 a
  8 items y pone las dos formas de cargar datos en el mismo lugar en vez de
  dispersas.
- **`parsing.py`** es Django-free a propósito (testeable con
  `SimpleTestCase`, sin DB) — lee el `.xlsx` con `openpyxl`, resuelve celdas
  combinadas, detecta columnas por alias (case/acentos-insensible) y arma
  filas válidas/inválidas. Fila inválida = se salta y se lista con motivo,
  nunca invalida la hoja entera (salvo que falte una columna REQUERIDA en
  TODA la hoja, ahí se excluye esa hoja sola, no el archivo).
- **`matching.py`**: matching difuso de nombres de ejercicio contra la
  biblioteca del gimnasio vía `rapidfuzz` (`PISO_SCORE=60`,
  `UMBRAL_AMBIGUO=87` — por debajo de 60 es "nuevo", 60-86 es "ambiguo",
  ≥87 se trata como confiable). Un match ambiguo NUNCA se resuelve solo:
  queda pre-marcado "usar existente" pero el staff tiene que elegir
  activamente en el preview (plantillas vía `ResolucionEjercicioFormSet`;
  biblioteca vía el mismo campo JSON único que ya lleva `grupo_muscular`,
  ver el punto siguiente). `Ejercicio.grupo_muscular` nuevo nunca tiene
  default silencioso — choices cerradas, el staff lo elige siempre.
- **Gotcha de escala (biblioteca)**: el flujo de biblioteca reemplaza el
  patrón de "un form de Django por ejercicio pendiente" (el que sí usa
  plantillas) por un único campo JSON serializado a mano con un poco de JS
  vanilla (sin build, sin Alpine) — una biblioteca real puede traer miles de
  filas, y un formset de ese tamaño rompe
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` (default 1000 de Django). Si tocás este
  flujo, NO reintroduzcas un formset por ítem ahí — ver `ISSUES.md`
  `[2026-07-28]` para el detalle completo (incluye el caso simétrico de
  plantillas, aceptado como riesgo documentado en vez de arreglado, porque
  el dueño confirmó que una plantilla real nunca supera ~300 ejercicios
  distintos).

## UI y white-label (Fase 4)

- **Tailwind sin reescribir plantillas**: en vez de convertir las ~25
  plantillas existentes a clases utilitarias, se redefinieron los mismos
  nombres de clase que ya usaban (`.tarjeta`, `.boton`, `.badge--ok`,
  `.tabla`, `.contenido--ancho`, etc.) como clases de componente con `@apply`
  en `styles/input.css` (`@layer components` — patrón que la propia
  documentación de Tailwind recomienda). Si agregás una plantilla nueva,
  reusá estas clases en vez de escribir utilidades sueltas repetidas; si te
  hace falta una nueva, defínila ahí, no inline en el HTML.
- **`styles/input.css` (fuente) vive fuera de `static/`** a propósito: si
  quedara dentro de `STATICFILES_DIRS`, Django lo recolecta como si fuera un
  asset servible y WhiteNoise intenta parsear sus `@import`/`@source` como
  URLs de CSS, rompiendo `collectstatic` (pasó en Fase 5, ver `ISSUES.md`).
  Solo `static/css/app.css` (el output compilado) se sirve.
- **Build**: `npm run build:css` compila `styles/input.css` →
  `static/css/app.css` (el que de verdad se sirve). `npm run watch:css`
  durante desarrollo. El output SÍ se versiona en git (`node_modules/` no) —
  Render no corre `npm`, así que el CSS compilado tiene que estar en el repo.
  **Si tocás `input.css`, corré `npm run build:css` antes de commitear.**
- **Colores por gimnasio**: `Gimnasio.color_primario`/`color_secundario` son
  datos de runtime, no algo que Tailwind conozca en build-time. Se definen
  como variables CSS (`--color-primario`/`--color-secundario`, default en
  `input.css`) y `base.html` las sobreescribe inline por request si el
  gimnasio logueado tiene colores propios. El resto de la UI los referencia
  vía `bg-[var(--color-primario)]` (clases arbitrarias) o, para lo ya
  existente, a través de `.boton`/`.tabla th`/etc.
- **Tipografía por gimnasio**: `Gimnasio.tipografia` es un `TextChoices` con
  6 opciones curadas de Google Fonts (Inter, Montserrat, Poppins, Oswald,
  Playfair Display) más `sistema` como default — texto libre queda afuera a
  propósito, mismo criterio que `grupo_muscular` de `Ejercicio`: un catálogo
  cerrado evita que el dueño rompa la estética con una fuente ilegible. El
  default `sistema` no carga ningún recurso externo: mapea a `var(--font-sans)`,
  el stack que Tailwind v4 ya aplica por preflight, así que un gimnasio
  existente no cambia de aspecto hasta que el dueño elige explícitamente. Las
  demás opciones se sirven desde Google Fonts CDN, no auto-hospedadas (mismo
  criterio que Alpine.js/htmx por CDN) — se reevalúa si el tráfico lo
  justifica. El mapeo tipografía → (familia CSS, query de Google Fonts) vive
  en `Gimnasio.TIPOGRAFIA_FUENTES`, única fuente de verdad para `base.html` y
  el preview en vivo de `gimnasio_form.html`. La variable `--font-gimnasio`
  sigue el mismo patrón que los colores (default en `input.css`, override
  inline en `base.html`), aplicada vía `font-[family-name:var(--font-gimnasio)]`
  en `body` — el hint `family-name:` es necesario porque sin él Tailwind
  interpreta `font-[var(...)]` como `font-weight`, no `font-family` (ambigüedad
  de la sintaxis de valores arbitrarios). **Gotcha de autoescape**: el valor de
  `tipografia_css_family` se inyecta en `base.html` con `|safe` a propósito —
  `<style>` es un elemento "raw text" y el navegador no decodifica entidades
  ahí adentro, así que sin `|safe` el autoescape de Django convierte las
  comillas de `'Playfair Display'` en `&#x27;` y rompe el CSS en vez de
  protegerlo (el valor sale de un dict fijo del código, nunca de input de
  usuario, por eso es seguro). Ver `tenants.tests.GimnasioUpdateViewTests.
  test_tipografia_con_comillas_no_queda_html_escapada`.
- **`tenants:gimnasio_editar`** (`GimnasioUpdateView`, sin pk en la URL —
  siempre edita el gimnasio del `Perfil` logueado): logo, colores, tipografía,
  texto de bienvenida, contacto, redes, con preview en vivo (JS vanilla sobre
  el mismo `<form>`, sin depender de htmx porque el form ya tiene
  `hx-boost="false"` por el upload de logo) de cómo el alumno va a ver esos
  cambios antes de guardar. Es lo que le faltaba a Fase 1/2: el modelo tenía
  estos campos desde Fase 1 pero no había ninguna vista para editarlos fuera
  de `/admin/`.
- **HTMX**: `hx-boost="true"` en `<body>` (`base.html`) — convierte toda
  navegación por `<a>`/`<form>` normal en transiciones AJAX sin reescribir
  ninguna vista (siguen devolviendo la página completa; htmx solo evita el
  reload duro). Excluido explícitamente (`hx-boost="false"`) en los dos
  forms con upload de archivo (`pagos/pago_confirmar.html`,
  `tenants/gimnasio_form.html`, para no arriesgar el envío de multipart) y en
  el link "Conectar Google Calendar" (`mis_turnos.html`, para no tragarse el
  redirect cross-origin a `accounts.google.com` — ver la sección de Google
  Calendar arriba). Regla general: cualquier `<a>`/`<form>` que dependa de un
  redirect externo o de multipart necesita `hx-boost="false"`.
- **Alpine.js**: solo para el toggle del nav en mobile (`x-data` en `<body>`,
  compartido entre el botón ☰ del header y el `<nav>` — deben estar en el
  MISMO scope de `x-data`, si no el toggle no hace nada). No se usó para
  nada más ("solo si hace falta", ROADMAP Fase 4).

## Deploy (Fase 5)

**Estado (2026-07-08): código listo, falta la parte manual en Render y
Cloudflare** (cuentas/pagos de terceros — eso no lo puedo hacer yo). Repo en
`https://github.com/fabri07/app-gim` (privado).

- **Plan elegido: arrancar en el free tier de Render, upgradear cuando entre
  el primer gimnasio pago** (decisión del usuario, coincide con "primero se
  cobra, después se sofistica"). El Postgres free expira a los 90 días y el
  web service se duerme sin tráfico — aceptado a propósito, ver `ISSUES.md`.
- **`render.yaml`** define el Blueprint: web service (free) + Postgres
  (free). El cron de `generar_pagos` queda comentado en el archivo — **Render
  no tiene plan free para cron jobs**, hace falta upgradear a Starter (o
  correr el comando a mano desde la Shell de Render) hasta entonces.
- **Pasos manuales que quedan** (no los puedo hacer yo — cuentas de
  terceros):
  1. En Cloudflare: crear un bucket R2 + un token de API (Account API Token
     con permiso de Object Read & Write sobre ese bucket). Anotar: nombre
     del bucket, Access Key ID, Secret Access Key, y el endpoint S3
     (`https://<account_id>.r2.cloudflarestorage.com`).
  2. En Render: "New" → "Blueprint" → conectar `fabri07/app-gim` → aplicar
     `render.yaml`. Después del primer deploy, completar a mano en el
     dashboard las env vars marcadas `sync: false` en el Blueprint:
     `DJANGO_ALLOWED_HOSTS`/`DJANGO_CSRF_TRUSTED_ORIGINS` (con la URL real
     que Render asigna) y las 4 `R2_*` (con lo del paso 1).
  3. Verificar: la app levanta, el login funciona, un logo/comprobante
     subido efectivamente aparece en el bucket de R2 (no en el filesystem
     de Render).
  4. Opcional (integración con Google Calendar): crear credenciales OAuth
     "Web application" en Google Cloud Console, y setear en Render las 4 env
     vars `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`/
     `GOOGLE_OAUTH_REDIRECT_URI`/`GOOGLE_TOKEN_ENCRYPTION_KEY` (las 4 o
     ninguna — settings.py revienta al arrancar si están parciales). Sin
     esto la app funciona igual; simplemente el alumno no ve la opción de
     conectar su calendario (`GOOGLE_CALENDAR_ENABLED = False`).
- **Settings de producción** (`config/settings.py`): `DATABASE_URL` (Postgres
  si está seteada, SQLite si no — mismo criterio que el resto del archivo),
  `STORAGES["default"]` cambia a `storages.backends.s3.S3Storage` solo si
  `R2_BUCKET_NAME` está seteada (si no, sigue en `FileSystemStorage` como en
  dev), `STORAGES["staticfiles"]` usa el manifest comprimido de WhiteNoise
  SOLO fuera de `DEBUG` (con `DEBUG=True` no hay `collectstatic` corrido, así
  que exigir el manifest rompe `{% static %}` en dev/tests — pasó durante
  esta fase, quedó cubierto por un test). `CSRF_TRUSTED_ORIGINS` nuevo,
  mismo patrón CSV-por-entorno que `ALLOWED_HOSTS`.
- **`WhiteNoiseMiddleware`** va justo después de `SecurityMiddleware` (sirve
  los estáticos sin depender de Nginx/CDN aparte).

## Comandos

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # completar si hace falta

python manage.py migrate
python manage.py runserver
python manage.py test -v 2           # suite completa
python manage.py createsuperuser     # acceso a /admin/
python manage.py generar_pagos       # autogenera pendientes del mes + vence atrasados
python manage.py collectstatic       # solo hace falta simulando producción (DEBUG=False)

npm install                          # una vez, para compilar Tailwind
npm run build:css                    # compila static/css/input.css -> app.css
npm run watch:css                    # lo mismo, en watch mode durante desarrollo
```

## Canales de auditoría (fallas y problemas)

- **`logs/app.log`** (rotado, no versionado): logging estructurado
  configurado en `config/settings.py` (`LOGGING`). Captura consola + archivo;
  `django.request` a nivel `ERROR` asegura que un 500 no manejado quede
  registrado, no solo impreso en una consola efímera.
- **`ISSUES.md`**: registro humano de problemas, causa y resolución —
  complementa al log (que es de runtime, no de decisiones). Agregar una
  entrada ahí cada vez que se resuelve algo no obvio o se acepta un riesgo a
  propósito.
- **Tests** (`python manage.py test`): cada modelo tenant-owned nuevo debe
  tener al menos un test de aislamiento (que un gimnasio no vea datos de
  otro), siguiendo `tenants/tests.py::TenantIsolationTests` como referencia.

## Qué NO construir todavía

QR de ingreso, control de asistencia, app nativa, Mercado Pago/débitos
automáticos, chat interno, nutrición, métricas deportivas, IA de rutinas,
subdominios por gimnasio. Ver ROADMAP.md § "Fase 9" para la lista completa y
cuándo sí tiene sentido evaluarlas (solo con clientes pagos).
