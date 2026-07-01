# REUSO — Fase 0: extracción del esqueleto reutilizable

**Corrección de fuente:** el ROADMAP dice "extracción del esqueleto
reutilizable de Vektor", pero Vektor (`~/Desktop/vektor/Vektor/`) es FastAPI +
SQLAlchemy + Next.js, sin Django. El esqueleto real (Django, multi-tenant por
fila, `TenantScopedMixin`) vive en `~/gestor-pedidos`. Se extrajo de ahí, con
confirmación del usuario. Ver `ISSUES.md` (entrada 2026-07-01).

## Qué se reutilizó (copiado y adaptado)

| Pieza | Origen | Adaptación |
|---|---|---|
| `TimeStampedModel`, `TenantQuerySet`, `TenantOwnedModel` | `core/models.py` | `negocio` → `gimnasio` en el FK, el manager y `for_negocio()` → `for_gimnasio()` |
| `TenantScopedMixin` | `core/mixins.py` | Misma lógica; `self.negocio` → `self.gimnasio`, resuelve `request.user.perfil.gimnasio` |
| `TenantScopedModelForm` (anti FK-injection) | `core/forms.py` | Idéntico, solo el kwarg `negocio` → `gimnasio` |
| Patrón `Negocio`/`Perfil` (tenant + vínculo 1:1 con User) | `tenants/models.py` | `Negocio` → `Gimnasio`; se agregó `rol` (`staff`/`alumno`) a `Perfil`, que gestor-pedidos no tenía (ahí solo hay un rol implícito de dueño) |
| Flujo de registro atómico (User + tenant + Perfil en una transacción, login automático) | `tenants/views.py::RegisterView` | Mismo patrón; genera `slug` único vía `slugify` + sufijo incremental (gestor-pedidos no tiene slug) |
| URLs de auth (`login`/`logout`/`register` con vistas genéricas de Django) | `tenants/urls.py` | Se agregó la ruta `home` (antes vivía en `operaciones`, que no se copia) |
| Settings: `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` desde entorno, endurecimiento de producción condicionado a `DEBUG=False` (HSTS, cookies seguras, proxy SSL header) | `config/settings.py` | Se agregó `python-dotenv` para cargar `.env` en dev (gestor-pedidos espera las variables ya exportadas) |
| `LANGUAGE_CODE='es-ar'`, `TIME_ZONE='America/Argentina/Buenos_Aires'` | `config/settings.py` | Sin cambios |
| Patrón de tests con `django.test.TestCase` (sin pytest, sin factories, fixtures por `setUp()`) | `core/tests.py`, `tenants/tests.py` | Se migraron los casos aplicables a Fase 0 (registro, login/logout, aislamiento básico); ver "Qué queda pendiente" |
| Estructura de templates (`base.html` con `{% block content %}`, `registration/login.html`, `registration/register.html`) | `templates/` | Reescritos sin el sistema de diseño atómico propio de gestor-pedidos (ver abajo) — Fase 4 define el look real con Tailwind/HTMX/Alpine |
| `.gitignore` (venv, sqlite, `.env`, logs) | raíz del repo | Sin cambios de fondo |

## Qué se descartó (a propósito)

- Todo `operaciones/` (Cliente, Producto, Pedido, ItemPedido, servicios de
  pedidos, snapshot de precio) — es el dominio de pedidos, no aplica a un
  gimnasio.
- El sistema de diseño atómico de gestor-pedidos (`componentes/atomos`,
  `moleculas`, `organismos`, sus tokens CSS y `static/css/app.css` propio) —
  está fuertemente ligado a su branding. Fase 4 del ROADMAP pide
  explícitamente Tailwind + HTMX + Alpine, así que no tiene sentido copiar un
  sistema de CSS a mano que se va a reemplazar.
- `Negocio.plan` (Instagram/Full) — es un concepto comercial de
  gestor-pedidos sin equivalente todavía en el ROADMAP de gimnasios.

## Qué queda pendiente / riesgos técnicos abiertos

- **Tests de `TenantScopedMixin`/`TenantScopedModelForm` contra un modelo de
  dominio real:** en gestor-pedidos se prueban contra `Cliente`/`Pedido`
  (modelos concretos de `operaciones`). Fase 0 no tiene todavía ningún
  `TenantOwnedModel` de dominio (eso es Fase 1: Alumno, Ejercicio,
  RutinaPlantilla, PagoMensual, Novedad), así que esos tests se escriben en
  Fase 1, siguiendo el mismo patrón de `~/gestor-pedidos/core/tests.py`.
- **Object storage (Cloudflare R2) para `Gimnasio.logo`:** el campo no existe
  todavía (Fase 1 lo agrega junto con `django-storages`). Ningún archivo de
  usuario debe tocar el filesystem de Render — recordarlo al implementar
  Fase 1/5.
- **Acceso sin contraseña del alumno (magic-link):** ni gestor-pedidos ni
  Vektor tienen esto implementado. Se diseña desde cero en Fase 3
  (`django-sesame` o tokens de un solo uso con el framework de signing de
  Django, según el ROADMAP).
- **Settings sin split dev/prod:** se mantuvo un único `config/settings.py`
  (igual que gestor-pedidos) controlado por variables de entorno, no un
  paquete `settings/{base,dev,prod}.py`. Es deliberado (menos superficie,
  YAGNI); si la complejidad crece, dividir es sencillo porque todo el
  endurecimiento ya está condicionado a `DEBUG`.

## Criterio de salida (verificado 2026-07-01)

- [x] La app levanta local (`manage.py runserver`).
- [x] Login funciona end-to-end (registro → login automático → logout).
- [x] Un gimnasio creado, un usuario asociado a ese gimnasio (`Perfil`).
- [x] Admin de Django funcionando (superuser puede ver `Gimnasio`/`Perfil`).
- [x] Tests básicos pasando (6/6 — `manage.py test`).
