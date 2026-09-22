# Panel de plataforma (superadmin): monitor, facturación, congelar, actividad, Sentry

## Contexto

El dueño del producto hoy administra los gimnasios desde la Shell de Render y `/admin/`. No hay
forma de ver de un vistazo cuántos alumnos tiene cada gimnasio, cuándo le corresponde pagar a la
plataforma, si está usando la app, ni de congelar una cuenta que no paga. Dos hallazgos de la
exploración que condicionan todo:

- **`Gimnasio.activo=False` no congela nada.** Solo da 404 en `/g/<slug>/…`. Staff y alumnos
  siguen entrando por `/accounts/login/`, `marcar_vencidos` y `enviar_recordatorios` lo ignoran.
- **Un `User` superuser sin `Perfil` recibe 403 en `/`** (`tenants/views.py::HomeView`) y una
  interfaz sin nav. Su único camino útil es `/admin/`.

### Decisiones cerradas con el usuario

| Tema | Decisión |
|---|---|
| Precio | USD por alumnos **ACTIVOS**: ≤100 → 10, 101–300 → 15, >300 → 20. **Asunción a confirmar: 100 exactos paga 10, 300 exactos paga 15** |
| Ciclo | Cada 30 días desde el alta, **primeros 30 días gratis** (primer cobro vence el día 30) |
| Cambio | USD y pesos al **dólar MEP** (`https://dolarapi.com/v1/dolares/bolsa`, campo `venta`), automático, cache 24 h; si falla, solo USD |
| Pagos | Los registra a mano el superadmin |
| Congelar | Dos niveles **manuales**: «Bloquear alumnos» → «Suspender cuenta» → «Restaurar». Datos intactos |
| Tráfico | Actividad por gimnasio desde la base: **usuarios activos por día** (staff/alumno) y último uso. No pageviews |
| Errores | **Sentry** (plan gratis), link en el panel |
| Alta | Crear gimnasio desde el panel (reusa `tenants.services.crear_gimnasio`) |

### Revisión adversarial del plan (2026-09-22): fallas corregidas acá

1. La query «única» del monitor con tres joins multivaluados explotaba filas → **una `Subquery` por anotación**.
2. Faltaba avisar ANTES de vencer → estado **`POR_VENCER`** (7 días) y tarjeta ámbar «Para cobrar esta semana».
3. «Actividad» por `user_logged_in` no mide uso en una PWA con sesión de 2 semanas → **día activo por usuario desde el middleware**, dedupe en sesión.
4. `estado_cuenta` sin fecha → **`estado_cuenta_desde`**.
5. Superuser con Perfil no veía el link a Plataforma → link por `is_superuser`, fuera del `elif`.
6. KPI «gimnasios activos» contaba demo y verificación → se cuenta `facturacion_aplica`; ingreso esperado en USD y ARS.
7. `push_suscribir`/`push_desuscribir` a la allowlist (devuelven JSON). Privacidad menciona el registro de actividad. Orden de entrega: Fases 1 y 2 primero.

## Arquitectura

- **App nueva `plataforma`**, última en `INSTALLED_APPS` (lee todo el dominio). Rutas bajo
  `/plataforma/`, namespace `plataforma:`.
- **`SuperadminRequiredMixin`** (`plataforma/mixins.py`): `LoginRequiredMixin` + `is_superuser and is_active`, si no 403.
- **Campos de plataforma sobre `Gimnasio`** (mismo criterio que `es_demo`/`exportacion_habilitada`,
  fuera de `GimnasioForm.Meta.fields`): `estado_cuenta`, `estado_cuenta_desde`, `facturacion_inicio`,
  `facturacion_exenta`. `activo` queda como está, documentado como «oculto/retirado, no bloquea».
- **Modelos propios** en `plataforma/models.py`, `models.Model` y **NO `TenantOwnedModel`** (son datos
  DE la plataforma SOBRE el gimnasio): `PagoPlataforma` y `ActividadDiaria`. Por eso no entran en
  `vaciar_gimnasio`, `_ensuciar` ni `HOJAS`/`EXCLUIDOS`, y los tests de introspección
  (`tenants/tests.py::_modelos_tenant_owned`, `tests_exportacion.py`) no los ven. Docstring explícito.
