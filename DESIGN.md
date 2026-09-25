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
  display-producto:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "clamp(2.3rem, 4.4vw, 3.5rem)"
    fontWeight: 800
    lineHeight: 1.03
    letterSpacing: "-0.035em"
  headline-producto:
    fontFamily: "'Plus Jakarta Sans', var(--font-sans)"
    fontSize: "clamp(1.9rem, 3.6vw, 2.75rem)"
    fontWeight: 800
    lineHeight: 1.06
    letterSpacing: "-0.03em"
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
  pildora-portada:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.full}"
    padding: "14px 28px"
  pildora-portada-chica:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.full}"
    padding: "8px 16px"
  pildora-portada-contorno:
    backgroundColor: "transparent"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    padding: "8px 16px"
  pildora-portada-clara:
    backgroundColor: "#ffffff"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    padding: "14px 28px"
  auth-card:
    backgroundColor: "#ffffff"
    rounded: "{rounded.2xl}"
    padding: "36px"
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
material — tipografía pesada, paleta cálida, energía de landing de venta).
El panel no la calca composición por composición; el **sitio público del
producto** (la portada `/` y el login genérico) sí toma su estructura —
nav fija, hero con el producto a la vista, funciones alternadas, precios
sobre un campo de marca — a pedido del dueño del producto: **su estructura,
nuestra voz**.

El resultado: la marca del producto vive en la forma (tipografía, radio,
forma de botón), no en un color fijo. El color es lo que cada gimnasio
"pone sobre la mesa", elegido de un catálogo — nunca libre — para que
ninguna combinación resulte ilegible. **Bosque** (crema + verde bosque +
coral) es el paisaje por defecto y el que usa el propio sistema cuando
todavía no hay un gimnasio en contexto (login, error 404, etc.).

Persuade tiene dos superficies. La landing pública de cada gimnasio lleva
la energía completa con SUS colores: degradé de marca a todo el ancho del
hero, números y botones grandes. El sitio del producto (portada + login
genérico) habla con la voz de TuGimApp sobre el paisaje Bosque: titulares
en 800 con tracking cerrado, y como prueba el producto mismo — pantallas
de la app dibujadas en HTML, con datos de ejemplo rotulados como tales, que
el visitante recolorea con los cuatro paisajes.
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
- El sitio público del producto se prueba con el producto: maquetas HTML
  de la UI real que cambian de paisaje en vivo, nunca capturas raster ni
  prueba social inventada.
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
build-time. Vale también para la portada: los chips de paisaje leen sus
colores de `Gimnasio.PALETAS` (vía `paletas_demo`), no de hex copiados al
template.

**The Demo-Only Recolour Rule.** En la portada, elegir un paisaje escribe
`--color-fondo`/`--color-primario`/`--color-secundario` sobre cada
`[data-demo]` (la maqueta del hero y los cuatro campos de funciones) y
NUNCA sobre `:root`: la página en sí sigue siendo de TuGimApp en Bosque; lo
que cambia es el ejemplo de lo que vería el gimnasio. Todo lo que tenga que
seguir al selector se marca `data-demo` o vive dentro de uno.

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
  landing pública de un gimnasio (`.landing__titulo`, `text-4xl sm:text-5xl
  font-bold`, blanco sobre el degradé de marca). Es el único uso de esta
  escala.
- **Display de producto** (800, `clamp(2.3rem, 4.4vw, 3.5rem)`, 1.03,
  `-0.035em`): titular del sitio público de TuGimApp — el hero de la portada
  (`.portada__titulo`) y el login genérico (`.auth-hero__titulo--producto`).
  Dos oraciones cortas; la segunda va en `var(--color-primario)` en su propio
  renglón (`__titulo-marca`). `text-wrap: balance`.
