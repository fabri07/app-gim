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

**Fase actual: código de Fases 0-6 completo en `main` y desplegado.** La app
vive en `https://app-gim.onrender.com` (Render, free tier) y el bucket de
Cloudflare R2 (`app-gim-media`) ya está creado y en uso — los pasos manuales
de Fase 5 que dependían de cuentas de terceros están hechos. Ver "Deploy
(Fase 5)" más abajo para el estado exacto y lo que sigue pendiente. Fases 0-4
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

- **`alumnos`** — `Alumno(TenantOwnedModel)`. Además de los datos de contacto
  de Fase 1, tiene una ficha de inscripción ampliada (agregada después de
  Fase 6, fuera del scope original) que el staff carga el día del alta:
  `sexo` y `frecuencia_actividad_previa` son catálogos cerrados (`TextChoices`,
  mismo criterio que `grupo_muscular` de `Ejercicio`); `deportes_practica`,
  `discapacidad_detalle` y `enfermedad_cronica_detalle` son texto libre a
  propósito (no amerita un catálogo cerrado). Todos `blank=True`/opcionales:
  no todo alumno cuenta todo el detalle en el momento, y los alumnos ya
  existentes antes de esta feature no lo tienen cargado. La edad sigue
  siendo `fecha_nacimiento` (ya existía) — no se agregó un campo `edad`
  aparte para no duplicar el dato y arriesgar que se desincronice.
- **`ejercicios`** — `Ejercicio(TenantOwnedModel)`, biblioteca por gimnasio
  (no global; ver docstring del módulo).