- **Un solo middleware** `plataforma/middleware.py::PlataformaMiddleware` en `process_view` (ahí existe
  `resolver_match`), después de `ExpirarSuplantacionMiddleware`. Hace dos cosas con la MISMA resolución
  de `user.perfil.gimnasio`: (a) bloqueo por `estado_cuenta`, (b) registro del día activo. Salta anónimos,
  superuser y suplantación (`suplantacion.esta_activa(request)`). Cartel con **200, no 403** (bajo
  `hx-boost` un 4xx es un click muerto). 0 queries netas para la lectura: `base.html` y `TenantScopedMixin`
  ya resuelven `user.perfil.gimnasio` y el ORM lo cachea en la instancia.
- **Precios puros** en `plataforma/precios.py` (Django-free, `SimpleTestCase`); ORM en
  `plataforma/facturacion.py` y `plataforma/actividad.py` (una query agregada por indicador).
- **Cotización** en `plataforma/cambio.py` con `urllib` (no hay `requests` en requirements), timeout
  3 s, cache default (`LocMemCache`, 1 worker), TTL 24 h, fallo cacheado 10 min, `if settings.TESTING: return None`.
  Nunca lanza. `fechaActualizacion` viene en UTC → mostrar con `localtime`.

## Fases (cada una mergeable sola). Orden de entrega: 1 → 2 → 3 → 4 → 5, Sentry (0) cuando convenga

### Fase 0 — Sentry
- `requirements.txt`: `sentry-sdk[django]==<2.x>`. **Verificar primero** que instala en Python 3.14.3.
- `config/settings.py`: `SENTRY_DSN`, `SENTRY_PANEL_URL` opcionales; `SENTRY_ENABLED = bool(SENTRY_DSN) and not TESTING`;
  `sentry_sdk.init(dsn, send_default_pii=False, traces_sample_rate=0.0, environment=...)`.
- `render.yaml` (`sync: false`, o falla `config/tests.py::BlueprintDeclaraLoQueSettingsLeeTests`), `.env.example`, CLAUDE.md § Canales de auditoría.
- Test: `SENTRY_ENABLED` es `False` en la suite.

### Fase 1 — Esqueleto, redirect del superuser, monitor de solo lectura, precios
- Crear `plataforma/{__init__,apps,urls,views,mixins,precios,facturacion,tests,tests_precios}.py`,
  `templates/plataforma/{inicio,gimnasio_detalle}.html`. `config/urls.py`: `path("plataforma/", include(...))`.
- `precios.py`: `DIAS_CICLO=30`, `DIAS_GRATIS=30`, `DIAS_AVISO_COBRO=7`, `ESCALONES=((100,10),(300,15),(None,20))`,
  `precio_usd(n)`, `escalon_de(n)`, `fin_de_prueba(inicio)`, `periodo_siguiente(inicio, cubierto_hasta)`
  (30 días, `hasta` INCLUSIVO como `Cuota.periodo_fin`), `proximo_vencimiento(inicio, hoy, cubierto_hasta)`,
  `EstadoPago` (EXENTA / PRUEBA / AL_DIA / **POR_VENCER** / VENCIDA), `estado_pago(...)`, `dias_de_atraso(...)`.
  POR_VENCER = vence entre hoy y hoy+7 (incluye el fin de la prueba). VENCIDA = vencimiento < hoy.
- `facturacion.py::gimnasios_anotados()` — **una query con `Subquery` por anotación, nunca joins multivaluados juntos**:
  `alumnos_activos = Subquery(Alumno.filter(gimnasio=OuterRef("pk"), estado=ACTIVO).order_by().values("gimnasio").annotate(c=Count("id")).values("c"))`,
  `ultimo_uso_staff` (Fase 4: `Max(fecha)` de `ActividadDiaria` rol staff; hasta entonces `Max(last_login)` por Subquery),
  `cubierto_hasta` (Fase 2). `filas_del_monitor(hoy)` calcula precio/estado en Python por fila; `kpis(filas)`:
  clientes (`facturacion_aplica`), alumnos activos totales, ingreso mensual esperado USD (y ARS si hay cotización),
  gimnasios vencidos, gimnasios por vencer. `para_cobrar(filas)` = POR_VENCER + VENCIDA, ordenados por vencimiento.
- `InicioView`: tarjeta `.aviso-urgente` «Para cobrar esta semana» (molde `planes_por_vencer` de `home.html`),
  KPIs con `.metrica`, tabla `.tabla` en `.tabla-scroll` ordenada por próximo vencimiento, badges `.badge--*`
  (VENCIDA riesgo, POR_VENCER alerta, AL_DIA ok, PRUEBA/EXENTA neutro), link a Sentry si `SENTRY_PANEL_URL` (`hx-boost="false"`).
  `GimnasioDetalleView` (`DetailView` sobre `Gimnasio`).
