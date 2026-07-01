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

**Fase actual: Fase 3 completa** (portal del alumno). Ver `REUSO.md` para el
detalle de qué se copió de dónde en Fase 0. Un dueño puede usar el sistema de
punta a punta desde el panel web (registro → alumnos → ejercicios → rutinas →
asignación → pagos → novedades → dashboard → crear acceso de alumno) sin
tocar `/admin/`, y el alumno entra con ese usuario/contraseña a ver su propia
rutina, cuota del mes y novedades. Próximo paso: Fase 4 (UX/UI y white-label
— Tailwind + HTMX + Alpine).

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
- SQLite en dev, Postgres en producción (Fase 5).
- Fase 4 en adelante: HTMX + Tailwind CSS + Alpine.js. **Nada de React/Next**
  en el MVP — Fase 0 usa HTML mínimo sin esas libs todavía (llegan en Fase 4).
- Deploy objetivo: Render (Fase 5) + Cloudflare R2 para media.

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

## Apps de dominio (Fase 1)

- **`alumnos`** — `Alumno(TenantOwnedModel)`.
- **`ejercicios`** — `Ejercicio(TenantOwnedModel)`, biblioteca por gimnasio
  (no global; ver docstring del módulo).
- **`rutinas`** — `RutinaPlantilla`/`RutinaPlantillaItem` (editable) y
  `RutinaAsignada`/`RutinaAsignadaItem` (snapshot congelado). La copia se
  hace con `RutinaAsignada.crear_desde_plantilla(...)` y
  `RutinaPlantilla.duplicar()` — ambas transaccionales. Los modelos "Item" NO
  son `TenantOwnedModel` (se acceden vía su padre, que ya está scopeado).
- **`pagos`** — `PagoMensual(TenantOwnedModel)`. `pagos/models.py` expone
  `generar_pagos_pendientes(mes, anio)` y `marcar_vencidos(mes, anio)`;
  `python manage.py generar_pagos` corre ambas para el mes actual — es el
  comando que Fase 5 programa como Render Cron Job.
- **`novedades`** — `Novedad(TenantOwnedModel)` con `NovedadQuerySet.visibles()`
  (activa + publicada + no vencida).

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