- **`rutinas`** — `RutinaPlantilla`/`RutinaPlantillaItem` (editable) y
  `RutinaAsignada`/`RutinaAsignadaItem` (snapshot congelado). La copia se
  hace con `RutinaAsignada.crear_desde_plantilla(...)` y
  `RutinaPlantilla.duplicar()` — ambas transaccionales. Los modelos "Item" NO
  son `TenantOwnedModel` (se acceden vía su padre, que ya está scopeado).
  **RPE** (agregado después de Fase 6): `RutinaAsignadaItem.rpe` es un
  `TextChoices` de 4 niveles ("Podría hacer más intenso" ... "Debería bajar
  la intensidad") que el propio alumno carga desde su portal (`home.html`,
  un `<select>` que se auto-envía a `rutinas:item_calificar`,
  `RutinaAsignadaItemCalificarView` con `AlumnoRequiredMixin`, mismo patrón
  que `NovedadMarcarLeidaView`) — solo puede calificar items de su rutina
  **activa** (una vieja/cerrada da 404, igual que un item de otro alumno). El
  staff lo ve de solo lectura en `rutinas:asignada_detalle`. **Riesgo
  aceptado a propósito**: como `RutinaAsignadaItem` no tiene FK viva a
  `Ejercicio` (es un snapshot, ver arriba), agregar el RPE por ejercicio para
  el dashboard del dueño va a tener que agrupar por `ejercicio_nombre_snapshot`
  (texto) — si un ejercicio se renombra en la biblioteca, el historial viejo
  de RPE no se fusiona con el nombre nuevo. Es consecuencia directa de que el
  RPE es una calificación por sesión/semana (lo que pidió el dueño del
  producto), no una opinión general y estable del ejercicio.
  **Agrupamiento por grupo muscular y PDF** (agregado después de Fase 6):
  `rutinas/agrupacion.py::agrupar_items_por_grupo_muscular()` es el único
  lugar que agrupa los items de un día por `grupo_muscular_snapshot` —
  lo usan tanto el portal del alumno (`RutinaMiDiaDetailView` →
  `mi_dia_detalle.html`, un día por vez, con las 4 semanas lado a lado en
  columnas separadas por Series/Reps/Kilos/Descanso/Calificación desde el
  rediseño "tabla ancha por columna") como `rutinas/pdf.py::
  generar_pdf_rutina_asignada()` (fpdf2, Django-free a propósito, recorre
  todos los días). `RutinaAsignadaPdfView` (staff-only, botón "Descargar
  PDF" en `asignada_detail.html`) es el fallback en papel para cuando un
  alumno se queda sin acceso al portal — pensado para imprimir, no como
  documento de marketing. **Mantené el desglose de campos del PDF
  sincronizado con el de la tabla del portal**: el PDF original (commit
  `51239e5`) empaquetaba todo en una celda compacta tipo "3x12 · 20kg
  (hecho)", y quedó desactualizado cuando `d0de225` separó esas columnas
  en pantalla — se corrigió después para que `_celda_semana` liste
  Series/Repeticiones/Kilos/Descanso/Calificación (con
  `item.get_rpe_display()`, no un genérico "(hecho)") en líneas
  separadas, y cada fila lleve el grupo muscular como subtítulo bajo el
  nombre del ejercicio, igual que la tabla en pantalla.
- **`pagos`** — `PagoMensual(TenantOwnedModel)` y `MedioCobro(TenantOwnedModel)`
  (alias/CBU/lo que el gimnasio muestra al alumno para pagar, editable por
  staff). `pagos/models.py` expone `generar_pagos_pendientes(mes, anio)` y
  `marcar_vencidos(mes, anio, dia)`; `python manage.py generar_pagos` corre
  ambas para el mes/día actual — lo programa
  `.github/workflows/generar-pagos.yml` (GitHub Actions, no Render: no hay
  cron en el plan free). `marcar_vencidos` vence tanto los pendientes de
  meses ya cerrados como los del mes en curso que ya pasaron el
  `Gimnasio.dia_vencimiento_pago` de su propio gimnasio (join por FK, cada
  gimnasio tiene el suyo) — antes ese campo era solo cosmético en el portal
  del alumno.
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
  con pago pendiente, pagos del mes, rutinas activas, últimas novedades) +
  analítica (subproyecto 4, agregada después de Fase 6; el 5to gráfico
  "ejercicios más asignados" se sumó más tarde, ver abajo): asistencia por
  día/hora, alumnos por género, RPE por ejercicio, y ejercicios más
  asignados (general + desglosado por género). La agregación vive en
  `tenants/analitica.py` (no en la vista) porque cruza 3 apps (turnos,
  alumnos, rutinas) y se testea mejor sola. Asistencia agrupa TODO el
  historial de `Reserva` por día de semana + hora (no una ventana de
  tiempo) para revelar el patrón recurrente de horas pico — decisión
  explícita del dueño del producto; "ejercicios más asignados" sigue el
  mismo criterio (todo el historial, no solo rutinas activas). Los
  gráficos siguen la skill `dataviz`: la grilla de calor de asistencia es
  HTML/CSS puro (color secuencial azul; Chart.js no trae heatmap nativo
  sin plugin aparte), género y "ejercicios más asignados" (general) son
  una barra Chart.js de un solo color (las categorías ya se identifican
  por el eje), RPE por ejercicio es una barra apilada horizontal
  **divergente** azul↔rojo (mismo tratamiento que una escala Likert), y
  "ejercicios más asignados por género" es una barra apilada horizontal
  con una **paleta categórica** nueva de 4 colores (azul/naranja/aqua/
  amarillo, slots 1-4 del tema por defecto de `dataviz`, documentada en
  `DESIGN.md` § "Paleta categórica de dataviz") — cargados por CDN solo en
  `home.html`, no en todo el sitio. `ejercicios_mas_asignados_por_genero`
  reusa el ranking (mismo conjunto, mismo orden) de `ejercicios_mas_asignados`
  en vez de ordenar independiente, para que los dos gráficos se lean lado a
  lado sin que las barras cambien de orden entre uno y otro — el costo
  aceptado es correr la query de ranking dos veces por carga del
  dashboard (agregado liviano, acotado por gimnasio). "Ejercicios más
  asignados" cuenta CUALQUIER `RutinaAsignadaItem` asignado (a diferencia
  de RPE, que excluye `rpe=""`): mide qué se pone en las rutinas, no qué
  se calificó. Cada gráfico tiene su "Ver como tabla" (`<details>`, sin
  JS) como equivalente accesible. Para `alumno`: el portal de Fase 3 (su
  rutina activa, su cuota del mes, últimas novedades) — ver más abajo.
