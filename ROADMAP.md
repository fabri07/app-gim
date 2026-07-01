# ROADMAP — App Multi-Tenant para Gimnasios y Entrenadores (v2)

## Cambios en esta versión

Ajustes sobre la versión anterior, todos incorporados abajo:

1. **Comprobantes en object storage, no en el filesystem.** Render tiene filesystem
   efímero: cualquier archivo subido se borra en cada deploy. Los comprobantes van a
   Cloudflare R2 (S3-compatible, free tier generoso, sin costo de egress) vía
   `django-storages`. Nunca al disco de Render.
2. **Timeline realista.** Se pasa de 4 semanas rígidas a ~6 semanas / hitos. Se agrega el
   cierre comercial del primer gimnasio *antes* de terminar el producto (onboarding
   concierge + seña del setup) para asegurar plata de julio y bloquear al cliente.
3. **Acceso del alumno sin contraseña (magic-link / código).** Diseñado explícitamente.
   Los socios de gimnasio no son técnicos; usuario+contraseña = call center de reseteos.
4. **Pagos autogenerados por cron.** El dueño confirma, no crea. El cron genera los pagos
   pendientes del mes y pasa `pendiente → vencido` solo. Sin esto, el dato se pudre.
5. **Sin subdominios por gimnasio en el MVP.** El tenant se resuelve por el usuario logueado
   (o path `/g/slug/`). Subdominios wildcard + SSL quedan para más adelante.
6. **Simplificaciones:** el comprobante lo sube solo el dueño en la v1; los roles "dueño" y
   "entrenador" se colapsan en un rol `staff` para el MVP.

---

## Objetivo del proyecto

App SaaS simple, multi-tenant y white-label para gimnasios y entrenadores locales.

Reemplazar Excel + papel + WhatsApp por una app donde cada gimnasio pueda: registrar
alumnos, asignar planes, mostrar ejercicios con videos, controlar pagos mensuales,
registrar comprobantes, comunicar novedades y personalizar logo/nombre/colores.

Objetivo comercial de julio: primeros clientes pagos, y validar si el producto cubre los
costos mensuales de herramientas e infraestructura.

---

# Principios no negociables

## 1. Una sola app, múltiples gimnasios
Una única base de código, un único deploy, gimnasios aislados por tenant. **No se copia el
repo por gimnasio.** Todo registro importante (alumno, staff, rutina, plan asignado, pago,
comprobante, novedad, config visual) pertenece a un gimnasio.

## 2. La app es la fuente de verdad
Los Excel solo se usan para importación inicial. **No hay sync en vivo con Excel/Sheets en
el MVP.** Flujo: el gimnasio entrega su Excel → se limpian los datos → se importan alumnos,
rutinas y ejercicios → desde ahí se trabaja dentro de la app.

## 3. Pagos simples, sin integración financiera
Nada de Mercado Pago, bancos, débito automático ni APIs de cobro en el MVP. El sistema solo:
marca mes como pagado, guarda comprobante, muestra deudores, filtra por mes, registra
observaciones. **Los pagos pendientes del mes se autogeneran (cron); el dueño confirma, no
crea.** El comprobante lo sube el dueño en la v1.

## 4. Seguridad desde el modelo de datos
Aislamiento por gimnasio desde el primer modelo.
> Ningún alumno, rutina, pago, comprobante o novedad puede existir sin estar asociado a un
> gimnasio.

## 5. Los archivos subidos NO viven en Render
El filesystem de Render es efímero (se borra en cada deploy). Todo archivo de usuario
(comprobantes, logos) va a **object storage externo (Cloudflare R2)** vía `django-storages`.
Esto se decide antes de subir el primer comprobante real.

## 6. El tenant se resuelve sin subdominios
En el MVP, el gimnasio se identifica por el usuario logueado (o path `/g/slug/`). Nada de
DNS wildcard ni SSL por subdominio todavía.

## 7. Primero se cobra, después se sofisticá
El producto inicial resuelve el dolor actual: dejar de imprimir rutinas, dejar de mandar
todo por WhatsApp, saber quién pagó, dar imagen profesional.

**No construir todavía:** QR de ingreso, control de asistencia, app nativa, Mercado Pago,
débitos automáticos, chat interno, nutrición, métricas deportivas, IA de rutinas,
subdominios por gimnasio.

---

# Fase 0 — Extracción del esqueleto reutilizable