- `tenants/views.py::HomeView.get()`: sin Perfil y `is_superuser` → `redirect("plataforma:inicio")`; el 403 actual queda para el no-superuser.
- `templates/base.html`: link «Plataforma» **siempre que `user.is_superuser`** (dentro de la nav de staff si tiene Perfil, o en una `<nav class="nav-staff">` propia con Plataforma y `/admin/` `hx-boost="false"` si no). Sin `:class` de Alpine en la nav propia.
- Tests: bordes 100/101/300/301; PRUEBA día 0 y 22 (→ POR_VENCER si faltan ≤7 al fin de prueba), VENCIDA día 31, AL_DIA con pago, POR_VENCER 5 días antes; superuser → 302; superuser CON Perfil → home normal y link a Plataforma; staff y alumno → 403 en `/plataforma/`; **mismas queries con 2 gimnasios de 3 alumnos que con 12 gimnasios de 40 alumnos**; `alumnos_activos` no cuenta inactivos ni otro gimnasio; fecha de alta en hora LOCAL (congelar reloj 22:00 UTC-3).

### Fase 2 — Facturación
- Migración `tenants`: `facturacion_inicio = DateField(null, blank)` («vacío = fecha de alta»), `facturacion_exenta = BooleanField(default=False)`; property `Gimnasio.facturacion_aplica = not (facturacion_exenta or es_demo)`.
- `crear_gimnasio(es_demo=True)` → `facturacion_exenta=True`. `tenants/demo.py::_gimnasio_canonico()` **NO** incluye los campos nuevos (usa `setattr`+`save()` → los preserva; test).
- `plataforma/models.py::PagoPlataforma(TimeStampedModel)`: `gimnasio` FK PROTECT `related_name="pagos_plataforma"`, `fecha_pago`, `monto_usd`, `monto_ars` (null), `tipo_cambio` (null), `alumnos_activos`, `periodo_desde`, `periodo_hasta` (inclusivo), `notas`, `registrado_por` FK PROTECT. `CheckConstraint(hasta >= desde)`, index `(gimnasio, periodo_hasta)`.
- `cambio.py`: `URL=.../dolares/bolsa`, `cotizacion_dolar()` → `{compra, venta, fecha(local), fuente}` o `None`; `pesos(usd, cot)`.
- `forms.py`: `PagoPlataformaForm` (sin `gimnasio`/`registrado_por`; `clean` de período), `FacturacionForm`. Campo por campo con su línea de error (regla `.errorlist`).
- Vistas: `PagoPlataformaCreateView` (`get_initial` precarga alumnos activos, `precio_usd`, `venta`, ARS, `periodo_siguiente`, hoy), `FacturacionUpdateView`. Detalle: tarjeta «Facturación» + tabla de pagos. `plataforma/admin.py` registra `PagoPlataforma`; `GimnasioAdmin` suma `facturacion_exenta`.
- `gimnasios_anotados()` suma `cubierto_hasta = Subquery(Max periodo_hasta)`.
- Tests: `GimnasioForm` no expone los campos; FK-injection de `gimnasio`; período inválido → 200 sin fila; estados con fechas; `cambio` con `_descargar` parcheado a `URLError` → `None` y fallo cacheado; bajo `TESTING` no llama `_descargar`; **costo constante con N pagos por gimnasio**.

### Fase 3 — Congelar: `estado_cuenta`, middleware, push
- Migración `tenants`: `estado_cuenta = CharField(choices=EstadoCuenta: NORMAL/ALUMNOS_BLOQUEADOS/SUSPENDIDA, default=NORMAL)`, `estado_cuenta_desde = DateTimeField(null, blank)`; constantes `ESTADOS_SIN_ACCESO_ALUMNO`, `ESTADOS_SIN_ACCESO_STAFF`; help text de `activo` actualizado.
- `PlataformaMiddleware.process_view` (parte de bloqueo): salta anónimos y superuser; allowlist por `resolver_match`:
  apps `admin`, `plataforma`; url names `logout`, `login`, `login_gimnasio`, `suplantacion_volver`, `pwa_service_worker`, `pwa_manifest`, `pwa_icono`, `pwa_icono_maskable`, `push_suscribir`, `push_desuscribir`, `logo_gimnasio`, `fondo_gimnasio`, `landing_gimnasio`, `politica_privacidad` (`tenants/urls.py` no tiene `app_name`, así que los nombres van pelados; `notificaciones` sí: `notificaciones:pwa_*`). Sin Perfil → deja pasar. Bloqueado = SUSPENDIDA, o rol alumno con ALUMNOS_BLOQUEADOS. GET → `render("plataforma/cuenta_bloqueada.html", status=200)`; no-GET → `redirect("home")` (el GET de home renderiza el cartel, nunca redirige → sin loop).