- **`RutinaPlantillaItem`/`RutinaAsignadaItem`** no son `TenantOwnedModel`
  (no tienen `gimnasio` propio): sus vistas resuelven el aislamiento
  buscando primero el padre vía `for_gimnasio()` antes de tocar el item — ver
  `rutinas/views.py` (`ItemPlantillaMixin`).
- `PagoMensual` sigue sin vista de "crear" — el staff solo confirma pagos ya
  autogenerados (principio no negociable §3).

## Portal del alumno y acceso (Fase 3)

- **Alta de gimnasios: por comando, no self-serve.** `/accounts/register/` era
  público y sin throttling (cualquiera creaba User + Gimnasio + Perfil STAFF y
  quedaba logueado); se cerró el 2026-07-29, ver `ISSUES.md`. Hoy el único
  camino es `python manage.py crear_gimnasio`
  (`tenants/services.py::crear_gimnasio`), que crea al dueño con
  `set_unusable_password()` porque el staff va a entrar por Google. **No
  reintroduzcas una vista de registro** sin volver a discutir la decisión.
- **Acceso**: `Alumno.perfil` (`OneToOneField` a `tenants.Perfil`, nullable)
  vincula un alumno con su login. El staff lo crea/resetea desde la ficha del
  alumno (`alumnos:acceso_crear` / `alumnos:acceso_cambiar_password`,
  `alumnos/views.py::CrearAccesoView`/`CambiarPasswordAlumnoView`) — un form
  plano (no `ModelForm`), con la contraseña en texto plano en pantalla
  (`help_text` lo explica: es la única vez que se puede leer, el staff la
  tiene que copiar para pasársela al alumno). `username` es único GLOBAL
  (`auth.User`, sin namespacing por gimnasio) — el form lo valida y sugiere
  uno libre (mismo patrón que `tenants.services.slug_disponible`).
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

## Accesos, revocación y suplantación (Frente B)

Completa lo que a Fase 3 le faltaba para que el dueño opere sin llamar al
desarrollador. Spec y plan en `docs/superpowers/{specs,plans}/
2026-07-30-portal-de-cuentas-*`.

- **El identificador del alumno es su email o su teléfono**, a elección del
  staff. `alumnos/identidad.py` los normaliza (email a minúsculas —
  `User.objects.get(username=...)` es case-sensitive en Postgres, así que sin
  eso `Juan@x.com` y `juan@x.com` serían dos cuentas; teléfono a `+54...`
  sacando el `0` de característica y el `15`). El módulo es **Django-free a
  propósito** y se testea con `SimpleTestCase`: el riesgo real es que la
  normalización difiera entre el alta y el login, porque ahí el alumno no entra
  y no puede darse cuenta solo.
  - **No hace falta un `User` custom**: `UnicodeUsernameValidator` acepta `@` y
    `+` (regex `^[\w.@+-]+\Z`). Hay un test que fija ese supuesto.