- **Headline de producto** (800, `clamp(1.9rem, 3.6vw, 2.75rem)`, 1.06,
  `-0.03em`): `h2` de sección de la portada y el titular del cierre. Los
  subtítulos de función y de paso bajan a `text-2xl`/`sm:text-3xl` y
  `text-xl`, siempre 800 con tracking negativo.
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
**The Optical Punctuation Rule.** En los titulares del sitio público (800,
tamaño display), Plus Jakarta Sans trae el punto y la coma con mucho aire a
la izquierda y se leen como «app .». Esa puntuación va envuelta en `.pt`
(`margin-left: -0.1em`). Solo en titulares de ese peso y tamaño, nunca en
texto corrido.

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
- **Sitio público** (`max-w-6xl`, ~72rem, `px-4`): portada y login genérico.
  Nav fija de 64px; secciones separadas por `pt-24`/`sm:pt-28`; el hero y el
  login parten en dos columnas desde `lg` (copy a la izquierda, producto o
  formulario a la derecha) y apilan abajo de eso. Las funciones alternan
  texto/demo en dos columnas desde `md`. Precios es la única franja a todo
  el ancho.

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
`.metrica`, que siguen sólo con `shadow-sm` + borde. `body` lleva
`min-height: 100dvh` para que en una página más corta que la ventana (el
login) la atmósfera llegue hasta abajo en vez de cortarse seca contra el
color plano.

Las maquetas de producto de la portada (ver Components) toman su
profundidad del **bisel oscuro** (`gray-900`) del marco del dispositivo, con
el mismo `shadow-sm` de siempre: es una excepción declarada de material,
no de sombra — la One Shadow Rule sigue intacta.

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

**Excepción declarada, válida solo en las maquetas de producto:** el marco
del celular dibujado (`.disp-celu`, `2.25rem`, y su pantalla, `1.75rem`) cae
fuera de la escala. Es la silueta de un teléfono, una ilustración, no una
superficie de la UI; la UI de adentro sí usa `md`/`2xl`/`full`. Ningún
componente real toma estos radios.

### Named Rules
**The Two-Radius Rule.** Solo dos escalones de radio conviven en cualquier
jerarquía visual: `md` en controles densos, `2xl` en superficies — más
`full` como caso especial para botones primarios, píldoras de CTA, chips de
paisaje y badges. Nunca un tercer escalón intermedio.

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
- **Neutro (`--neutro`, gris):** para lo que **no es un estado** — el código de
  bloque de una superserie (`A1`), un "Ya existe" del importador, un plan
  "Finalizada". No es el cuarto color de estado: los estados siguen siendo
  tres, y esta variante existe justamente para no teñir de verde/ámbar/rojo
  una etiqueta que no comunica ninguno. Antes esos cinco lugares usaban
  `.badge` a secas, que solo aporta forma: salían como texto suelto con un
  padding raro.

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

### Hojas de semana del alumno (`.semanas-carrusel` / `.semana-hoja`)

La única superficie del sistema donde una MISMA pantalla tiene dos formas
distintas según el ancho, y no un reflujo de la misma. En escritorio, la tabla
de 4 semanas lado a lado (comparar la progresión). Abajo de `sm`, un carrusel
con imán: una hoja por semana a pantalla completa, abierta en la semana en
curso, con la siguiente asomando (`w-[92%]` + `snap-start`) para enseñar el
gesto sin un cartel que lo explique. Es adaptación de contenido, no de escala:
el alumno parado en el gimnasio mira la semana de hoy y un ejercicio a la vez.

- **Superficie:** `.tarjeta` (misma sombra, mismo radio, mismo borde) con `p-4`
  en vez de `p-6` — 24px por lado en una pantalla de 360 son 48px de contenido
  perdidos.
- **Prescripción** (`.prescripcion`): etiqueta en el rol **Label** arriba y
  valor en `text-xl font-bold tabular-nums` abajo — la misma forma que
  `.ficha-datos`, un escalón más grande. Es el único dato del sistema pensado
  para leerse a un brazo de distancia fuera del dashboard, y por eso toma
  prestada la voz del rol **Métrica** sin su tamaño. `tabular-nums` es
  obligatorio: con las cifras proporcionales de Plus Jakarta Sans, los números
  no alinean entre hojas.
