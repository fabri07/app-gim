---
name: TuGimApp
description: Panel operativo multi-tenant para gimnasios y entrenadores locales, con blanco-etiquetado en vivo por gimnasio.
colors:
  primary: "#2563eb"
  primary-deep: "#1e40af"
  neutral-canvas: "oklch(98.5% .002 247.839)"
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
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "clamp(2.25rem, 5vw, 3rem)"
    fontWeight: 700
    lineHeight: 1.1
  title:
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  headline:
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.05em"
  metrica:
    fontFamily: "{tipografía elegida por el gimnasio}, var(--font-sans)"
    fontSize: "1.875rem"
    fontWeight: 700
rounded:
  md: "0.375rem"
  lg: "0.5rem"
  xl: "0.75rem"
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
    rounded: "{rounded.md}"
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
    rounded: "{rounded.xl}"
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

**Creative North Star: "El Mostrador Neutral"**

Esta es una fotografía del sistema **tal como existe hoy** — un panel de
back-office deliberadamente discreto, pensado para que el mostrador (la app)
nunca compita con el cartel del negocio (el gimnasio). El canvas es gris
claro y uniforme, las tarjetas son blancas con un borde y una sombra apenas
perceptible, y hay un único color de acento — hoy azul por defecto — que
cada gimnasio pisa en tiempo real con el suyo (`--color-primario`/
`--color-secundario`, sobreescritas inline por `base.html` según el
`Gimnasio` logueado). La tipografía sigue la misma lógica: por defecto es la
pila de sistema de Tailwind, y el gimnasio puede elegir una de cinco fuentes
curadas de Google Fonts sin que el resto del sistema cambie de forma.

El resultado es un sistema que se nota poco a propósito: dos densidades de
texto (títulos semi-bold cortos, cuerpo gris regular), radios de esquina
consistentes en todos los contenedores, y color reservado casi enteramente
para estado (verde/ámbar/rojo en badges) en vez de decoración. La única
excepción real es la landing pública (`landing.html`), la primera superficie
del proyecto pensada para persuadir en vez de operar: ahí el degradé
primario/secundario del gimnasio ocupa el hero a todo el ancho.

**Este documento registra el sistema actual como línea de base.** Hay una
dirección nueva ya conversada — acercar la estética a `crossfyapp.com`,
manteniendo el blanco-etiquetado por gimnasio — que todavía no se
implementó; cuando eso avance, este archivo se reemplaza a través del flujo
de creación/reemplazo de mundo visual, no se edita a mano encima de esto.

**Key Characteristics:**
- Canvas gris neutro (`gray-50`) con tarjetas blancas de sombra mínima.
- Un solo acento de marca, sobreescrito en tiempo real por gimnasio — nunca
  hardcodeado en un componente nuevo.
- Color reservado para estado (verde/ámbar/rojo), no para decoración.
- Radios de esquina consistentes: `md` en controles, `xl` en contenedores.
- Tipografía intercambiable por gimnasio sin tocar la jerarquía de tamaños.
- Una sola superficie "Persuade" (la landing pública); el resto es "Operate".

## Colors

La paleta es casi enteramente neutra; el color con intención de marca es un
solo par (primario/secundario) que además es dato de runtime, no una
decisión de diseño fija.

### Primary
- **Azul de arranque** (`#2563eb`): valor por defecto de `--color-primario`,
  usado en enlaces, el botón principal, focus rings y — junto al secundario —
  el degradé del hero de la landing. Es un placeholder honesto: en producción,
  cada gimnasio cliente lo reemplaza por el suyo vía `Gimnasio.color_primario`.
- **Azul de arranque, oscuro** (`#1e40af`): `--color-secundario`, compañero
  del primario en el degradé del hero; fuera de la landing casi no se usa
  solo.

### Neutral
- **Canvas** (`oklch(98.5% .002 247.839)`, gray-50): fondo de página.
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

### Estado (semántico, no de marca)
Verde/ámbar/rojo comunican **estado**, nunca identidad — se mantienen fijos
sin importar el color elegido por el gimnasio.
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
**deliberadamente distinto del color de marca**: es un canal de codificación
de datos, no branding, así que no se pisa por gimnasio.
`#b7d3f6` → `#6da7ec` → `#2a78d6` → `#184f95` (claro a oscuro).

### Named Rules
**The Runtime Brand Rule.** Ningún componente nuevo hardcodea un hex de
marca. Todo lo que deba reflejar la identidad del gimnasio referencia
`var(--color-primario)` / `var(--color-secundario)` — son datos de
`Gimnasio`, sobreescritos por request en `base.html`, nunca constantes de
Tailwind en build-time.

## Typography

**Fuente por defecto:** la pila de sistema de Tailwind (`var(--font-sans)`:
`ui-sans-serif, system-ui, sans-serif, ...`).
**Fuentes opcionales por gimnasio:** Inter, Montserrat, Poppins, Oswald,
Playfair Display (Google Fonts, cargadas solo si el gimnasio la eligió).

**Character:** utilitaria y sin ceremonia por defecto — títulos semi-bold
cortos, cuerpo regular gris. La fuente cambia por gimnasio; el peso y el
tamaño de cada rol, no.

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

