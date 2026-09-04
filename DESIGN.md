---
name: TuGimApp
description: Panel operativo multi-tenant para gimnasios y entrenadores locales, con blanco-etiquetado en vivo por gimnasio.
colors:
  fondo: "#f5ede4"
  primary: "#1d6f56"
  secondary: "#e8735c"
  neutral-surface-border: "oklch(96.7% .003 264.542)"
  neutral-border: "oklch(92.8% .006 264.531)"
  neutral-muted: "oklch(55.1% .027 264.364)"
  neutral-label: "oklch(44.6% .03 256.802)"
  neutral-body: "oklch(37.3% .034 259.733)"
  neutral-heading: "oklch(21% .034 264.665)"
  status-ok-bg: "oklch(96.2% .044 156.743)"
  status-ok-text: "oklch(44.8% .119 151.328)"
  status-alerta-bg: "oklch(96.2% .059 95.617)"
  status-alerta-border: "oklch(92.4% .12 95.746)"
  status-alerta-text: "oklch(47.3% .137 46.201)"
  status-riesgo-bg: "oklch(93.6% .032 17.717)"
  status-riesgo-text: "oklch(44.4% .177 26.899)"
  accion-peligro: "oklch(57.7% .245 27.325)"
  accion-peligro-hover: "oklch(50.5% .213 27.518)"
  suplantacion-bg: "oklch(96.2% .059 95.617)"
  suplantacion-border: "oklch(87.9% .169 91.605)"
  suplantacion-text: "oklch(41.4% .112 45.904)"
  dataviz-secuencial-1: "#b7d3f6"
  dataviz-secuencial-2: "#6da7ec"
  dataviz-secuencial-3: "#2a78d6"
  dataviz-secuencial-4: "#184f95"
typography:
  display:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "clamp(2.25rem, 5vw, 3rem)"
    fontWeight: 700
    lineHeight: 1.1
  title:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  headline:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.05em"
  metrica:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "1.875rem"
    fontWeight: 700
rounded:
  md: "0.375rem"
  lg: "0.5rem"
  2xl: "1rem"
  full: "9999px"
spacing:
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.full}"
    padding: "8px 16px"
  button-secondary:
    backgroundColor: "#ffffff"
    textColor: "{colors.neutral-body}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-danger:
    backgroundColor: "{colors.accion-peligro}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  card:
    backgroundColor: "#ffffff"
    rounded: "{rounded.2xl}"
    padding: "24px"
  badge-ok:
    backgroundColor: "{colors.status-ok-bg}"
    textColor: "{colors.status-ok-text}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  badge-alerta:
    backgroundColor: "{colors.status-alerta-bg}"
    textColor: "{colors.status-alerta-text}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  badge-riesgo:
    backgroundColor: "{colors.status-riesgo-bg}"
    textColor: "{colors.status-riesgo-text}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
---

# Design System: TuGimApp

## Overview

**Creative North Star: "Un Paisaje por Gimnasio"**

El sistema tiene ahora una voz propia y confiada — tipografía geométrica
bold (Plus Jakarta Sans por defecto), radios grandes, botones píldora — en
vez de la identidad neutra que tenía antes. Esa voz nunca cambia; lo que
cambia por gimnasio es el **paisaje de color**: cada uno elige una de cuatro
paletas curadas y completas (fondo cálido + 2 acentos, ya armonizados) en
vez de dos colores sueltos como antes. La dirección está inspirada en
crossfyapp.com (un competidor directo, referencia tomada a propósito por el
material — tipografía pesada, paleta cálida, energía de landing de venta —
no calcada composición por composición).

El resultado: la marca del producto vive en la forma (tipografía, radio,
forma de botón), no en un color fijo. El color es lo que cada gimnasio
"pone sobre la mesa", elegido de un catálogo — nunca libre — para que
ninguna combinación resulte ilegible. **Bosque** (crema + verde bosque +
coral) es el paisaje por defecto y el que usa el propio sistema cuando
todavía no hay un gimnasio en contexto (login, error 404, etc.).