- `templates/plataforma/cuenta_bloqueada.html`: rama alumno («la cuenta de {{ gimnasio.nombre }} está suspendida, contactá al gimnasio» + `partials/redes_sociales.html`), rama staff SUSPENDIDA (`SOPORTE_CONTACTO`). Sin links a secciones. Decisión: el staff suspendido tampoco exporta desde la app; el superadmin usa `manage.py exportar_gimnasio`.
- Context processor `plataforma/context_processors.py::estado_cuenta` → `cuenta_alumnos_bloqueados`, `cuenta_suspendida`, `SOPORTE_CONTACTO` (cortar en `admin`). `base.html`: `.banner-atraso` (ámbar, copiar `.banner-suplantacion`) si alumnos bloqueados; ocultar `nav-staff` si suspendida. `npm run build:css` y commitear `static/css/app.css`.
- `notificaciones/services.py`: `notificar_a_usuario` excluye `gimnasio__estado_cuenta__in=ESTADOS_SIN_ACCESO_ALUMNO`; `notificar_a_gimnasio` corta si SUSPENDIDA, o si `rol=ALUMNO` y alumnos bloqueados. Consecuencia aceptada: `_ya_notificado` marca antes → el aviso silenciado no se reenvía al restaurar (documentar).
- Crons de cuotas **no cambian** (test explícito).
- Acciones: `EstadoCuentaView` en `gimnasios/<pk>/estado/<estado>/` (GET confirmación con molde `templates/core/confirmar_borrado.html` para bloquear/suspender; POST aplica `Gimnasio.objects.filter(pk).update(estado_cuenta=..., estado_cuenta_desde=now())` para no tocar `modificado`; «Restaurar» POST directo, `estado_cuenta_desde=None`; estado inválido → 404). `ExportacionToggleView` POST invierte `exportacion_habilitada` con `update()`. Detalle muestra «Suspendida desde …».
- Tests (`plataforma/tests_bloqueo.py`): alumno bloqueado → 200 cartel en `/` y `rutinas:mi_dia`, POST `item_calificar` → 302 sin escribir; staff entra con banner; SUSPENDIDA → staff cartel sin nav, POST a `alumnos:crear` → 302 sin crear; logout y `suplantacion_volver` funcionan; `sw.js`/manifest/íconos/push 200; `/plataforma/` y `/admin/` no pasan por el bloqueo; **mismas queries en GET `/` de un staff NORMAL con y sin el middleware** (descontando el INSERT de actividad de Fase 4); push: alumno bloqueado no recibe, staff sí con ALUMNOS_BLOQUEADOS y no con SUSPENDIDA (parchear `webpush`, `TransactionTestCase`, `override_settings(PUSH_ENABLED=True)`); `generar_pagos` sigue emitiendo con SUSPENDIDA; `restaurar_demo` preserva `estado_cuenta`; `estado_cuenta_desde` se setea y se limpia.
- **Riesgo de deploy**: `enviar-recordatorios.yml` corre cada 15 min con checkout de `main` sin `migrate` → una corrida en rojo posible entre merge y deploy. Mergear y verificar el deploy enseguida.