- **La contraseña la genera SIEMPRE la app** (`alumnos/services.py`, reusando
  `tenants.services.generar_password`) y se muestra **una sola vez** en
  `acceso_credenciales.html`. **No pasa por `messages`**: `messages` se
  serializa en la sesión, que en este proyecto vive en la base de datos. El
  POST no redirige (se rompe PRG a propósito; el F5 lo cubre el guard de "este
  alumno ya tiene acceso").
- **`Alumno.estado` es el maestro del acceso**, y la sincronización con
  `User.is_active` vive en **un solo lugar**:
  `alumnos/signals.py::sincronizar_acceso_con_estado` (`post_save` sobre
  `Alumno`). **No la repitas en las vistas.** El estado se escribe desde tres
  caminos —el botón de baja, el form de la ficha (donde `estado` es editable) y
  `crear_acceso` sobre un alumno ya dado de baja— y ponerlo en cada vista
  garantiza que alguna se olvide; una revisión encontró exactamente eso, con
  dos de los tres caminos rotos. El receiver chequea `raw` para no repetir el
  problema de `calendario/signals.py`.
  - No hace falta invalidar sesiones a mano: `ModelBackend.get_user()`
    revalida `is_active` en CADA request. Regenerar la contraseña también
    expulsa al alumno, porque `get_session_auth_hash()` deriva del hash.
  - `crear_acceso` toma `select_for_update()` sobre el `Alumno` y traduce
    `IntegrityError` a `IdentificadorEnUso`: sin eso, un doble submit del form
    (va boosteado por htmx) creaba dos `User`+`Perfil` y dejaba uno huérfano
    que podía loguearse y no aparecía en ningún panel.
- **Panel `alumnos:accesos`**, colgado del listado de alumnos y **no del nav**
  (ya tiene 8 ítems tras el esfuerzo de bajarlo de 10; mismo criterio que el
  importador). El `select_related("perfil__usuario")` no es cosmético: sin él
  son 17 queries donde ahora hay 7, y hay un test que lo prueba comparando dos
  tamaños de conjunto (no un `assertNumQueries` fijo, que se rompe con cambios
  internos de Django).
- **NO se guardan contraseñas legibles.** Se pidió mostrar las de todos los
  alumnos en una sección y se descartó: ver `ISSUES.md`. La alternativa es
  suplantación + regeneración.
- **Suplantación** (`tenants/suplantacion.py`, servicio; auditada en
  `RegistroSuplantacion`, que SÍ es `TenantOwnedModel`). Reglas: solo staff,
  solo alumnos activos del propio gimnasio (404), nunca a otro staff ni a una
  cuenta con privilegios, no anidable, POST-only, y **máximo 2 h** — el límite
  lo aplica `tenants/middleware.py::ExpirarSuplantacionMiddleware`, que es el
  único middleware propio del proyecto: la expiración tiene que evaluarse en
  cada request y no hay otro lugar donde hacerlo.
  - `iniciar()` también chequea `usuario.is_active`, porque **`login()` no lo
    valida**: con un usuario desactivado la suplantación "funcionaba" y el
    staff perdía su sesión en el request siguiente, sin poder ni volver.
  - **Las dos trampas de `django.contrib.auth.login()`**, cada una con test de
    regresión. (1) `login()` hace `session.flush()` al cambiar de usuario: la
    clave de retorno se escribe **DESPUÉS**, nunca antes. (2) `login()` emite
    `user_logged_in`, y dos receivers corromperían datos —
    `alumnos/signals.py` estamparía `fecha_activacion` a un alumno que nunca
    entró, y `update_last_login` pisaría el "último ingreso" del panel. Se
    resuelven con `request._suplantacion_en_curso` y un `UPDATE` de
    restauración. **Nunca con `signal.disconnect()`**: muta estado global y no
    es thread-safe.
  - `last_login` se lee de la BASE, no del objeto en memoria: con una
    instancia desactualizada, "restaurar" borraría el valor real.
  - `volver()` es fail-closed y revalida TODO contra la base, **incluido que
    el staff sea del mismo gimnasio** — sin eso, una sesión manipulada
    permitía saltar de tenant.
  - `VolverDeSuplantacionView` **no** lleva `StaffRequiredMixin`: durante la
    suplantación el usuario es el ALUMNO, y exigir rol staff dejaría al staff
    atrapado.
  - **Conectar/desconectar Google Calendar está bloqueado mientras se
    suplanta**: el flujo OAuth usa la cuenta de Google de quien está frente al
    navegador, así que el staff vincularía la suya al calendario del alumno.
  - **Deuda para el Frente C**: `tenants/suplantacion.BACKEND` apunta a
    `ModelBackend`. Cuando exista `PerfilModelBackend` (y django-axes por
    delante) hay que actualizarla, o `login()` elige mal el backend.

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
- **Colores por gimnasio**: desde el rediseño "Un Paisaje por Gimnasio"
  (2026-08-13, `85ca0a3`) ya no son 2 colores libres — `Gimnasio.paleta` es
  un catálogo cerrado de 4 paisajes curados (Bosque/Océano/Arena/Pizarra,
  `Gimnasio.PALETAS`), cada uno con sus 3 roles (`fondo`/`primario`/
  `secundario`) ya armonizados. Datos de runtime, no algo que Tailwind
  conozca en build-time: se definen como variables CSS
  (`--color-fondo`/`--color-primario`/`--color-secundario`, default en
  `input.css`) y `base.html` las sobreescribe inline por request si el
  gimnasio logueado tiene un paisaje propio. El resto de la UI los
  referencia vía `bg-[var(--color-primario)]` (clases arbitrarias) o, para
  lo ya existente, a través de `.boton`/`.tabla th`/etc. El canvas de fondo
  (`body`, `.landing`) no es un color sólido plano: lleva una atmósfera de
  blobs radiales suaves mezclados con `color-mix()` sobre esos mismos
  tokens (2026-08-14, ver "The Atmospheric Canvas Rule" en `DESIGN.md`).
  Al elegir un logo nuevo en `gimnasio_form.html`, `tenants/
  paisaje_matching.py::sugerir_paisaje()` extrae su color dominante
  (ignorando fondo blanco/negro/transparente) y preselecciona el paisaje
  curado más parecido vía `tenants:logo_sugerir_paisaje` — sugerencia pura,
  no persiste nada, el dueño la confirma o la cambia a mano con "Guardar
  cambios" (ver `ISSUES.md` `[2026-08-14]` sobre por qué la distancia es
  RGB simple, no Lab/CIEDE2000).
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
  interpreta un valor arbitrario de font sin ese hint como `font-weight`, no
  `font-family` (ambigüedad de la sintaxis de valores arbitrarios). **Gotcha
  de autoescape**: el valor de
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