Persuade (la landing pública de cada gimnasio) lleva la energía completa:
degradé de marca a todo el ancho del hero, números y botones grandes.
Operate (el resto del panel) usa la misma paleta con más moderación, pero
ya no es un plano sin vida: el canvas de fondo (`body`, y `.landing` bajo
el hero) lleva una atmósfera de 3 blobs radiales muy suaves, mezclados con
`color-mix()` sobre `--color-primario`/`--color-secundario` — nunca un
bloque sólido de color a página completa, pero tampoco un solo hex fijo.
Las superficies con datos (`.tarjeta`, `.tabla`, `.metrica`) siguen 100%
blancas encima: la expresión vive únicamente en el canvas, nunca estorba
una tabla de 40 alumnos.

**Key Characteristics:**
- Tipografía bold y geométrica como identidad propia del sistema —
  auto-hospedada por defecto, nunca dependiente de que el gimnasio elija algo.
- Cuatro paisajes de color completos y curados (Bosque/Océano/Arena/Pizarra),
  nunca colores sueltos elegidos libremente.
- Radios grandes (`2xl` en superficies, píldora en el botón primario) —
  más amigable que el sistema anterior, sin perder densidad en controles.
- Persuade (landing) va a fondo con el color en el hero; Operate lleva el
  mismo paisaje al canvas de fondo como atmósfera suave (nunca un bloque
  sólido), en vez de un color plano.
- Color de estado (verde/ámbar/rojo) sigue totalmente separado del paisaje
  de marca — un canal, no el otro.

## Colors

Cuatro paisajes completos y curados reemplazan el par de colores libres que
existía antes. Cada uno define 3 roles (fondo, primario, secundario) ya
armonizados — el gimnasio elige un paisaje entero, nunca un color suelto.

### Primary
- **Verde bosque** (`#1d6f56`): acento principal de **Bosque**, el paisaje
  por defecto — botón primario, links, foco de inputs, mitad del degradé
  del hero. Es el que usa el propio sistema fuera de contexto de gimnasio
  (login, 404).
- **Coral** (`#e8735c`): secundario de Bosque — compañero en el degradé del
  hero, casi no se usa solo fuera de la landing.

### Otros paisajes curados (mismo rol que Bosque, distinto tono)
- **Océano**: fondo `#eef3f6`, primario `#1e3a5f` (azul noche), secundario
  `#e2a03f` (ámbar).
- **Arena**: fondo `#faf6f0`, primario `#b4532a` (terracota), secundario
  `#2f6b63` (verde azulado).
- **Pizarra**: fondo `#f0f1f3`, primario `#33475b` (gris azulado), secundario
  `#5b8c5a` (verde salvia).

### Neutral
- **Borde sutil** (`oklch(96.7% .003 264.542)`, gray-100): separadores de
  baja intensidad (filas de tabla, secciones de formulario).
- **Borde** (`oklch(92.8% .006 264.531)`, gray-200): borde estándar de
  tarjetas, tablas, inputs y la barra superior.
- **Texto apagado** (`oklch(55.1% .027 264.364)`, gray-500): etiquetas
  secundarias, texto de apoyo (`.texto-suave`).
- **Texto de label** (`oklch(44.6% .03 256.802)`, gray-600): encabezados de
  tabla, texto de navegación.
- **Texto de cuerpo** (`oklch(37.3% .034 259.733)`, gray-700): párrafos,
  celdas de tabla.
- **Texto principal** (`oklch(21% .034 264.665)`, gray-900): títulos, valores
  numéricos de métricas.

Nota: las superficies (`.tarjeta`, `.tabla`, `.metrica`) siguen en blanco
sólido siempre, sin importar el paisaje — solo el CANVAS de fondo
(`--color-fondo`) cambia por gimnasio. Es deliberado: el sistema no tiene
modo oscuro real, así que el texto gris fijo solo es legible sobre
superficies claras garantizadas.