**Objetivo:** repo nuevo con solo la infraestructura reusable de Vektor, sin el dominio de
pedidos.

**Reutilizar:** config base de Django, estructura de settings, variables de entorno, auth,
`TenantScopedMixin`, managers/querysets con scoping por tenant, middleware de tenant (si
existe), fixtures y patrones de tests, templates base, config de producción y static files,
y el patrón de snapshot / congelamiento de valores históricos.

**No reutilizar:** `Pedido`, `ItemPedido`, `Producto`, carrito, modelos de pedidos, planes
de pricing complejos, cualquier feature de Vektor innecesaria acá.

**Entregables:** repo creado, Django levanta local, app base, Gimnasio como modelo central,
y `REUSO.md` (qué se reutilizó / descartó / adaptó / qué riesgos técnicos quedan).

**Criterio de salida:** la app levanta con login, un gimnasio creado, un usuario asociado a
ese gimnasio, admin de Django funcionando y tests básicos pasando.

---

# Fase 1 — Modelo de datos mínimo

### Gimnasio (tenant)
`nombre`, `slug`, `logo`, `color_primario`, `color_secundario`, `texto_bienvenida`,
`contacto`, `link_instagram`, `link_whatsapp`, `activo`, `fecha_alta`.
(El logo también va a object storage, no al filesystem.)

### Usuario / Perfil
Roles del MVP: **`staff`** (dueño y/o entrenador, mismos permisos) y **`alumno`**.
Un usuario pertenece a un gimnasio. (Separar dueño de entrenador queda para después: en los
gimnasios chicos suele ser la misma persona, y menos roles = menos lógica de permisos.)

**Acceso del alumno: sin contraseña.** El alumno accede por magic-link o código enviado a su
email/teléfono. Opción concreta: `django-sesame` para links firmados, o tokens de un solo
uso con el framework de signing de Django. Esto define un pequeño flujo de invitación (ver
Fase 3), no necesariamente campos extra en el modelo.

### Alumno
`gimnasio`, `nombre`, `apellido`, `email`, `teléfono`, `fecha_nacimiento`,
`estado` (activo/inactivo), `fecha_alta`, `observaciones`, `fecha_activacion` (cuándo entró
por primera vez — sirve para la métrica de adopción).

### Ejercicio
`gimnasio`, `nombre`, `grupo_muscular`, `descripción`, `url_video`, `activo`.
(Por gimnasio al inicio; biblioteca global es una feature posterior.)

### RutinaPlantilla
`gimnasio`, `nombre`, `objetivo`, `nivel`, `días_por_semana`, `activa`.

### RutinaPlantillaItem
`rutina`, `ejercicio`, `día`, `orden`, `series`, `repeticiones`, `descanso`, `notas`.

### RutinaAsignada (snapshot — modelo clave)
`gimnasio`, `alumno`, `nombre_snapshot`, `objetivo_snapshot`, `fecha_inicio`, `fecha_fin`,
`activa`. Al asignar, se copia la info de la plantilla. Editar la plantilla después **no**
cambia el historial del alumno.

### RutinaAsignadaItem
`rutina_asignada`, `ejercicio_nombre_snapshot`, `ejercicio_video_snapshot`, `día`, `orden`,
`series`, `repeticiones`, `descanso`, `notas`.

### PagoMensual
`gimnasio`, `alumno`, `mes`, `año`, `monto`, `estado` (pendiente/pagado/vencido),
`fecha_pago`, `medio_pago_texto`, `comprobante` (a object storage), `observaciones`.
Los pendientes se **autogeneran por cron** para cada alumno activo al inicio del mes, y el
cron pasa `pendiente → vencido` cuando corresponde. Usar `PROTECT`.

### Novedad
`gimnasio`, `título`, `mensaje`, `fecha_publicación`, `visible_hasta`, `activa`.

---

# Fase 2 — Backend funcional (flujos del staff)

### 1. Dashboard
Alumnos activos, alumnos con pago pendiente, pagos del mes, rutinas activas, últimas novedades.

### 2. Gestión de alumnos
Crear, editar, activar/inactivar, ver ficha, ver pagos, ver rutina actual.
**Incluye enviar/reenviar la invitación de acceso** (magic-link) al alumno.

### 3. Gestión de ejercicios
Crear, editar, cargar link de YouTube, filtrar por grupo muscular.

