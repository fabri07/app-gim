# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Dueños y entrenadores de gimnasios locales argentinos** (rol `staff`,
  nivel `dueño`) — gestionan alumnos, rutinas, pagos, turnos y la
  configuración/marca de su gimnasio desde un panel web.
- **Empleados de un gimnasio** (rol `staff`, nivel `empleado` — en
  construcción) — mismo acceso operativo día a día que el dueño (alumnos,
  rutinas, pagos, turnos, novedades), sin poder editar el gimnasio ni
  gestionar otras cuentas de staff.
- **Alumnos del gimnasio** (rol `alumno`) — ven su rutina activa, su cuota
  del mes, novedades, reservan turnos y opcionalmente sincronizan con Google
  Calendar, desde un portal mobile-first.
- **El dueño del producto** (superusuario Django, fuera del sistema de
  tenants) — da de alta cada gimnasio nuevo tras cerrar una venta directa; no
  hay autoservicio.

## Product Purpose

Reemplazar Excel + papel + WhatsApp para la gestión diaria de un gimnasio o
entrenador independiente: alumnos, planes/rutinas con video, pagos
mensuales, comprobantes, novedades, turnos con cupo, y personalización de
marca por gimnasio. Éxito = un dueño de gimnasio opera todo el día a día
desde el panel sin tocar Excel ni `/admin/`, y sus alumnos entienden su
rutina y su estado de cuenta sin explicación adicional.

## Positioning

Una sola aplicación multi-tenant (nunca una copia del repo por gimnasio) con
aislamiento de datos por fila y blanco-etiquetado real (logo, colores y
tipografía por gimnasio) — se vende y se opera como "tu propia app", no como
una cuenta más en una plataforma compartida y genérica. Sin cobro automático
(Mercado Pago, débitos): los pagos los confirma el propio staff, lo que
mantiene el producto simple para un gimnasio de barrio y evita el costo y la
fricción de integrarse a un gateway de pagos en esta etapa.

## Operating Context

Venta directa, uno por uno, a gimnasios y entrenadores de un pueblo y
alrededores (Argentina) — no autoservicio ni alta pública. Cada gimnasio se
da de alta por el dueño del producto tras cerrar una venta (hoy por comando
de consola; un panel web equivalente está en desarrollo). El staff gestiona
todo desde un panel de escritorio/mobile; el alumno usa un portal
mobile-first. No hay app nativa.

## Capabilities and Constraints

- Aislamiento de datos por gimnasio a nivel de fila (no schema-per-tenant);
  ningún registro operativo existe sin un `gimnasio`.
- Sin subdominios por gimnasio en el MVP — el tenant se resuelve por el
  usuario logueado.
- Sin Mercado Pago ni integraciones financieras — los pendientes del mes se
  autogeneran por cron, el staff confirma manualmente.
- El identificador de login (email o teléfono) es único a nivel global de la
  plataforma, no por gimnasio — una misma persona no puede tener una sola
  cuenta que cubra dos gimnasios distintos (riesgo aceptado y documentado).
- Sin cobro/facturación integrada al producto — la venta y el cobro a cada
  gimnasio cliente son manuales, fuera de la app.
- Dos niveles de staff por gimnasio: `dueño` (control total, incluida la
  configuración del gimnasio y la gestión de otras cuentas de staff) y
  `empleado` (mismo acceso operativo, sin esas dos áreas) — sin niveles
  intermedios ni permisos granulares por función.

## Brand Commitments

- **Nombre del producto** (cara vendedor → dueño de gimnasio): **TuGimApp**
  (dominio `tugimapp.com`, comprado, todavía sin apuntar al deploy). El
  `<title>` actual del sitio dice genéricamente "App Gimnasios" — pendiente
  de actualizar para reflejar el nombre.
- Cada gimnasio cliente tiene su propia identidad visual dentro de la app
  (logo, color primario/secundario, tipografía de un catálogo curado) — el
  blanco-etiquetado es una promesa central del producto, no un detalle
  cosmético.

## Evidence on Hand

- **Sin clientes pagos todavía** — el dueño del producto está por empezar a
  vender. Sin testimonios, casos de estudio ni datos de uso real de
  gimnasios: no inventar ninguno en trabajo futuro.
- Desplegado en un entorno de prueba: `https://app-gim.onrender.com`
  (Render free tier).

## Product Principles

1. Una sola app, nunca una copia por gimnasio — el aislamiento vive en el
   modelo de datos, no en la infraestructura.
2. Primero se cobra, después se sofistica — no construir features que no
   ayuden a conseguir o retener los primeros gimnasios pagos.
3. La app es la fuente de verdad — nada de sincronización en vivo con Excel
   ni datos que vivan fuera del sistema.
4. Blanco-etiquetado real por gimnasio, no una plantilla compartida con un
   logo pegado encima.
5. Simplicidad operativa por sobre integraciones — sin cobro automático, sin
   subdominios, sin complejidad que un dueño de gimnasio de barrio no pidió.

## Accessibility & Inclusion

Sin estándar formal exigido — se sigue con buenas prácticas generales. Ya
existe precedente en el propio código: cada gráfico del dashboard analítico
tiene un "Ver como tabla" (`<details>`, sin JS) como equivalente accesible.