### Estado (semántico, no de marca)
Verde/ámbar/rojo comunican **estado**, nunca identidad — se mantienen fijos
sin importar el paisaje elegido por el gimnasio.
- **Éxito** (`oklch(96.2% .044 156.743)` fondo / `oklch(44.8% .119 151.328)`
  texto, green-100/green-800): alumno activo, pago al día, turno propio.
- **Alerta** (`oklch(96.2% .059 95.617)` fondo / `oklch(47.3% .137 46.201)`
  texto, amber-100/amber-800): pago pendiente, novedad, mensaje del sistema.
  El mismo ámbar, más saturado (`amber-100`/`amber-300`/`amber-900`), viste
  el banner de suplantación — a propósito: es un estado temporal y anómalo,
  no una sección más del panel.
- **Riesgo** (`oklch(93.6% .032 17.717)` fondo / `oklch(44.4% .177 26.899)`
  texto, red-100/red-800): pago vencido, turno lleno.
- **Acción destructiva** (`oklch(57.7% .245 27.325)`, red-600, con hover
  `oklch(50.5% .213 27.518)`, red-700): el único uso de rojo sólido, en
  `.boton-peligro`.

### Escala secuencial de datos (dataviz)
Ramp propio de 4 pasos para la grilla de calor de asistencia del dashboard —
**deliberadamente distinto del paisaje de marca**: es un canal de
codificación de datos, no branding, así que ningún paisaje lo pisa.
`#b7d3f6` → `#6da7ec` → `#2a78d6` → `#184f95` (claro a oscuro).

### Paleta categórica de dataviz

4 colores para series categóricas (no ordinales/divergentes) del dashboard
— hoy solo el desglose por género de "Ejercicios más asignados", que
**solo se renderiza en gimnasios de público mixto** (`Gimnasio.tipo_publico`,
ver CLAUDE.md § "Público del gimnasio"): en uno de un solo género ese gráfico
sería el general repintado, así que no se muestra y esta paleta se queda sin
ningún consumidor en pantalla. Slots 1-4

del tema por defecto de la skill `dataviz` (azul → naranja → aqua →
amarillo), en ese orden fijo, nunca ciclado. Validados con
`scripts/validate_palette.js` de la skill contra el fondo real de esta app
(`#f5ede4`): lightness band, chroma floor, separación CVD y piso de visión
normal en PASS; el único WARN (contraste vs. superficie) se mitiga con
leyenda siempre visible + tabla `<details>` accesible, mismo patrón que ya
usan los otros 3 gráficos.
`#2a78d6` (azul, reusado del paso 3 de la escala secuencial) · `#eb6834`
(naranja) · `#1baf7a` (aqua) · `#eda100` (amarillo). Deliberadamente no
reusa el rojo de RPE (`#e34948`) porque ahí significa "al límite" — usarlo
acá como categoría neutral de género confundiría el significado.

### Named Rules
**The Landscape Rule.** El color de marca nunca se elige suelto — siempre
es uno de los 4 paisajes curados de `Gimnasio.PALETAS`, cada uno con sus 3
roles (fondo/primario/secundario) ya armonizados. Un componente nuevo jamás
ofrece un color picker libre para identidad de marca.

**The Runtime Brand Rule.** Ningún componente nuevo hardcodea un hex del
paisaje. Todo lo que deba reflejar la identidad del gimnasio referencia
`var(--color-fondo)` / `var(--color-primario)` / `var(--color-secundario)`
— son datos de `Gimnasio`, sobreescritos por request en `base.html` (y por
`landing.html` para el visitante anónimo), nunca constantes de Tailwind en
build-time.

## Typography

