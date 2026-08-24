# Issues

Registro de problemas, decisiones correctivas y deuda técnica conocida.
No es un tracker de features (eso vive en ROADMAP.md) — es el lugar donde se
anota qué se rompió, qué se corrigió y qué riesgo queda abierto, para no
perder el porqué con el tiempo.

Formato de entrada:

```
## [YYYY-MM-DD] Título corto
**Estado:** abierto | resuelto | aceptado (riesgo asumido a propósito)
**Impacto:** qué se rompe o qué riesgo corre si no se atiende.
**Resolución / próximo paso:** qué se hizo o qué falta hacer.
```

Los errores en runtime quedan además en `logs/app.log` (rotado, no
versionado); este archivo es para el análisis humano posterior, no un mirror
del log.

---

## [2026-07-07] Parte C: `cryptography` no compila con `pip install` a secas (Python 3.14)

**Estado:** resuelto

**Impacto:** al agregar `cryptography` (para cifrar tokens OAuth), `pip install`
intenta compilar el sdist con Rust y falla (no hay toolchain de Rust en el
entorno; venv Python 3.14 x86_64). Bloquea instalar las deps de la Parte C.

**Resolución / próximo paso:** existe wheel binario — instalar con
`pip install --only-binary :all: cryptography` (quedó `cryptography==48.0.1`
pineada en `requirements.txt`). En Render (Linux) hay wheels manylinux, así que
`pip install -r requirements.txt` no compila nada; el gotcha es solo del entorno
local de dev.

## [2026-07-07] Parte C: `calendar.app.created` no escribe en el calendario principal

**Estado:** aceptado (decisión de diseño)

**Impacto:** el scope `calendar.app.created` (el más acotado, recomendado por
Google) NO permite crear eventos en el calendario principal del alumno: solo en
calendarios que la propia app crea. Escribir en el principal exigiría
`calendar.events`, con más fricción de consentimiento/verificación.

**Resolución / próximo paso:** al conectar, la app crea/reutiliza un calendario
secundario "Turnos de {gimnasio}" y vuelca ahí todos los eventos
(`calendario.services.asegurar_calendario_secundario`). Al desconectar se borra
ese calendario. La app del gimnasio sigue siendo la fuente de verdad de los
turnos; Google Calendar es un mirror opcional.

## [2026-07-07] Parte C: sync best-effort e integración apagada por defecto

**Estado:** aceptado (riesgo asumido a propósito)