### 4. Gestión de rutinas
Crear plantilla, agregar ejercicios, ordenar por día, editar series/reps/descanso/notas,
**duplicar rutina existente** (antes que crear desde cero).

### 5. Asignación de rutina
Elegir alumno, elegir plantilla, definir fecha de inicio, generar snapshot, ver asignada.

### 6. Gestión de pagos
Los pagos pendientes del mes ya vienen autogenerados. El staff: **confirma** un pago
(marca pagado + sube comprobante), filtra deudores, filtra por mes, ve historial. No crea
pagos a mano.

### 7. Gestión de novedades
Publicar, editar, ocultar, definir visibilidad.

**Entregables:** vistas funcionales, formularios, permisos por rol (staff/alumno), tests de
aislamiento por gimnasio, test del snapshot de rutina asignada, tests de pagos (incluida la
autogeneración por cron).

**Criterio de salida:** un dueño usa el sistema de punta a punta desde el panel web, sin
tocar el admin de Django.

---

# Fase 3 — Portal del alumno

### Activación de cuenta (diseñada explícitamente)
1. El staff crea al alumno (o lo importa del Excel) y dispara la invitación.
2. El alumno recibe un link/código por email o WhatsApp/teléfono.
3. Toca el link → entra sin crear contraseña. Se registra `fecha_activacion`.
4. Reingresos: nuevo magic-link o sesión recordada. Cero gestión de contraseñas.

### Funciones del alumno
Ver su rutina activa, ver ejercicios por día, abrir videos, ver novedades, ver el estado de
su mensualidad.

**No incluir todavía:** subir comprobante (lo sube el dueño en v1), chat, comentarios por
ejercicio, carga de pesos, métricas corporales, asistencia, calendario, app nativa.

**Criterio de salida:** un alumno real entra desde el celular y entiende su rutina sin
explicación adicional.

---

# Fase 4 — UX/UI y white-label

**Personalización por gimnasio** (todo sale de los campos del modelo `Gimnasio`, un solo
código): nombre visible, logo, colores, texto de bienvenida, contacto, links de
Instagram/WhatsApp.

**UX del staff:** rapidez para cargar rutinas, pocos clicks, formularios simples, duplicar
antes que crear, filtros útiles.

**UX del alumno:** mobile-first, rutina clara, videos accesibles, cero complejidad, estado
de pago simple.

**Resolución de tenant:** por usuario logueado o path `/g/slug/`. **Sin subdominios.**

**Stack:** Django templates + HTMX + Tailwind CSS + Alpine.js solo si hace falta. Nada de
React/Next en el MVP.

**Criterio de salida:** la app se le muestra a un dueño sin parecer prototipo interno.

---

# Fase 5 — Deploy en Render

**Arquitectura inicial:**
- Render Web Service (Starter, siempre prendido) para Django.
- Render Postgres pago (con backups + PITR).
- **Cloudflare R2 para media** (comprobantes y logos) vía `django-storages`. Nunca el
  filesystem de Render.
- Cron Job de Render para: recordatorios de pago, autogeneración de pagos mensuales y
  transición `pendiente → vencido`.
- Variables de entorno en Render. Static files configurados (WhiteNoise sirve estáticos).
- **Sin subdominios por gimnasio.** Un dominio/subdominio único para toda la app.

**Variables críticas de seguridad:**
`DEBUG=False`, `SECRET_KEY` por entorno, `DATABASE_URL`, `ALLOWED_HOSTS` cerrado,
`CSRF_TRUSTED_ORIGINS`, `SECURE_SSL_REDIRECT=True`, `SESSION_COOKIE_SECURE=True`,
`CSRF_COOKIE_SECURE=True`, credenciales de R2 por entorno.

**Criterio de salida:** app online, con login, base persistente, backups activos, media en
R2, sin SQLite en producción.

---

# Fase 6 — Primer piloto pago

> Clave: el cierre comercial y la seña pueden pasar **antes** de que el producto esté
> terminado. No esperes a tener el software para cobrar.

**Cierre comercial (se puede hacer ya, en paralelo al desarrollo):**
1. Reunión con el dueño; mostrás roadmap + pantallas/mockup.
2. Cerrás el trato y cobrás **seña del setup**.
3. Recolectás el Excel actual.