**Fuente por defecto:** Plus Jakarta Sans, auto-hospedada (`@font-face` en
`styles/input.css`, servida desde el propio dominio) — nunca dispara una
carga externa a Google. Es la voz propia del sistema: hasta una página sin
gimnasio en contexto (login) ya se ve con esta identidad.
**Fuentes opcionales por gimnasio:** Sora, Manrope, Outfit, Space Grotesk —
todas geométricas/bold de la misma familia de carácter que el default
(Google Fonts, cargadas solo si el gimnasio activamente eligió una de estas
4). A diferencia del catálogo anterior, ya no hay una opción "sin
personalidad" (el viejo "sistema") — la identidad bold es parte de la marca
del producto, no algo opcional.

**Character:** confiada y geométrica, con presencia — títulos bold cortos,
cuerpo regular en el mismo carácter tipográfico (no una fuente aparte para
texto largo). La fuente cambia por gimnasio dentro de una familia de
carácter afín; el peso y el tamaño de cada rol, no.

### Hierarchy
- **Display** (700, `clamp(2.25rem, 5vw, 3rem)`, 1.1): título del hero de la
  landing pública (`.landing__titulo`, `text-4xl sm:text-5xl font-bold`,
  blanco sobre el degradé de marca). Es el único uso de esta escala.
- **Headline** (600, 18px/`1.125rem`, 1.3): encabezados de sección dentro de
  una pantalla (`h2`).
- **Title** (600, 20px/`1.25rem`, 1.3): título de página (`h1`).
- **Body** (400, 14px/`0.875rem`, 1.5): el tamaño de texto dominante del
  panel — párrafos, celdas de tabla, inputs, botones.
- **Label** (600, 12px/`0.75rem`, `letter-spacing: 0.05em`, uppercase):
  micro-etiquetas de contexto, como "Vista previa" en el editor de gimnasio.
- **Métrica** (700, 30px/`1.875rem`): valores numéricos grandes del
  dashboard (`.metrica__valor`) — el único rol pensado para leerse de lejos.

### Named Rules
**The Gym-Swappable Font Rule.** La familia tipográfica siempre se referencia
como `var(--font-gimnasio)` (con el hint `family-name:` en clases arbitrarias
de Tailwind — sin él, Tailwind interpreta el valor como `font-weight`, no
`font-family`). Nunca se hardcodea un nombre de fuente fuera de
`Gimnasio.TIPOGRAFIA_FUENTES`.

**The Self-Hosted Default Rule.** El default (Plus Jakarta Sans) se sirve
siempre desde el propio dominio, nunca desde Google — es la única fuente
del catálogo sin `google_param`. Las otras 4 son elección activa del
gimnasio; recién ahí se justifica pagar el costo de una carga externa.

## Layout

Dos anchos de contenedor, sin grid propio más allá de eso:
- **`.contenido`** (`max-w-md`, ~28rem): formularios y pantallas angostas
  centradas — el portal del alumno, login.
- **`.contenido--ancho`** (`max-w-5xl`, ~64rem): listados, dashboard, y toda
  vista de gestión de staff.

Densidad moderada: `px-4 py-8` en el contenedor principal, tarjetas con
`p-6`, filas de tabla con `py-2.5`. El dashboard usa una grilla de 2
columnas en mobile y 4 en desktop (`grid-cols-2 sm:grid-cols-4`) para las
métricas; la agenda de turnos pasa de 1 columna en mobile a 7 en desktop
(una por día). Mobile-first en todo: el nav de staff colapsa detrás de un
botón `☰` (Alpine.js) por debajo del breakpoint `sm`.

## Elevation & Depth

Sistema **plano por defecto, con una sola sombra**, en las SUPERFICIES.
`shadow-sm` (`0 1px 3px 0 rgba(0,0,0,.1), 0 1px 2px -1px rgba(0,0,0,.1)`)
aparece en tarjetas, tablas y métricas — siempre en reposo, nunca como
respuesta a hover o estado. No hay una escala de elevación: la profundidad
de una superficie es casi enteramente de borde (`border border-gray-200`),
no de sombra.

