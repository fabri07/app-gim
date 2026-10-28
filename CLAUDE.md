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

**Fase actual: código de Fases 0-6 completo en `main` y desplegado** (la tienda está en `main`: Hito 1, venta con transferencia, desplegado; el Mercado Pago de la tienda, Hito 2, mergeado el 2026-10-18 y prendido en producción desde el 2026-10-21, sin ningún pago real probado todavía; el add-on se cobra a USD 20/mes sumado a la suscripción sólo si `tienda_habilitada`, desde el próximo pago; ver `ARQUITECTURA.md` «Tienda del gimnasio»; desde el 2026-10-20 el alumno también puede pagar la cuota por Mercado Pago a la misma cuenta del gimnasio, con la misma bandera, ver «Cuotas por Mercado Pago»). La app
vive en `https://www.tugimapp.com` (Render, free tier, dominio propio desde
el 2026-08-19) y el bucket de
Cloudflare R2 (`app-gim-media`) ya está creado y en uso — los pasos manuales
de Fase 5 que dependían de cuentas de terceros están hechos. Ver "Deploy
(Fase 5)" más abajo para el estado exacto y lo que sigue pendiente. Fases 0-4
(esqueleto, modelos, vistas de staff, portal del alumno, UX/white-label)
completas — un dueño puede usar el sistema de punta a punta desde el panel
web sin tocar `/admin/`. Además del scope original del ROADMAP ya están
mergeadas: agenda de turnos/reservas con cupos, read-receipts de novedades,
medios de cobro configurables, una integración opcional con Google
Calendar por alumno (ver "Turnos, reservas y Google Calendar" más abajo),
un importador de planes/ejercicios desde Excel (ver "Importador de Excel
(Proyecto 2)" más abajo), el cobro por ciclos de 28 días con bloqueo de
acceso al alumno que no paga (ver "Bloqueo de acceso por falta de pago" más
abajo), y un panel de plataforma para el dueño del producto —superadmin—
que monitorea, factura y congela gimnasios (ver "Panel de plataforma" más
abajo), el control de ingreso por molinete con QR o lector facial (ver
"Control de ingreso" más abajo; sin hardware todavía, lo construido es el
contrato HTTP y las pantallas), y el sitio público en `/` con el embudo de
solicitudes de acceso (ver "Sitio público y embudo de solicitudes" más
abajo) — el ROADMAP.md no las documenta todavía como fases propias, viven en
`ISSUES.md` y en los mensajes de commit ("Fase 6, Task N", "Parte A/B/C",
"Proyecto 2, Task N").

**Nota:** el acceso del alumno NO es magic-link como decía la primera versión
del ROADMAP — el dueño del producto pidió que el staff asigne usuario y
contraseña directamente. Ver `ISSUES.md` (2026-07-01) y `ROADMAP.md` Fase 3,
ya actualizados.

## Principios no negociables (resumen — el detalle está en ROADMAP.md)

1. Una sola app, múltiples gimnasios. Nunca se copia el repo por gimnasio.
2. La app es la fuente de verdad; nada de sync en vivo con Excel.
3. Pagos simples: sin Mercado Pago ni integraciones financieras en el MVP.
   Los pendientes del mes se autogeneran por cron; el dueño confirma.
   **Matiz desde el 2026-09-29:** Mercado Pago entró para que el gimnasio le
   pague la suscripción a la plataforma (`suscripcion/`, ver "Cobro online de
   la suscripción"). Para la tienda (`tienda/`) **existe** (Hito 2, a la
   cuenta MP del propio gimnasio por OAuth, sólo para ventas de la tienda)
   y está **prendido en producción desde el 2026-10-21** (4 variables, ver
   "Mercado Pago de la tienda" en `ARQUITECTURA.md`; sin ellas cobra sólo por
   transferencia). **Ningún pago real se probó todavía:** con usuarios de
   prueba no se puede, porque la aplicación de MP de la plataforma es de una
   cuenta real y MP rechaza la mezcla real/prueba (`ISSUES.md` 2026-10-24).
   El primer gimnasio real que cobre es la prueba. **Cuotas (desde el
   2026-10-20):** el dueño del producto
   levantó el 2026-10-19 la regla «las cuotas no se cobran por MP». Cualquier
   gimnasio (con o sin el add-on) conecta su cuenta MP por OAuth y el alumno
   paga la cuota con un link; la plata va a la cuenta del GIMNASIO, nunca a
   la de la plataforma. Misma bandera `TIENDA_MP_ENABLED` más el
   interruptor `cuotas_con_mercadopago` de «Mi
   gimnasio». El staff sigue confirmando a mano lo que se paga por otro
   medio. El débito automático sigue afuera.
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

## El detalle vive en `ARQUITECTURA.md`

Este archivo es el resumen que se carga en cada sesión. El diseño completo de
cada app (por qué está armada así, trampas que ya mordieron, tests que fijan
cada decisión) está en **`ARQUITECTURA.md`**, que NO se carga solo: **antes de
tocar un área, leé su sección** (`grep -n '^## ' ARQUITECTURA.md` da el
índice). Las referencias viejas «ver `CLAUDE.md` § X» en código, `ISSUES.md`,
`ROADMAP.md` y specs apuntan a la sección homónima de `ARQUITECTURA.md`.

| Si vas a tocar… | Sección de `ARQUITECTURA.md` |
|---|---|
| Alumnos, rutinas, cuotas/ciclos de 28 días, novedades, qué app es cada cosa | Apps de dominio (incluye «Bloqueo de acceso por falta de pago») |
| Editar rutina asignada, propagación entre semanas, `vigente_de` | Editar la rutina asignada y el panel «Cómo viene el alumno» |
| Dashboard / analítica del staff | Indicadores temporales del panel · Público del gimnasio · Vistas de staff (Fase 2) |
| Cuenta demo, `sembrar_demo`, `restaurar_demo` | Datos de demostración · Cuenta de demostración compartida |
| Exportar datos del gimnasio | Exportador de datos |
| Superadmin, facturación a gimnasios, `estado_cuenta`, middleware | Panel de plataforma |
| Mercado Pago, `suscripcion/`, webhook | Cobro online de la suscripción |
| Molinete, QR, lectores, API `/ingreso/v1/` | Control de ingreso |
| Tienda (add-on), productos, pedidos, stock | Tienda del gimnasio (`tienda/`) |
| Moderación de la tienda (cola, denuncias, fotos sin aprobar) | Tienda del gimnasio → «Moderación» |
| Mercado Pago de la tienda (OAuth, tokens, acreditar, webhook, «Revisar MP», prueba guiada) | Tienda del gimnasio → «Mercado Pago de la tienda» · `docs/mercadopago-tienda.md` |
| Cuotas por Mercado Pago (precio, botón, acreditar, pantalla del staff, reconciliar) | Cuotas por Mercado Pago · Apps de dominio → `pagos` · `docs/mercadopago-tienda.md` Parte 9 |
| Botones de borrar | Borrar: `core/borrado.py` |
| Aviso de plan por vencer | Aviso de plan por vencer |
| Editor de plantillas, errores de formulario | Un formulario que rechaza sin que se note… (incluye «El editor de plantillas») |
| Pantalla del día del alumno en celular | El día del alumno en el celular |
| Login, accesos de alumnos, suplantación, Google, reset | Portal del alumno y acceso · Accesos, revocación y suplantación · Login por gimnasio… · Login con Google · "Olvidé mi contraseña" |
| Turnos, reservas, Google Calendar | Turnos, reservas y Google Calendar |
| Categorías de ejercicio, importador Excel/PDF | Categorías de ejercicio por gimnasio · Importador de Excel (Proyecto 2) |
| Importar alumnos desde Excel/CSV, «Sin fecha de inicio» | Importador de Excel (Proyecto 2) → «Importar alumnos» · Apps de dominio → `pagos` |
| Acciones en lote del listado de alumnos (fijar fecha, baja, reactivar, códigos) | Acciones en lote del listado de alumnos |
| CSS, Tailwind, paletas, tipografía, htmx | UI y white-label (Fase 4) |
| Portada `/`, `/solicitar/`, landing `g/<slug>/`, privacidad | Sitio público y embudo de solicitudes · Landing pública · Política de privacidad… |
| PWA, push, cron de recordatorios | PWA instalable + Web Push |
| Tour de onboarding | Tour de bienvenida para staff nuevo |
| Render, Neon, R2, Cloudflare, crons de GitHub Actions, backups | Deploy (Fase 5) |

## Reglas transversales (las que más mordieron)

El porqué de cada una está en `ARQUITECTURA.md`; acá va solo la regla.

- **Un `TenantOwnedModel` nuevo va a TRES lugares** en el mismo commit:
  `tenants/demo.py::vaciar_gimnasio`, el fixture `_ensuciar` de
  `tenants/tests.py`, y `HOJAS`/`EXCLUIDOS` de `tenants/exportacion.py`. Un
  dato de la PLATAFORMA sobre un gimnasio (lo escribe sólo el superadmin) NO
  es `TenantOwnedModel` y no va a ninguno.
- **`hx-boost="true"` global**: todo link/form que redirija cross-origin
  (Google, Mercado Pago), suba archivos, descargue, dependa de
  `{% block extra_style %}` (vive en `<head>`, htmx no lo reemplaza) o
  devuelva un secreto lleva `hx-boost="false"`. Datos por-request van DENTRO
  de `<body>`, nunca como atributo de `<body>`. Un 4xx bajo boost es un click
  mudo: los carteles responden 200.
- **Un alumno importado sin ancla no se cobra ni se bloquea**: `fecha_inicio_ciclo = None` es
  un estado legítimo («Sin fecha de inicio»). Mover el ancla de un alumno con cuotas está
  prohibido también en lote (el `UPDATE` lleva `~Exists(Cuota…)`), y baja/reactivación en
  lote van con `save(update_fields=...)` por alumno, nunca `update()`.
- **Fechas del dominio con `timezone.localdate()`**, nunca
  `timezone.now().date()` (UTC-3). Tests de esto: reloj congelado, fecha lejana.
- **Nada de N+1**: el costo en queries depende del catálogo, nunca de la
  cantidad de filas; contadores del panel son UNA query agregada; anotaciones
  multivaluadas como `Subquery`. Los tests de escala hacen crecer lo que de
  verdad multiplica el costo.
- **`QuerySet.update()` no dispara `auto_now`**: `modificado` explícito cuando
  importa, y sin tocarlo en `Gimnasio` (versiona logo/fondo/ícono PWA).
  `save(update_fields=...)` sobre `Alumno.estado` lista también `fecha_baja`
  y `fecha_inicio_ciclo`.
- **Formularios**: cada campo con su línea de error en el mismo commit;
  `.errorlist` no tiene estilo.
- **Templates**: `{# #}` es de una sola línea; para varias,
  `{% comment %}`. Si tocás `styles/input.css`, `npm run build:css` antes de
  commitear.
- **SQLite no muestra los modos de falla de Postgres** (largos de `varchar`,
  `select_for_update`, aritmética de fechas): validar largos en Python, nunca
  `fecha + columna` en el queryset.
- **La suite no sale a la red** (`TESTING` apaga R2, push, Sentry, Mercado
  Pago). Un servicio externo nuevo se apaga igual.
- **Toda llamada a `crear_preferencia`/`consultar_pago`/`buscar_pagos` en
  `tienda/` y `pagos/` lleva `token=` del gimnasio** (sin token sale el de la
  plataforma); lo vigila `pagos/tests_token_del_gimnasio.py`.
- **Verificar en el navegador**: si un `.js`/`.css` no cambia, sospechá del
  service worker (cache-first de `/static/`) y de un `runserver` viejo.
- **Nunca reintroducir registro self-service** de gimnasios ni de alumnos;
  las contraseñas nunca se guardan legibles y no pasan por `messages`.

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
                                     # imprime una contraseña provisoria; con
                                     # --sin-password el dueño entra por Google
python manage.py generar_pagos       # autogenera pendientes del ciclo + vence atrasados
python manage.py expirar_solicitudes # cierra los leads que nunca verificaron el email
python manage.py reconciliar_cobros  # acredita cobros online cuyo webhook se perdió
                                     # (falla con CommandError sin MERCADOPAGO_*)
python manage.py reconciliar_cuotas_mp  # acredita cuotas pagadas por MP cuyo webhook se perdió
python manage.py vencer_pedidos      # vence pedidos de la tienda sin comprobante + avisos
python manage.py avisar_cobros       # mails de «por vencer/vence hoy/vencido» al staff
                                     # (idem; la 1ra corrida avisa a todos los ya vencidos)
python manage.py sembrar_demo --gimnasio <slug>    # datos de muestra (ver su sección)
python manage.py restaurar_demo --gimnasio <slug>  # vacía y resiembra la cuenta demo
python manage.py exportar_gimnasio --gimnasio <slug> --salida datos.zip
                                     # mismo ZIP que el botón de "Mi gimnasio", sin timeout
python manage.py purgar_ingresos --gimnasio <slug> --meses 24 --dry-run
                                     # retención de ingresos; NUNCA antes de exportar
                                     # a un gimnasio que se va (ver "Control de ingreso")
python manage.py collectstatic       # solo hace falta simulando producción (DEBUG=False)

npm install                          # una vez, para compilar Tailwind
npm run build:css                    # compila styles/input.css -> static/css/app.css
                                     # (el prebuild copia la fuente auto-hospedada)
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
- **Sentry** (opcional, `SENTRY_DSN`): agrupa y alerta sobre excepciones de
  producción en tiempo real, algo que `logs/app.log` no hace (hay que ir a
  buscarlo). Sin `SENTRY_DSN` el SDK ni se inicializa (`SENTRY_ENABLED` en
  `config/settings.py`) — la app funciona igual, simplemente sin ese canal.
  Va apagado en la suite a propósito (`and not TESTING`, mismo criterio que
  R2/Web Push): `manage.py test` no debe salir a la red. El link "Errores
  (Sentry)" del panel de plataforma (`plataforma:inicio`) solo aparece si
  además se carga `SENTRY_PANEL_URL` (la URL del proyecto en sentry.io) —
  es cosmético, el SDK no la usa.

## Qué NO construir todavía

App nativa, débitos automáticos, chat interno, nutrición,
métricas deportivas, IA de rutinas, subdominios por gimnasio. Ver ROADMAP.md
§ "Fase 9" para la lista completa y cuándo sí tiene sentido evaluarlas (solo
con clientes pagos).

El QR de ingreso y el control de asistencia salieron de esta lista el
2026-09-28, cuando varios establecimientos se los pidieron al dueño del
producto (ver "Control de ingreso"). El modo kiosco de esa feature sigue sin construirse.

Mercado Pago salió de esta lista el 2026-09-29, **acotado**: sólo para el cobro
de la suscripción de la plataforma al gimnasio (ver "Cobro online de la
suscripción") y la tienda (`tienda/`; el MP de la tienda, Hito 2, prendido en producción desde el 2026-10-21). El 2026-10-19 salió también
para el cobro del gimnasio a sus alumnos (`pagos`, «Cuotas por Mercado Pago»,
a la cuenta OAuth del gimnasio). El débito automático sigue acá en todos los
casos.