**Onboarding (concierge, lo hacés vos a mano):**
4. Limpieza de datos.
5. Carga inicial de alumnos.
6. Carga de ejercicios frecuentes.
7. Carga de 3 a 5 rutinas reales.
8. Configuración de logo y colores.
9. Envío de invitaciones de acceso a alumnos.
10. Prueba con 2-3 alumnos.
11. Ajustes mínimos.
12. Activación del gimnasio completo.

**Cobro recomendado:**
- Setup inicial: ARS 50.000 a 100.000 (cubre migración, configuración, personalización).
- Mensualidad: ARS 30.000 a 50.000 (uso, hosting, soporte básico, mantenimiento).

**Criterio de salida:** el primer gimnasio tiene alumnos reales usando la app y al menos un
pago registrado.

---

# Fase 7 — Validación comercial local

**Medir en las primeras 4 semanas:** alumnos cargados, rutinas asignadas, pagos
registrados, comprobantes subidos, pedidos de soporte, alumnos que realmente entran
(usar `fecha_activacion`), tiempo para configurar un gimnasio nuevo, bugs por gimnasio,
pedidos repetidos de features.

**Señales positivas:** el dueño usa pagos cada semana; los alumnos abren la rutina desde el
celular; el gimnasio deja de imprimir; el dueño recomienda la app; el segundo onboarding es
más rápido que el primero.

**Señales negativas:** el dueño sigue en Excel; el entrenador no carga rutinas; los alumnos
no entran; cada gimnasio pide una app distinta; el soporte manual consume demasiadas horas.

---

# Fase 8 — Segundo y tercer cliente

**Regla:** no agregar features grandes entre el primer y tercer cliente. Solo corregir bugs,
fricción de onboarding, permisos, UX y carga de datos.

**Criterio de salida:** tres gimnasios sobre la misma base de código, sin forks ni
personalizaciones duras en el código.

---

# Fase 9 — Features posteriores (solo con clientes pagos)

**Prioridad media:** importador CSV más automático, recordatorios automáticos por email,
reporte mensual de deudores, historial de rutinas por alumno, biblioteca global de
ejercicios, duplicación avanzada de rutinas, panel financiero simple, **subdominios por
gimnasio**.

**Prioridad baja:** Mercado Pago, débitos automáticos, QR de ingreso, control de asistencia,
app nativa, push reales, IA de rutinas, WhatsApp API, nutrición, medidas corporales,
gamificación.

---

# Orden recomendado de trabajo (~6 semanas, realista para un dev solo)

> El timeline es ajustado si además mantenés el otro proyecto. Si algo se estira, es la UX
> (Semana 4-5). Cerrá el gimnasio y cobrá la seña temprano para no depender de terminar todo.

**Semana 0 (en paralelo, sin código):** reunión con el primer gimnasio, cierre y seña del
setup, recolección del Excel.

**Semana 1:** repo + esqueleto reusable (Fase 0). Modelo Gimnasio, auth, Alumno, Ejercicio,
RutinaPlantilla, PagoMensual. Admin funcional. Tests de tenant isolation.

**Semana 2:** dashboard del staff, CRUD de alumnos (con invitación), CRUD de ejercicios,
CRUD de rutinas con duplicación.

**Semana 3:** asignación de rutina con snapshot, gestión de pagos (confirmar + comprobante),
cron de autogeneración/vencimiento, novedades.

**Semana 4:** portal del alumno + flujo de activación sin contraseña, vista mobile de la
rutina, integración de R2 para comprobantes.

**Semana 5:** white-label (logo/colores por gimnasio), pulido de UX, deploy a Render con
todas las variables de seguridad y backups.

**Semana 6:** onboarding concierge del primer gimnasio, migración del Excel real, prueba con
alumnos reales, ajuste de bugs, demo para el segundo gimnasio.

---

# Definición de MVP terminado

1. Un gimnasio inicia sesión.
2. Carga alumnos.
3. Crea ejercicios.
4. Crea rutinas.
5. Asigna una rutina a un alumno (con snapshot).
6. El alumno entra sin contraseña desde el celular y ve su rutina.
7. El dueño marca pagos (autogenerados por cron) y sube comprobante.
8. Los comprobantes persisten en R2 (no se borran en deploys).
9. El sistema separa correctamente los datos entre gimnasios.
10. La app está online en Render, con backups activos.
11. Hay al menos un gimnasio pagando.

---

# Regla final

No construir nada que no ayude directamente a conseguir o retener los primeros tres
gimnasios pagos.