El CANVAS detrás de esas superficies es un eje aparte: lleva una atmósfera
de 3 `radial-gradient()` muy suaves (`color-mix()` sobre `--color-primario`/
`--color-secundario`, sin `background-attachment: fixed` a propósito —
evita el jank conocido de esa propiedad en mobile Safari, y el portal del
alumno es mobile-first). Es profundidad de fondo, no de superficie: no
agrega ni reemplaza ninguna sombra, y no aplica a `.tarjeta`/`.tabla`/
`.metrica`, que siguen sólo con `shadow-sm` + borde.

### Named Rules
**The One Shadow Rule.** Un solo nivel de sombra en todo el sistema. Si un
componente nuevo necesita distinguirse, se hace con borde o con fondo, no
agregando una sombra más fuerte.

**The Atmospheric Canvas Rule.** El canvas de fondo (`body`, `.landing`)
nunca es un `background-color` sólido y liso: siempre lleva atmósfera. Desde
"Fondo personalizable" (2026-08-14) esa atmósfera tiene **tres variantes**, y
el dueño elige cuál con `Gimnasio.fondo_tipo`:

- **`color`** (default): los 3 blobs radiales del paisaje activo. Es lo que
  ve todo gimnasio que no eligió otra cosa.
- **`imagen`**: la foto propia del gimnasio, con un velo de `--color-fondo` al
  55% encima para que `.tarjeta`/`.tabla` sigan legibles.
- **`doodle`**: un patrón de uno de los 4 doodles curados, tileado a 300px y
  teñido con `--color-secundario` al 22%.

Las tres viven en un solo lugar (`body`/`.landing`, definido en
`styles/input.css` y sobreescrito por request en `base.html`/`landing.html`):
un componente nuevo no agrega su propio degradé ni su propia imagen de fondo,
y la atmósfera no se repite por sección.

Dos invariantes que valen para las tres variantes:

- **Los acentos no se personalizan con el fondo.** `--color-primario`/
  `--color-secundario` salen SIEMPRE de `paleta`, sin importar `fondo_tipo`.
  Una imagen propia no aporta color de marca: evita combinaciones
  imagen+acento no armonizadas. Es la misma lógica que The Landscape Rule.
- **Nunca `background-attachment: fixed`**, en ninguna de las tres — jank
  conocido en mobile Safari, y el portal del alumno es mobile-first.

El doodle se pinta en un pseudo-elemento con `mask-image` (un SVG monocromo
por doodle, "entintado" con `background-color`), no como imagen coloreada:
así el mismo archivo estático sirve para cualquier paisaje. Cuando ese
pseudo-elemento vive dentro de un contenedor con fondo propio —`.landing`, la
ventana del preview— el contenedor necesita `isolation: isolate`, si no su
`background-color` pinta ENCIMA del `z-index: -1` y el doodle no se ve.
(`body` no lo necesita: su fondo se propaga al canvas y pinta primero.)

## Shapes

Radios de esquina consistentes por categoría — más grandes que en la
versión anterior del sistema, sin bordes decorativos:
- **`md`** (`0.375rem`/6px): controles densos — inputs, botón secundario/
  peligro, filas de nav, mensajes, tarjetas de turno.
- **`lg`** (`0.5rem`/8px): contenedor de filtros.
- **`2xl`** (`1rem`/16px): el radio "de superficie" del sistema — tarjetas,
  tablas, métricas, la ventana de preview, el marco del logo de landing.
  Reemplaza al `xl` (12px) de la versión anterior.
- **`full`**: el botón primario (píldora — cambio respecto a la versión
  anterior, que era `md`), el CTA de la landing, y los badges de estado.

Bordes de 1px (`border-gray-200`) en casi todo — cards, tablas, inputs,
topbar.

### Named Rules
**The Two-Radius Rule.** Solo dos escalones de radio conviven en cualquier
jerarquía visual: `md` en controles densos, `2xl` en superficies — más
`full` como caso especial para botones primarios y badges. Nunca un tercer
escalón intermedio.

## Components