## Landing pública (subproyecto 5, más allá del ROADMAP original)

`tenants.views.GimnasioLandingView` (ruta `g/<slug>/`, `tenants/urls.py`) es
la **primera vista del proyecto sin ningún mixin de autenticación** —
accesible logueado o no. Sin subdominios por gimnasio (principio no
negociable #6): la URL se resuelve por `Gimnasio.slug`, que existía desde
Fase 1 sin ningún uso público hasta ahora. `get_queryset` filtra
`activo=True`: un gimnasio desactivado o un slug inexistente dan 404 por
igual (no revela cuál de los dos casos es).

No hay alta de leads propia ni formulario de contacto — decisión explícita
del dueño del producto: los alumnos NO pueden autoregistrarse (el staff
asigna usuario/contraseña a mano, ver `alumnos/views.py::CrearAccesoView`),
así que la landing solo ofrece contactar al gimnasio (`link_whatsapp`/
`link_instagram`/`contacto`, campos que ya existían desde Fase 1) o, si ya
es alumno, ir al login de siempre.

**Blanco-etiquetado sin tocar el `:root` global**: `templates/tenants/
landing.html` pisa `--color-primario`/`--color-secundario` y `font-family`
con un `style` inline en su propio `<div class="landing">` — como son
variables CSS heredadas, `.boton`/`.boton-secundario`/`a` reusan
automáticamente el color de ESE gimnasio adentro del `.landing`, sin mutar
las variables globales que usa `base.html` cuando hay un usuario logueado
(esas dependen de `user.perfil.gimnasio`, que un visitante anónimo no
tiene). El `<main>` de `base.html` no envuelve esta página en
`.contenido`/`.contenido--ancho` (`{% block main_class %}` vacío): el hero
necesita ir a todo el ancho de la pantalla, algo que ningún otro template
del proyecto necesitaba hasta ahora.

**Modo "Persuade"** (skill impeccable/dataviz): a diferencia del resto del
sitio (paneles de gestión, modo "Operate"), esta es la primera superficie
pensada para persuadir, no para operar — hero a todo el ancho con un
degradé de los dos colores del gimnasio (estrategia de color "Committed":
el color de marca ocupa una región entera, no un acento suelto) y un único
CTA primario (WhatsApp). El resto del sitio sigue en la paleta neutra
existente; este tratamiento es exclusivo de `landing.html`.

## Login por gimnasio y fix de usuario ya autenticado (subproyecto 6, más allá del ROADMAP original)

Agregado tras un reporte real en producción, con capturas: un staff ya
logueado que visitaba `/accounts/login/` veía su propio topbar, la nav
completa de staff y el fondo de su gimnasio **superpuestos** con el
formulario de login — muy confuso. Causa: `auth_views.LoginView` no redirige
por default a un usuario ya autenticado, y `base.html` renderiza el
topbar/nav en base a `user.is_authenticated` sin ningún caso especial para
la página de login.

`tenants.views.LoginView` agrega `redirect_authenticated_user = True` sobre
el `LoginView` de Django — un usuario ya logueado que visita
`/accounts/login/` (o `g/<slug>/login/`) es redirigido directo a
`LOGIN_REDIRECT_URL` ("home") en vez de ver el form. Vive como clase propia
(no como kwarg inline en `tenants/urls.py`) para que `GimnasioLoginView` la
herede sin duplicar el flag.

**`GimnasioLoginView`** (ruta `g/<slug>/login/`) es la versión "gym-specific":
resuelve el `Gimnasio` por slug con `gimnasio_activo_o_404` (helper
extraído de lo que antes era `GimnasioLandingView.get_queryset`, ahora
compartido por las dos vistas para que no puedan divergir en el criterio de
404 "no revela si el slug existió alguna vez"). No hereda de
`DetailView`/`SingleObjectMixin`: `LoginView` ya es una `FormView`, mezclar
dos jerarquías de vista genérica no aporta nada — alcanza con resolver el
gimnasio en `dispatch` y agregarlo al contexto.

**El slug es puramente estético, no una barrera de autenticación**: el
proyecto no tiene subdominios por gimnasio (principio no negociable #6), así
que un alumno de OTRO gimnasio, o un miembro de staff, pueden loguearse
igual desde `g/<cualquier-slug>/login/` — es el mismo `User`/`Perfil` de
siempre, sin restricción adicional. Solo cambia qué `Gimnasio` se le pasa al
template para pintar colores/logo/tipografía/copy antes de loguearse. Hay
un test de regresión (`test_alumno_de_otro_gimnasio_puede_loguearse_igual`)
que fija este comportamiento a propósito.

`templates/registration/login.html` NO reusa el `<style>` del `<head>` de
`base.html` (que depende de `user.perfil.gimnasio`, inexistente para un
visitante anónimo) — en cambio duplica el patrón ya usado por
`landing.html` (variables CSS inline en el wrapper `.auth-hero`,
`{% block extra_style %}` para fondo imagen/doodle, mismo criterio
`isolation: isolate` para que el doodle no quede tapado). Se evaluó
generalizar el bloque de `base.html` para que acepte un `gimnasio` de
contexto además de `user.perfil.gimnasio`, y se descartó: acoplaría el
head-style de TODA página autenticada a una necesidad exclusiva del login.
`.auth-hero--gimnasio` en `styles/input.css` duplica el mismo canvas
atmosférico de 3 blobs radiales que ya usan `body` y `.landing` — tercera
copia a propósito, mismo criterio ya documentado ahí: sin preprocesador CSS
no hay forma limpia de compartirlo.

Con `gimnasio` en contexto, el copy de marketing genérico
("Gestionar tu gimnasio es más fácil...") y el dibujo de atletas
(`atletas_frieze.html`) se reemplazan por el nombre del gimnasio y su
`texto_bienvenida` — decisión explícita del dueño del producto: un alumno
logueándose a SU gimnasio no debería ver un pitch de venta dirigido a
dueños de gimnasios. Sin `gimnasio` en contexto (login genérico, o cuando
Django redirige acá desde `LOGIN_URL` por una vista protegida — ese flujo
no tiene forma de saber el slug), el template renderiza EXACTAMENTE igual
que antes: paisaje Bosque default, copy de marketing y atletas.

`landing.html` enlaza "Iniciar sesión" a `login_gimnasio` con el slug del
gimnasio que se está visitando (antes iba al login genérico, sin contexto).

## Deploy (Fase 5)

**Estado (2026-07-30): desplegado.** App en `https://app-gim.onrender.com`
(Render free tier, Blueprint aplicado), media en el bucket R2
`app-gim-media`. Repo en `https://github.com/fabri07/app-gim` (privado).

**Dominio propio: `tugimapp.com`** — comprado en Cloudflare el 2026-07-30, por
un año. Todavía NO está apuntado a Render. Cuando se conecte hay que tocar
cuatro cosas, y ninguna es opcional: (1) `DJANGO_ALLOWED_HOSTS` y (2)
`DJANGO_CSRF_TRUSTED_ORIGINS` en Render; (3) el redirect URI de Google Calendar
en la consola de Google Cloud (`https://tugimapp.com/calendario/callback/`),
que si no queda apuntando a `app-gim.onrender.com` y rompe la integración; y
(4) los registros SPF/DKIM que pida el proveedor de email. El dominio es
justamente lo que destraba el email transaccional: sin verificación de dominio,
Resend solo deja enviar a la casilla propia.

- **Plan elegido: arrancar en el free tier de Render, upgradear cuando entre
  el primer gimnasio pago** (decisión del usuario, coincide con "primero se
  cobra, después se sofistica"). El web service se duerme sin tráfico —
  aceptado a propósito, ver `ISSUES.md`.
- **`render.yaml`** define el Blueprint: **solo el web service (free)**. Ya no
  declara `databases:` — la base salió del Blueprint el 2026-07-29 y vive en
  Neon (ver abajo). Tampoco tiene cron: **Render no ofrece plan free para cron
  jobs**, así que los dos trabajos programados corren en GitHub Actions.
- **La base es Neon, no Render** (migrada el 2026-07-29, ver `ISSUES.md`). El
  Postgres free de Render expiraba a los 30 días + 14 de gracia y después
  Render borra los datos; el de Neon es free permanente. `DATABASE_URL` se
  carga a mano en el dashboard de Render (`sync: false`) con la URL **POOLED**.
  La URL **DIRECTA** (sin `-pooler` en el host) se usa solo desde el workflow
  de backup: el pooler no sostiene bien la sesión larga de un `pg_dump`.
  `config/db.py` activa `conn_health_checks=True` — es obligatorio contra Neon,
  que suspende el compute por inactividad (scale-to-zero); sin el chequeo
  Django reusa conexiones muertas del pool de `CONN_MAX_AGE` y los requests
  fallan de forma intermitente.
- **Trabajos programados (GitHub Actions, no Render):**
  - `.github/workflows/generar-pagos.yml` — corre `manage.py generar_pagos`
    todos los días 06:30 UTC. Usa la URL **pooled** (conexiones cortas).
  - `.github/workflows/backup.yml` — `pg_dump` diario a las 06:00 UTC, cifrado
    con GPG y subido al bucket R2 `app-gim-backups`. Usa la URL **directa**.
    El día 1 de cada mes copia también a `monthly/` y **encadena** la
    verificación pasándole el nombre exacto del objeto — no la agenda con un
    cron propio, para que no pueda terminar validando el backup del día
    anterior.
  - `.github/workflows/backup-verify.yml` — baja el objeto, valida checksum,
    descifra y lo **restaura de verdad** en un Postgres descartable. Un backup
    que nunca se restauró no está verificado.
  - Retención: `daily/` 30 días por lifecycle, `monthly/` 12 meses por bucket
    lock. El lock va **solo** en `monthly/`: tiene precedencia sobre la
    lifecycle, así que en `daily/` impediría que la expiración ocurriera nunca.
  - Monitoreo en **Healthchecks.io**: alerta por **ausencia** de ping, que es
    lo único que cubre a la vez el dump fallido, el workflow desactivado por
    inactividad del repo y GitHub Actions caído.
  - **El respaldo usa `pg_dump`, nunca `dumpdata`/`loaddata`** — ver la entrada
    de `ISSUES.md`: `calendario/signals.py` no chequea `raw`, así que un
    `loaddata` sincronizaría cada `Reserva` restaurada contra la API real de
    Google Calendar.
- **Cloudflare R2 — creado y en uso.** Bucket `app-gim-media`, endpoint
  `https://<account_id>.r2.cloudflarestorage.com`. Las 4 credenciales
  (`R2_BUCKET_NAME`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/
  `R2_ENDPOINT_URL`) están en el `.env` local y en el dashboard de Render
  (van marcadas `sync: false` en el Blueprint, así que no se leen del repo —
  verificarlas ahí, no acá).
- **Qué se guarda en R2 y qué no** (pregunta recurrente): R2 guarda SOLO los
  archivos subidos por usuarios, que son exactamente tres campos —
  `Gimnasio.logo` (`logos/`), `PagoMensual.comprobante` (`comprobantes/`) e
  `Importacion.archivo` (`importaciones/`, el `.xlsx` original). **Todo el
  resto de los datos vive en Postgres**: alumnos, rutinas, ejercicios, pagos,
  novedades, turnos/reservas, tokens de Google Calendar, usuarios, y el
  `resultado` JSON de cada importación. Los estáticos (`static/css/app.css`,
  etc.) tampoco van a R2 — los sirve WhiteNoise desde el propio contenedor.
- **Gotcha: con `runserver` en local también se escribe al bucket de
  producción.** Como el `.env` de desarrollo tiene las 4 `R2_*`,
  `STORAGES["default"]` es `S3Storage` también en tu máquina (no existe ni se
  usa `media/`): un logo o un `.xlsx` subido corriendo `runserver` aterriza en
  el MISMO bucket que usa producción. La DB sí está separada (SQLite local vs
  Neon), así que quedan archivos huérfanos sin fila que los referencie —
  molesto pero inofensivo. Si algún día molesta, la salida es un bucket aparte
  para dev (cambiar `R2_BUCKET_NAME` en el `.env` local), no borrar las
  credenciales.
- **Los TESTS sí están aislados de R2** (desde el 2026-07-30; antes no lo
  estaban y habían dejado 816 archivos basura en el bucket, ver `ISSUES.md`).
  `config/settings.py` define `TESTING = "test" in sys.argv` y lo usa en dos
  lados: `PASSWORD_HASHERS` (MD5, para que la suite sea rápida) y
  `STORAGES["default"]` (`InMemoryStorage`). La rama de R2 está guardada con
  `if _r2_seteadas and not TESTING`. **Si agregás un servicio externo nuevo,
  usá `TESTING` para desactivarlo en la suite** — el criterio es que
  `manage.py test` no salga a la red por ningún motivo.
- **Google Calendar (opcional) — credenciales creadas.** Las 4 env vars
  `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`/
  `GOOGLE_OAUTH_REDIRECT_URI`/`GOOGLE_TOKEN_ENCRYPTION_KEY` están en el
  `.env` local (redirect a `http://localhost:8000/calendario/callback/`); en
  Render el redirect tiene que ser el de producción
  (`https://app-gim.onrender.com/calendario/callback/`) y estar dado de alta
  en la consola de Google Cloud. Las 4 o ninguna — `settings.py` revienta al
  arrancar si están parciales; sin ellas la app funciona igual, el alumno
  simplemente no ve la opción de conectar su calendario
  (`GOOGLE_CALENDAR_ENABLED = False`).
- **Estado del respaldo (2026-07-30): operativo y verificado de punta a punta.**
  Secrets cargados, bucket `app-gim-backups` con lifecycle en `daily/` y bucket
  lock en `monthly/`, los dos checks de Healthchecks andando. Verificados con
  evidencia real: backup → restore encadenado (`tablas_esenciales=10`), el
  bucket lock rechazando un borrado en `monthly/` mientras `daily/` lo acepta,
  y la alerta por ausencia llegando por mail al minuto que correspondía.
  **La base vieja de Render ya se borró**: Neon es la única base. El runbook
  completo está en `docs/runbook-respaldos.md`.
- **Lo que sigue pendiente**: (a) **rotar la contraseña de Neon** (quedó
  expuesta en texto plano) y actualizarla en los tres lugares — `DATABASE_URL`
  en Render y los dos secrets de GitHub; ojo que ya no hay base vieja como
  vuelta atrás, así que verificar las tres puntas después; (b) apuntar
  `tugimapp.com`; (c) smoke test manual end-to-end de turnos → Google Calendar
  contra producción.
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
python manage.py test alumnos        # solo una app
python manage.py test alumnos.tests.AlumnoTests.test_creacion_basica_y_str
                                     # un solo test (ruta punteada app.tests.Clase.metodo)
python manage.py createsuperuser     # acceso a /admin/
python manage.py crear_gimnasio --nombre "Gimnasio Central" --email dueno@gmail.com
                                     # imprime una contraseña provisoria; ver
                                     # --sin-password (solo cuando exista Google login)
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