### Fase 4 — Actividad (`ActividadDiaria`)
- `plataforma/models.py::ActividadDiaria`: `usuario` FK **CASCADE** (los `User` de alumnos demo se borran en `vaciar_gimnasio` y su historial se va solo), `gimnasio` FK PROTECT `related_name="actividad"`, `rol`, `fecha` (DateField). `UniqueConstraint(usuario, fecha)`, index `(gimnasio, fecha)`.
- **Registro desde `PlataformaMiddleware`** (misma resolución de perfil, sin señal de login): `hoy = localdate()`; si `request.session.get("plataforma_actividad") != hoy.isoformat()` → `ActividadDiaria.objects.bulk_create([...], ignore_conflicts=True)` y guardar la fecha en la sesión. Un INSERT por usuario por día; la sesión (en base) se escribe solo cuando cambia el día. Se salta si `suplantacion.esta_activa(request)` (el staff mirando como alumno no es uso del alumno) y para superuser. Corre ANTES del bloqueo (un bloqueado que intenta entrar también es información).
- `actividad.py::activos_por_dia(gimnasio, dias=30, hoy)`: una query `values("fecha","rol")` + `Count`, rellena 30 días × 2 roles (fila por día aunque vacío, molde `analitica.asistencia_diaria`). `ultimo_uso(gimnasio)` con `aggregate` + `filter=Q`.
- Monitor: `ultimo_uso_staff` pasa a `Subquery(Max(fecha) rol=staff)`; detalle muestra staff y alumnos.
- Detalle: tarjeta con `<canvas>`, `json_script`, Chart.js CDN **dentro del `{% block content %}`** (no en `extra_style`, por `hx-boost`), IIFE con `datosDe()` copiado de `home.html`, barras apiladas azul/gris, `<details class="tabla-detalle">`.
- `templates/tenants/privacidad.html`: sumar «registramos qué días usás la app, para que el gimnasio y la plataforma sepan si el servicio se usa».
- Tests: primer GET del día de un alumno crea UNA fila y el segundo GET no vuelve a escribir (contar queries del segundo: iguales a sin middleware); cambio de día (congelar reloj) crea otra; staff → rol staff; superuser → nada; suplantar → nada para el alumno; un GET a las 23:30 local cae en el día local; 30 filas siempre; una query con 3 y con 60 filas; `vaciar_gimnasio` sobre la demo no revienta.

### Fase 5 — Crear gimnasio desde el panel
- `CrearGimnasioForm(forms.Form)`: `nombre`, `email` (→ `normalizar_email`), `slug` opcional, `es_demo`, `sin_password`.
- `GimnasioCrearView(FormView)` en `gimnasios/nuevo/`: `form_valid` llama `crear_gimnasio` con `try/except ValidationError → add_error(None)`; en éxito **renderiza** `gimnasio_creado.html` con `never_cache` y la contraseña una sola vez (molde `alumnos/views.py::_render_credenciales` + `acceso_credenciales.html`), **sin redirect ni `messages`** (la sesión vive en la base). Botón «Nuevo gimnasio» en `inicio.html`.
- Tests: crea `Gimnasio`+`User`+`Perfil` STAFF, 200 con contraseña y `no-store`, la contraseña loguea; email duplicado → 200 con error sin crear; `es_demo` → exenta; staff → 403.

### Transversal (al cerrar)
- CLAUDE.md: sección «Panel de plataforma» (campos, por qué los modelos no son tenant-owned, middleware 200/allowlist/0 queries netas + 1 INSERT diario, filtro de push y dedup, precios, cotización, contraseña una vez). ISSUES.md: «`activo` no bloqueaba nada» y «`facturacion_inicio` a mano para gimnasios existentes (Vida Plena); `gimnasio-verificacion-r2` marcado exento».
- Cargar en Render: `SENTRY_DSN`, `SENTRY_PANEL_URL`, `SOPORTE_CONTACTO`. Crear el superuser en la Shell (`createsuperuser`).
- Templates nuevos: `{% comment %}` multilínea (los barre `ComentariosDeTemplateTests`).

## Verificación end-to-end
1. `python manage.py test -v 2` en verde sobre el **merge**, no solo la rama.
2. Local: `createsuperuser`, login → cae en `/plataforma/`; ver Vida Plena / demo / verificación con alumnos activos, escalón, estado. Editar `facturacion_inicio` de Vida Plena y ver que el vencimiento y la tarjeta «Para cobrar» se mueven.
3. Registrar un pago con la cotización precargada (comparar `venta` con `curl https://dolarapi.com/v1/dolares/bolsa`).
4. Bloquear alumnos del gimnasio de verificación: entrar como un alumno → cartel 200; como staff → banner ámbar. Suspender → staff ve cartel, logout funciona. Restaurar → `estado_cuenta_desde` vacío.
5. Navegar como alumno y staff dos días distintos (congelar reloj) → aparecen en el gráfico; suplantar → no.
6. Crear un gimnasio desde el panel, copiar la contraseña, loguearse con ella.
7. Verificar en el navegador con el service worker desregistrado (el CSS nuevo no llega con la caché vieja).
8. Post-deploy: forzar un 500 controlado en un entorno con `SENTRY_DSN` y confirmar que llega a Sentry.