### Buttons
- **Shape:** el primario es píldora (`rounded-full`); secundario y peligro
  se quedan en `md` (6px) — la asimetría es a propósito, marca cuál es LA
  acción de la pantalla.
- **Primary (`.boton`):** fondo `var(--color-primario)`, texto blanco,
  píldora. Hover: `opacity-90`. Es el único botón cuyo color cambia por
  gimnasio.
- **Secondary (`.boton-secundario`):** borde gris, fondo blanco, texto
  `gray-700`, `rounded-md`. Hover: `bg-gray-50`.
- **Danger (`.boton-peligro`):** fondo `red-600`, texto blanco, `rounded-md`,
  hover `red-700` — reservado para acciones destructivas confirmadas.

### Badges
- **Style:** píldora (`rounded-full`), `px-2.5 py-0.5`, `text-xs font-medium`.
- **State:** tres variantes fijas — `--ok` (verde), `--alerta` (ámbar),
  `--riesgo` (rojo) — nunca un cuarto color; el estado que no encaja en
  ninguna se omite, no se inventa una variante nueva.

### Cards / Containers
- **Corner Style:** `rounded-2xl` (16px).
- **Background:** blanco sólido siempre, sobre el canvas cálido de fondo
  (`--color-fondo`) — nunca cambia con el paisaje del gimnasio.
- **Shadow Strategy:** `shadow-sm`, siempre en reposo (ver Elevation).
- **Border:** `border border-gray-200`.
- **Internal Padding:** `p-6`.

### Tables (`.tabla`)
- **Corner Style:** `rounded-2xl`, con `border-separate border-spacing-0`
  para que el radio no se rompa en los bordes de las celdas.
- **Header:** fondo `gray-50`, texto `gray-600` semi-bold.
- **Rows:** borde inferior `gray-100` entre filas, sin borde en la última.

### Inputs / Fields
- **Style:** borde `gray-300`, `rounded-md`, `text-sm`, `px-3 py-2`.
- **Focus:** anillo de foco de 2px en `var(--color-primario)` + borde
  transparente — el único elemento de foco del sistema, y también el único
  input cuyo color de foco cambia por gimnasio.

### Navigation (`.topbar` / `.nav-staff`)
- **Topbar:** blanco, `sticky top-0`, con el logo/nombre del gimnasio a la
  izquierda y "Salir" a la derecha. El logo es dato del `Gimnasio`, no un
  asset del sistema.
- **Nav de staff:** fila de links de texto (`gray-600`, hover `bg-gray-100`),
  nunca íconos — 8 secciones, colapsa detrás de `☰` en mobile.

### Landing pública — horarios de atención
Sección nueva con datos **reales** de ese gimnasio puntual (nunca prueba
social inventada — `PRODUCT.md` documenta que no hay clientes pagos
todavía). Lista los `HorarioAtencion` agrupados por día, cada franja
separada por coma; oculta por completo si el gimnasio no cargó ninguno
(`{% if horarios_por_dia %}`) — nunca una sección vacía. Texto plano, sin
tarjeta propia, sobre el canvas de la landing.

### Banner de suplantación (componente de señal, no de marca)
Único componente que rompe la paleta neutra a propósito: ámbar saturado
(`amber-100`/`amber-300`/`amber-900`) a todo el ancho, arriba de la topbar.
Existe para que sea imposible no notar que se está operando como otra
persona.

## Motion

Primer uso de `@keyframes` del sistema (banda de atletas animados del
login) — no había ningún patrón de movimiento antes de esto.

### Named Rules
**The Restrained Motion Rule.** El movimiento es un acento, no un efecto:
amplitud chica (pocos px o grados), siempre `ease-in-out`, ciclos de 2-3s,
nunca `spring`/bounce. Todo `@keyframes` respeta
`prefers-reduced-motion: reduce` apagando la animación (nunca
reemplazándola por otra). Se usa con moderación — hoy solo la banda de
atletas del login; no es licencia para animar tarjetas o botones del panel.