- **Pestañas** (`.semanas-pestanas`): control segmentado con doble señal, a
  propósito — `var(--color-primario)` marca «la que estás viendo» (el uso de
  acento que Operate permite para selección actual) y un punto ámbar marca «la
  semana en curso». Nunca significan lo mismo. El punto va `absolute`: como
  ítem del flex empujaba el texto a dos renglones.
- **Estado:** `.badge--alerta` («Actual») y `.badge--ok` («Entrenado»), el
  vocabulario de estado de siempre. Ningún color nuevo.
- **Acción:** una sola por hoja, a lo ancho y al pie («Marcar como entrenado»),
  que es donde llega el pulgar. Los controles táctiles de la hoja miden 44px.

### Landing pública — horarios de atención
Sección nueva con datos **reales** de ese gimnasio puntual (nunca prueba
social inventada — `PRODUCT.md` documenta que no hay clientes pagos
todavía). Lista los `HorarioAtencion` agrupados por día, cada franja
separada por coma; oculta por completo si el gimnasio no cargó ninguno
(`{% if horarios_por_dia %}`) — nunca una sección vacía. Texto plano, sin
tarjeta propia, sobre el canvas de la landing.

### Sitio público del producto (portada `/` y login genérico)

Familia propia de Persuade para TuGimApp mismo, sin gimnasio en contexto:
resuelve al paisaje Bosque del `:root` y usa el canvas atmosférico de `body`.

- **Nav pública (`.portada-nav`, partial `portada_nav.html`):** fija arriba,
  64px, fondo `--color-fondo` al 94% con `backdrop-filter` blur; al dejar el
  tope gana un borde inferior del primario mezclado al 14% (`--scroll`).
  Isotipo (cuadrado `md` primario con un punto secundario) + «TuGimApp» en
  800; anclas de texto (Funciones/Precios/Preguntas, `gray-600`, solo desde
  `md`); «Ingresar» como link y la píldora chica de prueba. La comparten la
  portada y el login genérico: `anclas_base="/"` hace que las anclas vuelvan
  a la portada, y `en_login` oculta «Ingresar» y pasa la píldora a contorno.
  Nunca va en el login de un gimnasio.
- **Píldora (`.portada-pildora`):** una sola forma para todos los CTA del
  sitio — `rounded-full`, `px-7 py-3.5`, 700, fondo `var(--color-primario)`,
  hover al primario mezclado 84% con negro. Variantes: `--chica` (nav),
  `--clara` (blanca con texto primario, sobre campos de color) y `--contorno`
  (transparente con anillo interior de 2px). En el login la píldora de la nav
  va en contorno para que **«Entrar» sea la única acción llena** de la
  pantalla.
- **Maquetas de producto (`.maqueta`, `.disp-*`, `.app-*`, `.mini`):**
  ilustraciones HTML de la UI real — panel de escritorio con bisel oscuro y
  el portal del alumno en un celular superpuesto; en las funciones, una
  `.mini` (tarjeta `2xl` con `shadow-sm`) sobre un campo `2xl` del primario
  al 13% (o del secundario al 18% en las invertidas). Usan las etiquetas y
  el vocabulario reales (badges de estado, píldora primaria, prescripción en
  `tabular-nums`) con **datos de ejemplo rotulados como tales** junto a cada
  una. El celular es un partial (`portada_celular.html`) compartido con el
  login genérico.
- **Selector de paisaje (`.paisajes__chip`):** chips píldora blancos con una
  muestra partida primario/secundario; el activo lleva `aria-pressed` y borde
  del primario de SU paisaje. Recolorea solo los `[data-demo]` (The
  Demo-Only Recolour Rule) con un fundido de 0.45s.
- **Precios:** franja a todo el ancho en `color-mix(primario 72%, black)`,
  tres tarjetas blancas `2xl` **iguales**, cada una con la misma píldora.
  Ningún tramo se destaca: el tramo no se elige, lo define la cantidad de
  alumnos activos, y resaltar uno sería un «Popular» sin datos que lo
  respalden. Montos en `tabular-nums`, USD y su equivalente en pesos.
