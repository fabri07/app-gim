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