**Impacto:** la sync con Google es síncrona best-effort (sin outbox/reintentos
en background): si la API de Google falla, el evento queda en
`sync_status=error` y NO se reintenta solo (el alumno puede "Reintentar
sincronización" desde el portal). Además la integración solo se prende si están
las 4 `GOOGLE_*`; si no, degrada al deep-link.

**Resolución / próximo paso:** aceptado para el MVP (coincide con "primero se
cobra"). Si hiciera falta robustez, migrar a un outbox + management command
(mismo patrón que `generar_pagos`). Producción arranca en modo OAuth "Testing"
de Google (hasta 100 usuarios, sin verificación/CASA).

---

## [2026-07-01] Fase 0: el ROADMAP asumía Django en Vektor, pero Vektor es FastAPI

**Estado:** resuelto

**Impacto:** el ROADMAP (Fase 0) instruye extraer el esqueleto reutilizable
"de Vektor" (config Django, `TenantScopedMixin`, managers/querysets con
scoping por tenant, middleware de tenant, fixtures/tests, templates base,
config de producción). Vektor (`~/Desktop/vektor/Vektor/`) es en realidad
FastAPI + SQLAlchemy + Next.js — no tiene nada de eso. Seguir la instrucción
literal habría llevado a scaffolding sobre una base equivocada.

**Resolución:** se confirmó que `~/gestor-pedidos` es el proyecto que
realmente tiene el patrón Django descrito (TenantScopedMixin en
`core/mixins.py`, app `tenants`, templates, admin). El esqueleto de Fase 0 se
extrajo de ahí en su lugar, con confirmación del usuario. Detalle completo en
`REUSO.md`.

## [2026-07-01] Fase 1: `generar_pagos_pendientes` crea PagoMensual con monto=0

**Estado:** aceptado (riesgo asumido a propósito)

**Impacto:** el ROADMAP no define todavía de dónde sale el monto de la cuota
de un alumno (no hay campo de precio en `Alumno` ni un concepto de "plan" con
tarifa). La autogeneración mensual (`pagos.generar_pagos_pendientes`) crea
cada `PagoMensual` PENDIENTE con `monto=0`. Si Fase 2 no completa el monto al
confirmar el pago, quedarían filas de $0 en el sistema.

**Resolución / próximo paso:** aceptado como límite conocido de Fase 1 (solo
modelo de datos). Fase 2 §6 ("Gestión de pagos") debe asegurar que el flujo
de confirmación del staff exija completar `monto` antes de marcar
`estado=PAGADO` — no depender de que el cron lo haya puesto bien. Si más
adelante se agrega un campo de tarifa mensual (a `Alumno` o a un futuro plan),
`generar_pagos_pendientes` debería leerlo de ahí en vez de usar 0 fijo.

**[2026-07-01] Actualización — resuelto en Fase 2:** `ConfirmarPagoForm`
(`pagos/forms.py`) incluye `monto` como campo obligatorio del `ModelForm`
(sin `blank=True` en el modelo); el staff no puede marcar `estado=PAGADO` sin
completarlo. El límite de fondo sigue abierto (no hay tarifa mensual
configurable en `Alumno` todavía) pero el riesgo de filas en $0 sin que nadie
lo note ya no existe: el form lo bloquea.

## [2026-07-01] Fase 2: integración de 5 agentes en paralelo — dos hallazgos

**Estado:** resuelto

**Impacto:** al integrar las vistas de `alumnos`/`ejercicios`/`rutinas`/
`pagos`/`novedades` (cada una construida por un agente distinto, sin acceso a
`config/urls.py` ni `templates/base.html`) aparecieron dos problemas recién
visibles al juntar todo:
1. Cada agente había armado su propio urlconf de prueba (`tests_urlconf.py`,
   `urls_test.py`, o un `urlpatterns` inline en el propio `tests.py`) para
   poder testear vistas antes de que `config/urls.py` incluyera su app. Al
   agregar un nav global en `base.html` con `{% url 'alumnos:listado' %}`
   etc., esos urlconfs de prueba (que solo incluían su propia app + login)
   rompieron con `NoReverseMatch`.
2. Las páginas de listado/detalle usaban `<div class="contenido--ancho">`
   anidado dentro de `<main class="contenido">` (fijo en `base.html`,
   `max-width: 480px`) — el ancho "ancho" quedaba atrapado por el contenedor
   angosto del padre.

**Resolución:** (1) se reemplazó `ROOT_URLCONF` real (`config/urls.py`, ya
con las 5 apps incluidas) en todos los tests, eliminando los urlconfs de
prueba ad-hoc. (2) se agregó `{% block main_class %}` en `base.html` (default
`contenido`) y cada template "ancho" lo sobreescribe a `contenido--ancho`.
Verificado con la suite completa (85/85) y un recorrido manual de punta a
punta (registro → alumno → ejercicio → rutina → asignación → pago →
novedad → dashboard) sin tocar `/admin/`.

## [2026-07-01] Fase 3: se reemplaza magic-link por usuario/contraseña asignado por el staff

**Estado:** aceptado (decisión de producto, no un bug)

**Impacto:** el ROADMAP original decía, en dos lugares ("Cambios en esta
versión" §3 y Fase 3), que el acceso del alumno sería sin contraseña
(magic-link/código), explícitamente para evitar que el dueño gestione
resets de contraseña ("usuario+contraseña = call center de reseteos"). El
dueño del producto pidió lo contrario: que el staff le asigne usuario y
contraseña al alumno directamente.

**Resolución:** se actualizó `ROADMAP.md` (los 6 lugares que mencionaban
magic-link/sin-contraseña) para reflejar la decisión real, en vez de dejar
el documento contradiciendo la implementación. El riesgo original (soporte
de resets) se acepta con este matiz: el reset también lo hace el staff a
mano, cara a cara o por WhatsApp con el alumno — no es un flujo self-serve
remoto, así que el "call center" que motivaba el magic-link no aplica igual
en este contexto (gimnasios chicos, dueño con trato directo). Implementación:
`Alumno.perfil` (OneToOne a `tenants.Perfil`, nullable) vincula el alumno con
su login; el staff crea/resetea la contraseña desde la ficha del alumno;
`fecha_activacion` se registra en el primer login exitoso (señal), no al
crear el acceso — sigue midiendo adopción real, no alta administrativa.

## [2026-07-01] Fase 4: se descartó repartir el rediseño entre 5 agentes en paralelo

**Estado:** resuelto (decisión de alcance, no un problema)

**Impacto:** el plan inicial de Fase 4 era el mismo patrón de Fase 2/3 (un
agente por app de dominio, cada uno reescribiendo sus plantillas a clases
utilitarias de Tailwind). A mitad de camino se encontró un atajo legítimo:
Tailwind v4 permite redefinir clases de componente con `@apply` (`@layer
components`), así que en vez de reescribir el markup de las ~25 plantillas
ya existentes, se redefinieron los MISMOS nombres de clase que ya usaban
(`.tarjeta`, `.boton`, `.badge--ok`, `.tabla`, etc.) en
`static/css/input.css`. Ningún template cambió una sola clase; solo cambió
lo que esa clase significa. Sumado a `hx-boost="true"` en `base.html`
(mejora la navegación de toda la app sin tocar ninguna vista), el 90% del
objetivo de Fase 4 ("no parecer prototipo interno", "pocos clicks") quedó
resuelto sin necesidad de los 5 agentes.

**Resolución:** se canceló el plan de 5 agentes paralelos. El trabajo real
de Fase 4 terminó siendo: el layer de componentes en `input.css`, el rediseño
de `base.html`/`login.html`/`register.html`/`tenants/home.html`, la vista
`GimnasioUpdateView` (faltaba desde Fase 1 — el modelo tenía los campos
white-label pero ninguna UI para editarlos), y una verificación visual en
navegador real (no solo `curl`) para las partes con JS (hx-boost, Alpine,
upload de logo). Si en el futuro alguna app necesita una interacción HTMX
más fina que un boost genérico (p.ej. swap parcial de una fila sin
navegación), evaluarlo puntualmente ahí — no hace falta un rediseño general.

## [2026-07-01] Fase 5: se arranca en el free tier de Render (Postgres expira, sin cron)

**Estado:** aceptado (decisión de producto, riesgo conocido)

**Impacto:** el ROADMAP pide Postgres pago (con backups + PITR) y un Web
Service Starter "siempre prendido". El usuario solo tiene cuenta gratuita de
Render (agregar tarjeta es algo que Claude no puede hacer por políticas de
seguridad — entrar datos de pago de terceros). Arrancando en el plan free:
- **Postgres free expira** y después Render borra la base. **Corrección
  (2026-07-29): el plazo NO es 90 días** como decía esta entrada — Render lo
  bajó a **30 días + 14 de gracia** en mayo de 2024. Ya resuelto: la base se
  migró a Neon, ver `[2026-07-29] Postgres migrado de Render free a Neon free`.
- **El web service free se "duerme"** sin tráfico — el primer request
  después de inactividad tarda más (cold start), y no hay garantía de
  "siempre prendido" para una demo en vivo.
- **Render no ofrece plan free para cron jobs.** La autogeneración mensual
  de pagos (`pagos.generar_pagos_pendientes`/`marcar_vencidos`, expuesta por
  `python manage.py generar_pagos`) NO puede programarse automáticamente en
  el free tier.

**Resolución / próximo paso:** `render.yaml` deja el servicio de cron
comentado con instrucciones de cómo activarlo (cambiar a `plan: starter`).
Mientras tanto, correr `python manage.py generar_pagos` a mano (Shell de
Render) o aceptar que los pagos del mes no se autogeneran solos hasta el
upgrade. Cuando el primer gimnasio pague la seña de setup (Fase 6), es el
momento natural de upgradear Postgres y el web service, y activar el cron.

## [2026-07-01] Fase 5: `input.css` dentro de `static/` rompía `collectstatic`

**Estado:** resuelto

**Impacto:** `static/css/input.css` (la fuente de Tailwind, con
`@import "tailwindcss"` y `@source "../../templates"`) vivía dentro de
`STATICFILES_DIRS`. Al correr `collectstatic` con WhiteNoise, el
post-procesador de WhiteNoise intenta parsear TODO archivo `.css` recolectado
buscando referencias tipo `url(...)`, interpretó el `@import` de Tailwind
como una referencia a un archivo literal llamado `tailwindcss`, y
`collectstatic` falló con `MissingFileError`.

**Resolución:** se movió el archivo fuente a `styles/input.css` (fuera de
`static/`), y se actualizaron `package.json` (`build:css`/`watch:css`) y el
`@source` relativo dentro del propio archivo. Solo `static/css/app.css` (el
output compilado) queda dentro del árbol que Django recolecta.

## [2026-07-01] Fase 5: el manifest de WhiteNoise rompía `{% static %}` en dev/tests

**Estado:** resuelto

**Impacto:** al configurar `STORAGES["staticfiles"]` con
`whitenoise.storage.CompressedManifestStaticFilesStorage` sin condicionarlo,
la suite de tests completa empezó a fallar (33 errores) con
`Missing staticfiles manifest entry for 'css/app.css'`. Esa storage exige un
manifest (`staticfiles.json`) que solo genera `collectstatic` — algo que no
se corre en dev ni en tests, solo en el build de Render.

**Resolución:** el backend de `staticfiles` ahora es condicional a `DEBUG`
(igual criterio que el resto de `config/settings.py`): manifest de WhiteNoise
solo con `DEBUG=False`; en dev/tests usa la `StaticFilesStorage` simple de
Django, que no necesita manifest. Verificado corriendo la suite completa
(105/105) y simulando producción a mano (`DJANGO_DEBUG=False` +
`collectstatic` + resolución de la URL hasheada) antes de commitear.

## [2026-07-06] Reconciliación de turnos sin lock cuando el staff edita horarios

**Estado:** aceptado (riesgo asumido a propósito)

**Impacto:** `reconciliar_reservas_desencajadas()` (`turnos/services.py`),
que reubica o cancela reservas cuando el staff cambia horarios/duración, no
toma `select_for_update()` sobre `ConfiguracionTurnos` como sí hace
`crear_reserva()`. En teoría, un alumno reservando esa misma franja en el
instante exacto en que el staff guarda un cambio de horario podría hacer
que una franja quede momentáneamente por encima de su cupo.

**Resolución / próximo paso:** aceptado sin lock por ahora -- la
reconciliación corre solo cuando el staff edita su propia grilla (poco
frecuente, y es la misma persona activamente cambiando el horario que los
alumnos están reaccionando), y el código anterior (que solo borraba, sin
reubicar) tenía la misma propiedad. Si alguna vez se reporta una
sobre-ocupación real, la solución es barata: `reconciliar_reservas_desencajadas`
ya llama a `obtener_configuracion(gimnasio)` al principio; cambiarlo por
`ConfiguracionTurnos.objects.select_for_update().get(pk=config.pk)` (mismo
patrón que `crear_reserva`) la serializaría contra reservas concurrentes.

## [2026-07-08] Parte C: OAuth de Google fallaba con "Missing code verifier" (PKCE)

**Estado:** resuelto

**Impacto:** al conectar Google Calendar desde el portal del alumno, el
callback fallaba siempre con `InvalidGrantError: (invalid_grant) Missing code
verifier` y mostraba "No se pudo conectar Google Calendar". La conexión era
100% inservible en producción. Detectado probando el flujo real en el
navegador (los tests no lo cubrían, ver abajo).

**Causa raíz:** `google-auth-oauthlib` activa PKCE por defecto.
`build_authorization_url()` llamaba a `flow.authorization_url(...)`, que genera
un `code_verifier` en esa instancia de `Flow` y publica su `code_challenge` en
la URL — pero la función devolvía solo `(url, state)` y **descartaba el
verifier**. En el callback, `intercambiar_code()` creaba un `Flow` nuevo (con
`code_verifier=None`), así que `fetch_token()` no mandaba el verifier y Google
rechazaba el intercambio.

**Resolución:** persistir el `code_verifier` en la sesión junto al `state`.
`build_authorization_url()` ahora devuelve `(url, state, code_verifier)`
(capturado de `flow.code_verifier`); la vista lo guarda en
`session["calendario_oauth_verifier"]`; el callback lo saca de la sesión y lo
pasa a `intercambiar_code(code, state, code_verifier)`, que lo setea en el
`Flow` antes de `fetch_token`. Verificado end-to-end en el navegador (conexión
OK, credencial + calendario secundario "Turnos de {gimnasio}" creados) y con
tests de regresión que ejercitan la costura connect→sesión→callback SIN
mockear `build_authorization_url`/`intercambiar_code` (que es justo lo que
tapaba el bug: los tests viejos mockeaban ambas puntas y nunca probaban que el
verifier viajara por la sesión).

## [2026-07-08] Parte C: el botón "Conectar Google Calendar" no funcionaba bajo hx-boost

**Estado:** resuelto

**Impacto:** el `<a>` "Conectar mi Google Calendar" en `mis_turnos.html` no
hacía nada al clickearlo. El `<body>` tiene `hx-boost="true"`, así que htmx
interceptaba el click y hacía un GET por XHR a `/calendario/conectar/`, que
responde 302 hacia `accounts.google.com`; htmx no puede seguir un redirect
cross-origin por XHR, y el click quedaba tragado (sin error visible).

**Resolución:** `hx-boost="false"` en ese link, para que el navegador haga la
navegación dura y siga el 302 externo. Mismo criterio que ya se usaba para los
forms con upload de archivo (ver CLAUDE.md, sección UI/HTMX). Cubierto con un
test que verifica que el atributo se emite.

## [2026-07-27] Progresión semanal: sin auto-loop tras semana 4, y fallback a semana 1 si la semana actual no tiene items

**Estado:** decisión de diseño + resuelto (hallazgo de code review)

**Impacto/decisión:** `RutinaAsignada.semana_actual` clampea en 4 y no
vuelve a `semana 1` automáticamente pasado el ciclo de `SEMANAS_POR_CICLO`
semanas — es intencional, no un bug: el staff cierra la asignación (`activa
= False`) y crea una nueva cuando el alumno termina el ciclo, mismo patrón
que ya existe para cualquier cambio de rutina. Un loop automático escondería
el fin de ciclo del staff, que es justo el momento en que debería revisar y
ajustar la rutina.

Separado de eso, el code review final detectó que el filtro estricto
`items.filter(semana=rutina_actual.semana_actual)` dejaba la tabla de
ejercicios del portal completamente vacía (sin ningún mensaje) para: (a)
toda `RutinaAsignada` creada antes de este feature, cuyos items quedaron
enteros en `semana=1` (default del campo) por la migración; y (b) una
rutina nueva a la que el staff todavía solo le cargó la semana 1, si el
alumno ya lleva 7+ días con `fecha_inicio` en el pasado. En ambos casos
`semana_actual` calcula ≥2, el filtro no matchea nada, y como clampea en 4
nunca se recupera solo.

**Resolución:** `HomeView._portal_alumno` (`tenants/views.py`) cae a
`items.filter(semana=1)` cuando el filtro por la semana actual no devuelve
nada, preservando compatibilidad hacia atrás con rutinas viejas/parciales
("plantillas viejas siguen funcionando igual, solo viven enteras en semana
1", como dice el spec original). El residual (una rutina realmente sin
ningún item, ni en semana 1) ahora muestra un mensaje `{% empty %}` en vez
de una tabla en blanco. Test de regresión:
`tenants/tests.py::HomeViewAlumnoTests::test_alumno_ve_semana_1_si_semana_actual_no_tiene_items`.

## [2026-07-28] Importador: el invariante de "nunca se acerca a `DATA_UPLOAD_MAX_NUMBER_FIELDS`" no vale para una hoja con ejercicios 100% distintos

**Estado:** aceptado (riesgo asumido a propósito, test ajustado a un escenario realista)

**Impacto:** el spec del importador
(`docs/superpowers/specs/2026-07-27-importador-planes-entrenamiento-design.md`
§2) sostiene que el confirm POST "nunca se acerca al límite de campos sin
importar el tamaño de la planilla" porque manda decisiones por *ejercicio
distinto*, no por fila. El test de regresión de la Tarea 12
(`RegresionCamposDelPostTests`), tal como estaba escrito en el plan (500
filas, cada una con un nombre de ejercicio *distinto* — `f"Ejercicio {i}"`
para las 500), en realidad refuta ese invariante: con 500 ejercicios
100% distintos en una sola hoja, el confirm POST manda ~1508 campos (3 por
ejercicio × 500 + campos del formset de hoja), superando el default de
Django (`DATA_UPLOAD_MAX_NUMBER_FIELDS=1000`) y tirando `TooManyFieldsSent`
(HTTP 400) en vez del 302 esperado. Se reprodujo corriendo el test
verbatim contra el código real (`python manage.py test importaciones`).

**Resolución / próximo paso:** ninguna plantilla real tiene 500 ejercicios
completamente distintos en una sola hoja — una planilla de ese volumen de
filas normalmente repite un vocabulario acotado de ejercicios a lo largo de
varias semanas/días (mismo criterio que el propio ejemplo del spec: "4
semanas × 5 días × 6 ejercicios × 2 hojas ~240 filas"). El test se ajustó
para usar 500 filas que reciclan un pool de 20 ejercicios distintos —
mantiene el volumen de filas (500) que motiva el test, sin caer en el caso
patológico que rompe el invariante que se quiere demostrar. No se tocó
`config/settings.py` (`DATA_UPLOAD_MAX_NUMBER_FIELDS`) porque es un cambio
de superficie de seguridad (límite anti-DoS de Django) fuera del alcance de
la Tarea 12 — si en producción aparece un gimnasio con una biblioteca de
ejercicios genuinamente grande y 100% distinta en una sola hoja de
*plantillas* (no de *biblioteca*, que no pasa por formsets de N
ejercicios), reevaluar subir el límite explícitamente en `settings.py` con
su propio test dedicado.

## [2026-07-28] Importador: biblioteca sí corría el riesgo real que plantillas descartó — resuelto con un campo JSON, no aceptado

**Estado:** resuelto

**Impacto:** la entrada anterior de este mismo día documenta que, para
*plantillas*, el escenario de 500+ ejercicios 100% distintos en una sola
hoja es patológico (no ocurre con datos reales) y se aceptó tal cual. El
flujo de **biblioteca** (`PreviewBibliotecaView`) es distinto: el dueño del
producto confirmó que una carga inicial real puede traer 1000+ ejercicios,
y a diferencia de plantillas, en una biblioteca vacía la mayoría no tiene
forma de auto-resolver `grupo_muscular` (no hay alias de nombre que lo
infiera) — terminan todos en la resolución manual del staff. Con el diseño
original (`ResolucionGrupoMuscularFormSet`, 2 campos de POST por ejercicio
pendiente), el techo real era ~498 ejercicios pendientes antes de superar
`DATA_UPLOAD_MAX_NUMBER_FIELDS=1000` y recibir un `TooManyFieldsSent` (HTTP
400) crudo en vez de un error en español — no un caso patológico, sino el
caso esperado de una primera carga.

**Resolución / próximo paso:** por pedido explícito del dueño del producto
(la opción de mayor alcance, no un parche puntual), el POST de confirmación
de biblioteca ahora manda todas las resoluciones de grupo muscular como un
único campo JSON (`ResolucionesJSONForm.resoluciones`, `importaciones/forms.py`)
en vez de N pares de campos de formset — el conteo de campos del POST queda
constante sin importar cuántos ejercicios pendientes haya, y el límite
relevante pasa a ser `DATA_UPLOAD_MAX_MEMORY_SIZE` (default 2.5MB), que un
JSON de miles de entradas cortas no se acerca a rozar. No se tocó
`DATA_UPLOAD_MAX_NUMBER_FIELDS` (afectaría todas las vistas del proyecto,
no solo esta) ni se construyó un flujo de reintento por lotes.
`ResolucionGrupoMuscularForm`/`FormSet` se eliminaron de `forms.py` (código
muerto tras el cambio). El flujo de *plantillas* (`ResolucionEjercicioFormSet`,
`HojaMetadataFormSet`) no se tocó — ese invariante sigue aceptado tal cual,
ver la entrada de arriba. Test de regresión:
`importaciones/tests.py::RegresionCamposPostBibliotecaTests::test_600_ejercicios_pendientes_no_rompe_el_confirm_post`
(600 ejercicios pendientes, POST real vía test client, confirma 302 y 600
`Ejercicio` creados).

## [2026-07-28] Importador: biblioteca ignoraba el match ambiguo de `rapidfuzz` — creaba duplicados en vez de ofrecer "usar existente"

**Estado:** resuelto

**Impacto:** una revisión final de rama (post Tarea 13) encontró que
`PreviewBibliotecaView` calculaba el match ambiguo (`resolver_nombre`,
`matching.py`, Tarea 5) pero lo trataba igual que un match "nuevo": nunca le
ofrecía al staff la opción de "usar existente". Con "Sentadilla" ya cargada
en la biblioteca, importar "Sentadila" (typo, score ~94 por `WRatio`)
creaba un `Ejercicio` duplicado sin ningún aviso. Esto contradecía el
Global Constraint ("matches ambiguos quedan pre-marcados en 'usar
existente', el staff elige activamente 'crear nuevo' si corresponde"), que
el flujo de *plantillas* sí cumple desde la Tarea 6.

**Resolución:** se agregó la misma decisión de plantillas (usar
existente/crear nuevo, con nombre del candidato y score visibles) sin
reintroducir un formset — la restricción de la Tarea 13 (POST de biblioteca
no puede escalar con la cantidad de ejercicios pendientes) sigue vigente.
La resolución de "accion" viaja en el MISMO campo JSON que ya llevaba
`grupo_muscular` (`ResolucionesJSONForm.resoluciones`), ahora con forma
anidada `{nombre: {"grupo_muscular": str|None, "accion": str|None}}` en vez
de `{nombre: grupo_muscular_str}` — ruptura de payload que obligó a
actualizar dos tests existentes que posteaban el formato viejo (además de
uno tercero, `test_resoluciones_con_grupo_muscular_invalido_no_confirma_y_muestra_error`,
que el plan original no había marcado pero que rompía igual al validar
`isinstance(valor, dict)`). `previsualizar_importacion_biblioteca` ahora
agrega `candidato_nombre` al `match_json` de tipo "ambiguo" (mismo patrón
que ya usaba `previsualizar_importacion_plantillas`). El guard existente de
`confirmar_importacion_biblioteca` (`if not decision["incluir"] or
item["match"]["tipo"] == "exacto": continue`) no necesitó ningún cambio:
una vez que la vista calcula `incluir=False` para un ambiguo resuelto como
"usar_existente", el guard ya lo salteaba correctamente sin crear nada.
Tests nuevos en `ImportacionBibliotecaViewsTests`:
`test_preview_muestra_candidato_y_score_para_match_ambiguo`,
`test_ambiguo_usar_existente_no_crea_ejercicio_nuevo`,
`test_ambiguo_crear_nuevo_requiere_grupo_muscular_y_crea_ejercicio`,
`test_ambiguo_sin_resolver_no_confirma`.

## [2026-07-29] El `.env` local escribe al mismo bucket R2 que producción
**Estado:** aceptado (riesgo asumido a propósito)
**Impacto:** el `.env` de desarrollo tiene las 4 `R2_*` seteadas, así que
`STORAGES["default"]` es `S3Storage` también en local (el directorio `media/`
ni siquiera existe). Un logo o un `.xlsx` subido corriendo `runserver`
aterriza en `app-gim-media`, el MISMO bucket que usa producción. La base de
datos sí está separada (SQLite local vs Postgres de Render), así que esos
archivos quedan huérfanos: nadie los referencia desde prod. No hay riesgo de
pisar un archivo ajeno — `AWS_S3_FILE_OVERWRITE = False` hace que
django-storages sufije los nombres colisionados en vez de sobrescribir.
**Resolución / próximo paso:** se acepta así por ahora (tener R2 activo en
local es justamente lo que permite probar el flujo real de subida sin
desplegar). Si la basura acumulada molesta, la salida es crear un segundo
bucket de dev y cambiar `R2_BUCKET_NAME` en el `.env` local — no borrar las
credenciales, porque eso devuelve el comportamiento a `FileSystemStorage` y
deja de ejercitar el mismo backend que producción.

## [2026-07-29] Postgres migrado de Render free a Neon free
**Estado:** resuelto
**Impacto:** el Postgres free de Render expira a los 30 días (+14 de gracia) y
después Render borra los datos. Sin migración ni respaldo, la pérdida era
cuestión de tiempo.
**Resolución / próximo paso:** inventario previo de la base de Render
(`auth_user` 1, `tenants_gimnasio` 1, `tenants_perfil` 1,
`turnos_configuracionturnos` 1, `django_session` 2, y todo lo operativo
—alumnos, reservas, pagos, rutinas, ejercicios, importaciones— en 0). Como NO
estaba vacía, se migró con `pg_dump | psql` (ruta 2b del plan, no `migrate`
desde cero: recrear el gimnasio a mano habría perdido su configuración
white-label y la referencia al logo ya subido a R2). Verificado antes del
cutover: los 29 conteos coinciden, `migrate --check` da exit 0 contra Neon, y
el `Gimnasio` restauró con slug/colores/tipografía y su `logo` apuntando a
`logos/8_1sasa11.jpg` (el archivo que ya estaba en el bucket). Recién después
se cambió `DATABASE_URL` en el dashboard de Render a la URL **POOLED**;
confirmado que producción entra por el pooler (aparece `pgbouncer` en
`pg_stat_activity` de Neon). `render.yaml` ya no declara `databases:` —
sacarlo del Blueprint no borra el recurso, Render conserva lo existente hasta
que se lo elimina a mano. **Actualización 2026-07-30: la base vieja de Render se
borró.** Se mantuvo viva como vuelta atrás hasta completar la verificación
end-to-end del respaldo (backup + restore encadenado + alerta + bucket lock);
con eso cerrado, Neon quedó como única base.
**Nota:** el único usuario de la base NO es superusuario, así que hoy nadie
entra a `/admin/`. Operativamente no molesta (el sistema se usa entero desde
el panel web, por diseño), pero hay que saberlo.

---

## [2026-07-29] El respaldo usa `pg_dump`, nunca `dumpdata`/`loaddata`
**Estado:** aceptado (decisión de diseño que no hay que revertir)
**Impacto:** `dumpdata`/`loaddata` parece la opción "más Django" y es la que
alguien va a proponer al tocar el workflow de backup. Sería un error con
consecuencias visibles para el usuario final: `calendario/signals.py` **no
chequea el flag `raw`**, que es justamente el que Django activa durante un
`loaddata` para avisar "esto es una carga de fixtures, no una operación real".
Sin ese chequeo, restaurar un backup dispararía la sincronización de **cada
`Reserva` restaurada contra la API real de Google Calendar** — creando cientos
de eventos duplicados en los calendarios de los alumnos, y encima con una
restauración que tardaría muchísimo o directamente fallaría por rate limit.
`pg_dump` opera a nivel de base y no ejecuta código de Python, así que no
puede disparar ningún signal.
**Resolución / próximo paso:** `.github/workflows/backup.yml` y
`backup-verify.yml` usan `pg_dump --format=custom` y `pg_restore`. La
advertencia estaba solo en un comentario de cabecera del workflow y en el
spec; se registra acá porque es donde se busca. Si algún día se quiere que
`loaddata` sea seguro, el arreglo es agregar `if raw: return` al principio del
receiver de `calendario/signals.py` — pero no hay motivo para hacerlo.

---

## [2026-07-29] Se cerró el registro público de gimnasios
**Estado:** resuelto
**Impacto:** `/accounts/register/` era una ruta **pública y sin throttling**:
cualquiera en internet podía crear User + Gimnasio + Perfil STAFF y quedaba
logueado automáticamente (`login()` al final de `RegisterView.form_valid`). El
form no pedía email ni verificaba nada, así que además de permitir cuentas
basura en masa, un dueño que perdía su contraseña **no tenía ninguna forma de
recuperarla** salvo `manage.py changepassword` desde el servidor.
**Resolución / próximo paso:** se borraron `RegisterView`, `RegistroForm`, su
template y su ruta. El alta pasa a `python manage.py crear_gimnasio --nombre
... --email ...` (`tenants/services.py::crear_gimnasio`). Es coherente con la
etapa del producto (se buscan los primeros tres gimnasios pagos, no
autoservicio masivo) y con el principio "primero se cobra, después se
sofistica". `_slug_disponible` se mudó de la vista a
`tenants/services.py::slug_disponible` antes de borrarla.
**Estado transitorio de la contraseña:** el destino de la cuenta staff es no
tener contraseña (`set_unusable_password()`, que además la deja fuera del reset
por mail porque `PasswordResetForm.get_users()` filtra por
`has_usable_password()`). Pero el login con Google es el Frente C y todavía NO
existe: con `set_unusable_password()` como default, un gimnasio recién dado de
alta **no podría entrar de ninguna forma**. Por eso el comando genera hoy una
contraseña provisoria y la imprime; `--sin-password` implementa el modo
definitivo y `--password` permite elegirla. Cuando Google esté verificado
contra producción, `sin_password` pasa a ser el default y el parámetro se
borra.
**Riesgo que queda abierto:** el dueño existente sigue entrando con su
contraseña de siempre. No hay que correr la migración que inutiliza las
contraseñas de staff hasta que el login con Google esté verificado **contra
producción**.

---

## [2026-07-30] La suite de tests escribía en el bucket R2 de producción
**Estado:** resuelto
**Impacto:** el `.env` de desarrollo tiene las 4 `R2_*`, y `config/settings.py`
elegía `S3Storage` con solo mirar si estaban seteadas — sin distinguir si
estaba corriendo `manage.py test`. Como `importaciones/tests.py` sube `.xlsx`
de verdad (`SimpleUploadedFile` → `Importacion.archivo`), **cada corrida de la
suite dejaba ~20 archivos huérfanos en el bucket REAL `app-gim-media`**, bajo
`importaciones/`. Al detectarlo había **816 objetos basura** acumulados (~4 MB)
contra 1 solo archivo legítimo (`logos/8_1sasa11.jpg`). Además de ensuciar
producción, cada upload era un round-trip de red dentro de un test: la suite
tardaba **65 s** por eso.
**Resolución / próximo paso:** `config/settings.py` calcula `TESTING = "test"
in sys.argv` una sola vez (ya se usaba para `PASSWORD_HASHERS`) y ahora también
lo usa para el storage: en tests el backend es `InMemoryStorage`, y la rama de
R2 quedó guardada con `if R2_ENABLED and not TESTING` (la variable se
llamaba `_r2_seteadas` al momento de este cambio; renombrada a `R2_ENABLED`
el 2026-08-20 al extraer un helper compartido para las 5 integraciones que
ya seguían este mismo patrón todo-o-nada). La validación de
"las 4 o ninguna" **sigue corriendo siempre** — una config parcial es un error
de entorno en cualquier contexto. La suite bajó de 65 s a **7,2 s** (453 tests)
y verificado contra el bucket: 817 objetos antes de correrla, 817 después.
Regresión cubierta por `config/tests.py::StorageDeTestsAisladoTests`.
**Nota:** esto es distinto del issue del 2026-07-29 ("el `.env` local escribe
al mismo bucket que producción"), que sigue vigente y aceptado: corriendo
`runserver` en local, un upload real sigue yendo al bucket de producción. Lo
que se arregló acá es solo el caso de los tests, que era el que generaba
volumen.
**Limpieza:** los 816 objetos acumulados en `app-gim-media/importaciones/` se
borraron el 2026-07-30, con el prefijo acotado a `importaciones/` y un assert
previo de que ninguna clave cayera fuera de él. Era seguro porque ninguno tenía
una fila de `Importacion` que lo referenciara: se generaron corriendo la suite
contra la SQLite local, y el inventario previo a la migración a Neon confirma
`importaciones` en 0. Después de la limpieza el bucket quedó con **1 solo
objeto**, `logos/8_1sasa11.jpg`, y la landing que lo usa sigue respondiendo
200.

---

## [2026-07-30] Se descartó guardar las contraseñas de los alumnos para mostrárselas al staff
**Estado:** aceptado (decisión de diseño que NO hay que revertir)
**Impacto:** el pedido original era que el staff tuviera "los usuarios y
contraseñas de todos los alumnos" visibles en una sección de la web. Django
guarda hashes, así que cumplirlo exige guardar las contraseñas en claro (o
cifradas y descifrables) en paralelo. Eso convierte la cuenta del dueño en un
**depósito de credenciales**: hoy un robo de esa cuenta expone los datos del
gimnasio; con el vault, expondría las contraseñas reales de cada alumno — y
como la gente reusa contraseñas, también sus mails y sus bancos. Es el único
cambio evaluado en todo el proyecto que **empeoraría** el escenario de hackeo
que motivó el pedido.
**Resolución:** se planteó el problema y el dueño del producto aceptó la
alternativa, que cubre mejor el objetivo real (que el staff pueda entrar a
cualquier cuenta sin depender de la memoria del alumno): **suplantación**
("Entrar como este alumno", `tenants/suplantacion.py`) + **regeneración** de
contraseña, que se muestra una sola vez. El staff conserva control total y la
base no guarda ninguna contraseña legible.
**Si alguien lo vuelve a pedir:** el pedido es razonable y la necesidad es
real; lo que hay que ofrecer es suplantar, no guardar. Antes de reabrir, leer
`docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md`.

---

## [2026-07-30] El identificador del alumno es único en TODA la plataforma, no por gimnasio
**Estado:** aceptado (riesgo asumido a propósito)
**Impacto:** el alumno entra con su email o su teléfono, y eso va a
`User.username`, que es único global. Dos casos reales colisionan: la misma
persona entrenando en dos gimnasios, y un mail o teléfono familiar compartido
entre hermanos. El segundo alta falla.
**Por qué no tiene arreglo limpio:** con **una sola pantalla de login sin
selección de gimnasio**, el identificador TIENE que ser globalmente único.
Resolver `(identificador, gimnasio)` exigiría que el login supiera el gimnasio,
o sea subdominios o slug en la URL — que viola el principio no negociable #6.
Namespacing (`gim-slug:email`) obligaría al alumno a tipear eso. Un username
opaco mueve el problema sin resolverlo.
**Resolución / próximo paso:** se hace visible en vez de esconderlo: el form
avisa y ofrece el otro canal ("si pusiste el email, cargá el teléfono"). **El
mensaje es deliberadamente genérico**, sin confirmar que ese email ya existe:
cuando los usuarios eran inventados eso era irrelevante, pero ahora son emails
reales y un mensaje específico convertiría el form en un enumerador de usuarios
de toda la plataforma. Si algún día hay subdominios por gimnasio, esto se puede
revisar.

---

## [2026-07-30] Una suplantación abandonada queda con `finalizada_en` en NULL
**Estado:** aceptado (riesgo asumido a propósito)
**Impacto:** si el staff cierra la pestaña sin apretar "Volver a mi cuenta",
`RegistroSuplantacion.finalizada_en` queda `NULL` para siempre. La auditoría
registra cuándo empezó pero no cuándo terminó, así que no se puede calcular la
duración real de esas sesiones.
**Por qué se acepta:** cerrar el registro al vencimiento exigiría un job
periódico o un middleware que revise cada request — infraestructura nueva para
un dato que hoy nadie consulta. El `creado` alcanza para lo que la auditoría
tiene que responder: quién entró a la cuenta de quién y cuándo. La sesión en sí
no queda abierta indefinidamente: `MAX_DURACION` son 2 h y `vencida()` lo
expone.
**Cómo cerrarlo si hiciera falta:** un `update()` sobre los registros sin
finalizar cuya `creado` supere `MAX_DURACION`, corrido desde el mismo workflow
de GitHub Actions que ya ejecuta `generar_pagos`.

---

## [2026-07-30] Revisión del Frente B: el espejo estado↔acceso valía en 1 de 3 caminos
**Estado:** resuelto
**Impacto:** la revisión de código de la rama encontró que la sincronización
entre `Alumno.estado` y `User.is_active` estaba implementada **solo** en
`AlumnoToggleEstadoView`. Los otros dos caminos que escriben el estado no la
hacían, con dos síntomas opuestos y ambos malos:
1. `crear_acceso()` creaba el `User` con `is_active=True` sin mirar el estado,
   así que **un alumno dado de baja al que se le crea el acceso después podía
   entrar** — exactamente el criterio de salida que el frente venía a cumplir.
2. `estado` es un campo editable de `AlumnoForm`, así que reactivar a un alumno
   desde la ficha lo dejaba en `estado=activo` + `is_active=False`: **el alumno
   no podía entrar y nadie podía diagnosticarlo**, porque el listado decía
   "activo" y el panel de accesos decía "dado de baja".
**Resolución:** la invariante se movió de las vistas a **un solo punto**,
`alumnos/signals.py::sincronizar_acceso_con_estado` (`post_save` sobre
`Alumno`), que cubre además el admin, el shell y cualquier código futuro. La
vista del toggle ya no sincroniza nada. El receiver chequea `raw` para no
repetir el problema de `calendario/signals.py`.
**Lección de proceso:** poner una invariante en las vistas garantiza que
alguna se olvide. Si un dato tiene que mantenerse consistente con otro, el
lugar es el modelo o una señal, no cada llamador.

---

## [2026-07-30] `login()` de Django no valida `is_active`
**Estado:** resuelto
**Impacto:** `suplantacion.iniciar()` chequeaba `Alumno.estado` pero no
`User.is_active`, y `django.contrib.auth.login()` **no valida `is_active`** (a
diferencia de `authenticate()`). Con un usuario desactivado, la suplantación
"funcionaba": el POST devolvía 302, la sesión quedaba como el alumno, y en el
request siguiente `ModelBackend.get_user()` devolvía `AnonymousUser`. El staff
perdía su sesión **y no podía ni usar "Volver a mi cuenta"**, porque esa vista
exige estar logueado. Encima el `RegistroSuplantacion` quedaba sin cerrar.
**Resolución:** guard explícito de `usuario.is_active` en `iniciar()`. El
template ya escondía el botón para alumnos dados de baja, pero eso es
cosmético: el endpoint POST se puede llamar igual.

---

## [2026-07-30] `MAX_DURACION` de la suplantación era código muerto
**Estado:** resuelto
**Impacto:** `vencida()` existía pero **no se llamaba desde ningún lado**, así
que el límite de 2 horas no se aplicaba nunca. Peor: `CLAUDE.md` e `ISSUES.md`
afirmaban que sí. Documentar un control de seguridad que no corre es peor que
no tenerlo — quien lea la documentación asume que está cubierto y no lo
verifica.
**Resolución:** se implementó en vez de borrarlo, vía
`tenants/middleware.py::ExpirarSuplantacionMiddleware`. Es el único middleware
propio del proyecto y la excepción está justificada: la expiración tiene que
evaluarse en CADA request, y un mixin dejaría afuera cualquier vista que no lo
use (`HomeView`, por ejemplo, solo lleva `LoginRequiredMixin`). Si falla el
retorno, descarta la sesión entera en vez de dejar al staff dentro de la
cuenta del alumno.

---

## [2026-07-30] El `select_for_update()` de `crear_acceso` no está ejercitado por la suite
**Estado:** aceptado (limitación conocida de la cobertura)
**Impacto:** `crear_acceso()` toma `select_for_update()` sobre el `Alumno` para
serializar altas concurrentes. **En SQLite eso es un no-op silencioso**:
`sqlite3.DatabaseFeatures.has_select_for_update` es `False` y Django
simplemente omite el `FOR UPDATE` sin avisar. Como la suite corre contra
SQLite, **ningún test ejercita el lock**; el test de concurrencia solo prueba
la traducción de `IntegrityError` a `IdentificadorEnUso`, que sí funciona en
las dos bases.
**Por qué se acepta:** el mismo límite aplica a `turnos/services.py::crear_reserva`
e `importaciones/services.py`, que usan el patrón desde hace meses. Correr la
suite contra Postgres para cubrirlo cambiaría el tiempo de la suite (hoy ~8 s)
y la simplicidad del entorno de dev, a cambio de cubrir un caso que en
producción está bien resuelto.
**Qué NO asumir:** que "los tests pasan" prueba que el lock funciona. Si se
toca esa función, el razonamiento sobre concurrencia hay que hacerlo a mano.

---

## [2026-07-30] `post_save` no cubre `update()` ni `bulk_update`
**Estado:** aceptado (límite documentado)
**Impacto:** la sincronización `Alumno.estado` → `User.is_active` vive en un
receiver de `post_save`, que Django **no dispara** con
`Alumno.objects.filter(...).update(estado=...)` ni con `bulk_update()`. Hoy no
hay ninguna llamada así en el repo (verificado), pero `CLAUDE.md` afirma que la
sincronización vale para "cualquier código futuro" y eso no es cierto por esa
vía.
**Resolución / próximo paso:** anotado en el docstring del receiver. Si algún
día hace falta un cambio de estado masivo (p.ej. dar de baja a todos los
morosos), tiene que sincronizar los `User` a mano o iterar con `save()`.

---

## [2026-08-14] Sugerencia de paisaje por logo: distancia RGB simple, no Lab/CIEDE2000
**Estado:** aceptado (simplificación a propósito)
**Impacto:** `tenants/paisaje_matching.py::sugerir_paisaje()` extrae el color
dominante del logo (ignorando fondo blanco/negro/transparente) y elige el
paisaje curado de `Gimnasio.PALETAS` cuyo `primario` está más cerca en
distancia euclidiana sobre RGB crudo. RGB no es perceptualmente uniforme: dos
colores a la misma distancia euclidiana pueden verse más o menos parecidos
según la zona del espacio de color, así que en un catálogo con muchas más
opciones esto podría elegir mal más seguido de lo que un ojo humano elegiría.
**Por qué se acepta:** el catálogo tiene solo 4 candidatos (Bosque/Océano/
Arena/Pizarra), muy separados entre sí en tono — con tan pocas opciones la
distancia RGB simple alcanza, y evita sumar una dependencia nueva
(`colormath`/`scikit-image`) solo para convertir a Lab/OKLCH. Es sugerencia,
no una decisión final: el dueño ve el paisaje preseleccionado en el preview
en vivo de `gimnasio_form.html` y lo cambia a mano antes de guardar si no le
cierra.
**Qué NO asumir:** que la sugerencia es "la mejor" paleta en términos
perceptuales — es la más cercana en RGB entre 4 opciones ya harmonizadas, no
un análisis de color profesional.

---

## [2026-08-14] Una URL firmada de R2 dentro de `<style>` se rompe con el autoescape
**Estado:** resuelto
**Impacto:** el modo "imagen propia" de `Gimnasio.fondo_tipo` inyecta
`url("{{ gimnasio.fondo_imagen.url }}")` en el bloque `<style>` de
`base.html`/`landing.html`. En producción R2 va con `AWS_QUERYSTRING_AUTH`
(`config/settings.py`), así que `.url` devuelve una URL **presignada** con
query string; el autoescape de Django convierte cada `&` en `&amp;`, y como
`<style>` es un elemento *raw text* el navegador no decodifica entidades ahí
adentro. La firma viajaba rota y R2 respondía 403: el dueño subía su imagen,
la veía bien en el preview, y el fondo real quedaba en el tinte plano.
**Resolución:** `|safe` sobre `.url` en las dos plantillas (seguro: el nombre
de archivo ya pasó por `get_valid_filename()` del storage, que no deja
comillas ni `<`). En el `<script>` del preview, la misma URL viaja por
`json_script`, no por interpolación cruda. Es el mismo gotcha que ya estaba
documentado para `tipografia_css_family`.
**Qué NO asumir:** que la suite lo cubre. Los tests usan `InMemoryStorage`,
cuyas URLs (`/media/fondos/x.png`) no tienen query string, así que el bug era
invisible para toda la suite. El test de regresión
(`test_url_firmada_de_r2_no_sale_html_escapada`) tiene que **simular** la URL
firmada a mano.

---

## [2026-08-14] Un `--` en un comentario XML rompe el SVG entero, sin ningún síntoma
**Estado:** resuelto (con test)
**Impacto:** al escribir los 4 doodles curados usé `--` como guion dentro del
comentario de cabecera. `--` es ilegal dentro de un comentario XML: invalida
el documento completo, el SVG no parsea, la `mask-image` no carga y el
elemento queda enmascarado **a nada**. El fondo desapareció por completo en
las tres superficies. No emite ningún error: ni en consola, ni en los logs, ni
en los tests — una máscara que no carga simplemente no dibuja.
**Resolución:** guiones em (`—`) en los comentarios, y `DoodlesAssetsTests`,
que parsea los 4 SVG como XML y verifica que cada opción de
`Gimnasio.Doodle.choices` tenga su archivo. Verificado que falla al
reinyectar el `--`.
**Qué NO asumir:** que "los tests pasan" prueba que un asset se ve. Antes de
este test, los 4 SVG podían estar rotos y la suite seguía en verde; hizo falta
mirarlo en un navegador para descubrirlo.

---

## [2026-08-14] La máscara de un doodle se come el borde del elemento
**Estado:** resuelto
**Impacto:** dos síntomas de la misma causa. (1) En las miniaturas del
selector, `mask-image` estaba aplicada al elemento mismo, así que enmascaraba
también su `border` y su `background`: el swatch perdía el recuadro y quedaban
figuras flotando. (2) En `.landing` y en la ventana del preview, el
`::before` con `z-index: -1` quedaba TAPADO por el `background-color` opaco
del contenedor.
**Resolución:** la tinta del doodle va siempre en un pseudo-elemento, y el
contenedor que tenga fondo propio lleva `isolation: isolate` (documentado en
`DESIGN.md` § The Atmospheric Canvas Rule). `body` es la excepción: su fondo
se propaga al canvas y pinta primero, así que no lo necesita.
**Qué NO asumir:** que lo que funciona en `body` funciona igual dentro de un
div. Las reglas de propagación de fondo del `body` al canvas son un caso
especial de la especificación, no el comportamiento normal.

---

## [2026-08-15] La grilla de turnos de 14 días se leía como "el mismo calendario duplicado"
**Estado:** resuelto
**Impacto:** `MisTurnosView` (alumno) y `AgendaView` (staff) mostraban 14
días (2 semanas) desde el lunes de la semana actual en una grilla
`lg:grid-cols-7`. Con el mismo patrón semanal repetido (mismos horarios,
mismo orden de días) y la primera fila casi entera en gris
(`.turno--pasado`, por ser la semana en curso ya transcurrida en parte), el
usuario lo percibía como "veo el mismo calendario dos veces, uno gris no
clickeable y otro funcional" — no un bug de renderizado (era una sola
grilla en el HTML), sino un efecto de layout. El criterio de 14 días se
había copiado de `MisTurnosView` a `AgendaView` por consistencia visual, sin
ninguna razón de negocio propia (confirmado con `git log`, sin tests ni
entradas de este archivo que lo exigieran).
**Resolución:** `SemanaNavegableMixin` (`turnos/views.py`) pagina la grilla
semana por semana vía `?semana=<offset>` (0 = actual), con `dias=7` fijo y
navegación "← Semana anterior / Esta semana / Semana siguiente →" en las dos
vistas. Una sola fila de 7 columnas por vez, sin el efecto "duplicado".
**Qué NO asumir:** que alcanza con cambiar el día de inicio (ej. "desde hoy"
en vez de "desde el lunes"). Con `dias=14` fijo, cualquier punto de partida
sigue armando 2 filas de 7 con el mismo patrón semanal — el fix de fondo es
mostrar una sola semana, no correr la ventana.

---

## [2026-08-15] El botón "Reservar" se desbordaba y tapaba la tarjeta del día siguiente
**Estado:** resuelto
**Impacto:** `.turno` (`styles/input.css`) era un `flex items-center
justify-between` horizontal sin `flex-wrap`. En una columna angosta (7 por
semana), el botón "Reservar" no se achica (mínimo de flexbox = contenido sin
wrap) y se desbordaba tapando visualmente la tarjeta de horario del día de
al lado.
**Resolución:** `.turno` pasa a `flex flex-col items-stretch` (hora/cupo
arriba, botón o badge de estado debajo, ancho completo de la tarjeta) con
`.turno form, .turno .boton { width: 100% }`.
**Qué NO asumir:** que reducir a una sola semana (ver entrada anterior)
alcanza para resolver esto también. Las columnas siguen siendo angostas en
pantallas chicas con 7 columnas de una fila; hace falta el fix de CSS
igual.

---

## [2026-08-19] PWA + Web Push: riesgos aceptados a propósito

**Estado:** aceptado (riesgo asumido a propósito)

**Impacto:** al agregar la app `notificaciones` (PWA instalable + push, ver
`CLAUDE.md`) se tomaron varias simplificaciones de MVP en vez de resolverlas
de punta a punta. Cada una es independiente:

1. **Íconos del manifest sin `purpose: maskable`.** El logo de cada
   gimnasio es blanco-etiquetado y no hay garantía de que tenga la zona de
   seguridad (80% central) que exige un ícono maskable — declararlo así
   igual podría recortar mal un logo real. Se dejó solo `purpose: "any"`.
   Si algún launcher de Android empieza a verse mal, revisar acá primero.
2. **Placeholder de ícono sin logo usa la fuente default de Pillow**
   (`ImageFont.load_default`), no una tipografía cuidada — un gimnasio sin
   logo subido ve iniciales con una fuente genérica en vez de algo
   prolijo. Sale barato de arreglar (empaquetar una fuente en `static/
   fonts/` y pasarla a `ImageFont.truetype`) si algún gimnasio se queja.
3. **El cron de recordatorios (`enviar-recordatorios.yml`) corre cada 15
   min vía `schedule` de GitHub Actions, que NO garantiza puntualidad
   exacta al minuto** (mismo límite ya documentado para `backup.yml`) —
   "avisá 1 hora antes de tu turno" puede llegar con algunos minutos de
   margen de más o de menos. No hay Celery/Redis/worker en el proyecto
   (ver CLAUDE.md, sección Deploy) así que no hay una alternativa más fina
   sin sumar infraestructura nueva.
4. **El service worker (`static/js/sw.js`) no tiene fallback offline** —
   sin conexión, la PWA simplemente no carga nada (no hay pantalla "estás
   offline" ni cache de la shell de la app). Decisión YAGNI: el producto
   no tiene ningún caso de uso que dependa de funcionar sin internet.
5. **Web Push en iOS requiere Safari 16.4+.** Un alumno con una versión de
   iOS más vieja no va a poder activar notificaciones — el botón
   simplemente no hace nada (feature-detected en `pwa.js`, no rompe). No
   es un bug de esta implementación, es un límite de la plataforma; no
   vale la pena invertir en un fallback nativo (FCM/APNs) solo para cubrir
   ese segmento, dado el principio "primero se cobra, después se
   sofistica".
6. **Todos los gimnasios comparten origin y `/sw.js`** (no hay subdominios
   por gimnasio en el MVP, principio no negociable #6). Cada manifest tiene
   un `id` distinto (`/g/<slug>/`), que es el mecanismo estándar de la spec
   de Web App Manifest para que el navegador trate instalaciones del mismo
   origin como apps independientes — pero si una misma persona es staff o
   alumno en dos gimnasios distintos e instala las dos PWAs en el mismo
   dispositivo, comparten el mismo service worker registrado a nivel
   origin. No es un problema funcional (el SW no hace nada tenant-specific,
   nunca cachea HTML), pero el comportamiento de instalación/actualización
   en el launcher del SO puede variar según el navegador. Solucionarlo del
   todo requeriría subdominios por gimnasio, fuera de alcance del MVP.

**Resolución / próximo paso:** ninguno de los 6 puntos bloquea el uso real
de la feature. Reevaluar (1) y (2) si algún gimnasio pagos lo señala como
problema visual concreto; reevaluar (3) solo si el producto crece al punto
de justificar Celery/Redis por otras razones también; (6) solo sería un
problema real si aparece un caso concreto de una persona con doble rol en
dos gimnasios pagos distintos queriendo las dos apps instaladas.

---

## [2026-08-24] Login con Google: el warning "app no verificada" es de Google, no un bug de código

**Estado:** aceptado (mientras no se complete la verificación de Google)

**Impacto:** un cliente real reportó ver el warning propio de Google ("Esta
app no está verificada") al intentar loguearse con su cuenta de Gmail.
`tenants/google_login.py`/`GoogleLoginCallbackView` no tienen ningún mensaje
que diga literalmente "no verificado" — el más cercano es el genérico "No se
pudo verificar tu cuenta de Google. Probá de nuevo." de un `except Exception`
amplio, y ni siquiera ese llega a ejecutarse en este caso: el warning aparece
en la propia pantalla de `accounts.google.com`, ANTES de que el código de la
app reciba nada del callback. Causa: el consent screen de Google Cloud sigue
en modo **"Testing"** (tope de 100 usuarios, todos dados de alta a mano en la
lista de "Test users" — cualquier cuenta que no esté ahí ve el warning). Ya
documentado una vez para Google Calendar en la entrada `[2026-07-07] Parte C:
sync best-effort e integración apagada por defecto` ("Producción arranca en
modo OAuth 'Testing' de Google (hasta 100 usuarios, sin verificación/CASA)").
Login con Google para staff (Frente C, ver CLAUDE.md) reusa el MISMO Client
ID/Secret que Calendar con una redirect URI propia — hereda el mismo estado
de publishing del proyecto de Google Cloud, así que el mismo síntoma que ya
se sabía posible para Calendar aparece ahora también en login, con un cliente
real afectado.

**Resolución / próximo paso:**
1. **Inmediata:** agregar el email de Gmail del cliente afectado a la lista
   de "Test users" del consent screen en Google Cloud Console.
2. **De fondo, para no repetir este paso manual con cada cliente nuevo:**
   completar la verificación de Google para pasar el consent screen a "In
   production". Los scopes de login (`GOOGLE_LOGIN_SCOPES = ["openid",
   "email"]`, `config/settings.py`) son no sensibles, así que debería ser una
   verificación estándar (dominio + branding del consent screen), no la
   revisión pesada que exigen scopes sensibles como `calendar.app.created`.

**Qué NO asumir:** que esto se arregla tocando código. El warning lo emite
Google en su propia pantalla, antes de que `GoogleLoginCallbackView` reciba
el `code` — no hay mensaje de error de la app que interceptar ni corregir
para este caso puntual.