- **Login (`.auth-card`):** tarjeta `2xl` blanca con `shadow-sm`, formulario
  campo por campo (label 700, input `md` con `px-4 py-3`, foco de 2px en el
  primario), error general visible en `.auth-card__error` (rojo de riesgo,
  arriba del form) y error por campo debajo de cada input. «Entrar» es la
  píldora a lo ancho; Google, una píldora blanca con borde. El login genérico
  usa la nav pública, la voz del producto (Display de producto) y el celular
  compartido; `g/<slug>/login/` conserva la topbar de siempre y los colores
  de ESE gimnasio (`.auth-hero--gimnasio`): ahí la marca es la del gimnasio.

### Banner de suplantación (componente de señal, no de marca)
Único componente que rompe la paleta neutra a propósito: ámbar saturado
(`amber-100`/`amber-300`/`amber-900`) a todo el ancho, arriba de la topbar.
Existe para que sea imposible no notar que se está operando como otra
persona.

## Motion

Dos patrones de movimiento conviven, los dos en el sitio público de la
portada, más el splash de la PWA como excepción aparte:
- **Entrada del hero** (`portada-subir`): el copy y la maqueta suben 14px
  con fundido, escalonados (0.8s y 0.9s con 0.12s de retraso),
  `cubic-bezier(0.16, 1, 0.3, 1)` — una sola vez, al cargar.
- **Recoloreo por paisaje**: `background-color`/`color`/`border-color`
  transicionan 0.45s `ease` dentro de las maquetas cuando cambia el paisaje.

Nada más se mueve en la página: el revelado por sección al hacer scroll se
probó y se retiró en la revisión. (La banda de atletas animados que tuvo el
login ya no existe.)

### Named Rules
**The Restrained Motion Rule.** El movimiento es un acento, no un efecto:
amplitud chica (pocos px), duración corta, desaceleración suave, nunca
`spring`/bounce. Todo `@keyframes` y toda transición decorativa respeta
`prefers-reduced-motion: reduce` apagándose (nunca reemplazándose por otra
animación). Hoy son solo la entrada del hero de la portada y el recoloreo
de sus maquetas; no es licencia para animar tarjetas o botones del panel.

**Excepción deliberada: el splash de instalación.**
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
animación que la lleva a 1). `base.html` lo incluye tanto autenticado
(colores de `user.perfil.gimnasio`) como en el login con estética por
gimnasio (colores del `gimnasio` resuelto por slug/cookie) vía el partial
`partials/pwa_splash.html`, porque abrir la PWA instalada sin
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
- **Do** usar `rounded-full` solo para el botón primario, las píldoras de CTA
  (landing y portada), los chips de paisaje y los badges — todo lo demás
  denso va en `md`, las superficies en `2xl`.
- **Do** dejar la atmósfera del canvas (`body`/`.landing`) en `color-mix()`
  sobre los tokens del paisaje — nunca un `background-color` sólido y liso.
- **Do** usar animación con la misma moderación que la entrada del hero de
  la portada — amplitud chica, una sola vez, `prefers-reduced-motion`
  siempre respetado.
- **Do** mostrar el producto con maquetas HTML de la UI real y datos de
  ejemplo rotulados como tales, recoloreables con los paisajes — nunca una
  captura raster genérica.
- **Do** dejar una sola acción llena por pantalla en el sitio público: en el
  login es «Entrar»; la píldora de la nav pasa a contorno.

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
- **Don't** destacar un tramo de precios («Popular», «Recomendado», borde o
  color distinto) — los tres tramos van iguales.
- **Don't** escribir los colores de un paisaje de demostración sobre
  `:root`: solo sobre `[data-demo]`.
- **Don't** llevar los radios del marco del celular (`2.25rem`/`1.75rem`) ni
  el bisel oscuro fuera de las maquetas de producto.