**Segunda excepción (deliberada): el splash de instalación.**
`.pwa-splash` (`styles/input.css`, disparado por `static/js/pwa.js` en CADA
apertura de la PWA en modo standalone — pedido explícito del dueño del
producto, "un efecto visual parecido a la N de Netflix", que además pidió
que se vea siempre al abrir la app, no solo la primera vez) rompe a
propósito la Restrained Motion Rule: amplitud grande (zoom de 0.3x a 1.15x
con overshoot, `cubic-bezier` con rebote leve), pantalla completa, ~2.2s.
Se justifica porque es un evento de arranque de la app (una marca en
`sessionStorage`, no `localStorage`, evita que se repita dentro de la MISMA
apertura — p.ej. al loguearse o confirmar un pago, navegaciones con
`hx-boost="false"` — pero se resetea sola en la apertura siguiente), no un
patrón de interacción recurrente dentro de una pantalla — sigue sin ser
licencia para animar tarjetas, botones o cualquier otra superficie del
panel. Como toda animación del sistema, respeta `prefers-reduced-motion:
reduce` apagándola (el splash directamente no se ve: `opacity: 0` sin la
animación que la lleva a 1). Se renderiza tanto autenticado (`base.html`,
colores de `user.perfil.gimnasio`) como en el login con estética por
gimnasio (`login.html`, colores del `gimnasio` resuelto por slug/cookie) vía
el partial `partials/pwa_splash.html`, porque abrir la PWA instalada sin
sesión activa cae ahí. El fondo usa
`var(--color-primario)`/`--color-secundario` del gimnasio logueado (The
Runtime Brand Rule), nunca un hex fijo.

## Do's and Don'ts

### Do:
- **Do** referenciar `var(--color-fondo)` / `var(--color-primario)` /
  `var(--color-secundario)` / `var(--font-gimnasio)` para cualquier cosa que
  deba reflejar la marca del gimnasio — nunca un hex o una fuente fija.
- **Do** ofrecer paisajes de color completos y curados (`Gimnasio.PALETAS`),
  nunca un color picker libre para identidad de marca.
- **Do** usar verde/ámbar/rojo exclusivamente para estado, con las tres
  variantes de `.badge` existentes.
- **Do** mantener `shadow-sm` como el único nivel de sombra; diferenciar con
  borde o fondo, no con más sombra.
- **Do** usar `rounded-full` solo para el botón primario, el CTA de landing
  y los badges — todo lo demás denso va en `md`, las superficies en `2xl`.
- **Do** dejar la atmósfera del canvas (`body`/`.landing`) en `color-mix()`
  sobre los tokens del paisaje — nunca un `background-color` sólido y liso.
- **Do** usar animación con la misma moderación que la banda de atletas del
  login — amplitud chica, `prefers-reduced-motion` siempre respetado.

### Don't:
- **Don't** hardcodear un hex de paisaje (`#1d6f56`, `#f5ede4`, etc.) en un
  componente nuevo — son datos de `Gimnasio.PALETAS`, no constantes.
- **Don't** introducir una segunda escala de sombra o elevación tipo
  "lifted"/"floating" — el sistema es intencionalmente plano.
- **Don't** repetir el degradé de blobs del canvas dentro de una tarjeta o
  sección individual — vive en un solo lugar (`body`/`.landing`), no por
  componente.
- **Don't** usar íconos en la navegación de staff — es texto puro hoy.
- **Don't** animar componentes del panel (tarjetas, botones, filas de
  tabla) solo para llamar la atención — el sistema es plano a propósito.
- **Don't** inventar prueba social (números, testimonios) en la landing de
  un gimnasio — solo datos reales de ESE gimnasio (horarios, y lo que se
  agregue después siguiendo el mismo criterio).
- **Don't** cargar Google Fonts para la tipografía default — está
  auto-hospedada a propósito; solo las 4 alternativas la disparan.