Sistema **plano por defecto, con una sola sombra**. `shadow-sm`
(`0 1px 3px 0 rgba(0,0,0,.1), 0 1px 2px -1px rgba(0,0,0,.1)`) aparece en
tarjetas, tablas y métricas — siempre en reposo, nunca como respuesta a
hover o estado. No hay una escala de elevación (no hay `shadow-md`/`shadow-
lg` en uso): la profundidad es casi enteramente de borde (`border
border-gray-200`), no de sombra. La sombra es un acabado, no un lenguaje de
capas.

### Named Rules
**The One Shadow Rule.** Un solo nivel de sombra en todo el sistema. Si un
componente nuevo necesita distinguirse, se hace con borde o con fondo
(`bg-gray-50` vs. `bg-white`), no agregando una sombra más fuerte.

## Shapes

Radios de esquina consistentes por categoría, sin bordes decorativos:
- **`md`** (`0.375rem`/6px): controles — botones, inputs, filas de nav.
- **`lg`** (`0.5rem`/8px): contenedor de filtros.
- **`xl`** (`0.75rem`/12px): tarjetas, tablas, métricas — el radio "de
  superficie" del sistema.
- **`2xl`** (`1rem`/16px): el marco del logo en la landing pública.
- **`full`**: badges de estado (píldora).

Bordes de 1px (`border-gray-200`) en casi todo — cards, tablas, inputs,
topbar. Ningún componente usa esquinas rectas junto a otros con esquina
redondeada dentro de la misma jerarquía visual.

## Components

### Buttons
- **Shape:** `rounded-md` (6px), `px-4 py-2`, `text-sm font-medium`.
- **Primary (`.boton`):** fondo `var(--color-primario)`, texto blanco.
  Hover: `opacity-90`. Es el único botón cuyo color cambia por gimnasio.
- **Secondary (`.boton-secundario`):** borde gris, fondo blanco, texto
  `gray-700`. Hover: `bg-gray-50`.
- **Danger (`.boton-peligro`):** fondo `red-600`, texto blanco, hover
  `red-700` — reservado para acciones destructivas confirmadas.

### Badges
- **Style:** píldora (`rounded-full`), `px-2.5 py-0.5`, `text-xs font-medium`.
- **State:** tres variantes fijas — `--ok` (verde), `--alerta` (ámbar),
  `--riesgo` (rojo) — nunca un cuarto color; el estado que no encaja en
  ninguna se omite, no se inventa una variante nueva.

### Cards / Containers
- **Corner Style:** `rounded-xl` (12px).
- **Background:** blanco sobre canvas `gray-50`.
- **Shadow Strategy:** `shadow-sm`, siempre en reposo (ver Elevation).
- **Border:** `border border-gray-200`.
- **Internal Padding:** `p-6`.

### Tables (`.tabla`)
- **Corner Style:** `rounded-xl`, con `border-separate border-spacing-0`
  para que el radio no se rompa en los bordes de las celdas.
- **Header:** fondo `gray-50`, texto `gray-600` semi-bold.
- **Rows:** borde inferior `gray-100` entre filas, sin borde en la última.

### Inputs / Fields
- **Style:** borde `gray-300`, `rounded-md`, `text-sm`, `px-3 py-2`.
- **Focus:** anillo de foco de 2px en `var(--color-primario)` + borde
  transparente (`focus:ring-2 focus:ring-[var(--color-primario)]
  focus:border-transparent`) — el único elemento de foco del sistema, y
  también el único input cuyo color de foco cambia por gimnasio.

### Navigation (`.topbar` / `.nav-staff`)
- **Topbar:** blanco, `sticky top-0`, con el logo/nombre del gimnasio a la
  izquierda y "Salir" a la derecha. El logo es dato del `Gimnasio`, no un
  asset del sistema.
- **Nav de staff:** fila de links de texto (`gray-600`, hover `bg-gray-100`),
  nunca íconos — 8 secciones, colapsa detrás de `☰` en mobile.

### Banner de suplantación (componente de señal, no de marca)
Único componente que rompe la paleta neutra a propósito: ámbar saturado
(`amber-100`/`amber-300`/`amber-900`) a todo el ancho, arriba de la topbar.
Existe para que sea imposible no notar que se está operando como otra
persona.

## Do's and Don'ts

### Do:
- **Do** referenciar `var(--color-primario)` / `var(--color-secundario)` /
  `var(--font-gimnasio)` para cualquier cosa que deba reflejar la marca del
  gimnasio — nunca un hex o una fuente fija.
- **Do** usar verde/ámbar/rojo exclusivamente para estado, con las tres
  variantes de `.badge` existentes.
- **Do** mantener `shadow-sm` como el único nivel de sombra; diferenciar con
  borde o fondo, no con más sombra.
- **Do** seguir el patrón de radios por categoría: `md` en controles, `xl`
  en superficies (tarjetas/tablas), `full` en badges.

### Don't:
- **Don't** hardcodear `#2563eb` (o cualquier hex de marca) en un componente
  nuevo — es un placeholder de arranque, no el color del sistema.
- **Don't** introducir una segunda escala de sombra o elevación tipo
  "lifted"/"floating" — el sistema es intencionalmente plano.
- **Don't** usar íconos en la navegación de staff — es texto puro hoy.
- **Don't** tratar este archivo como la dirección final: es la fotografía
  del sistema actual, no la dirección crossfy-inspirada ya conversada con el
  usuario, que todavía no se implementó.
