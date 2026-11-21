# Modo oscuro (automático + interruptor) — diseño

Fecha: 2026-11-21. Estado: borrador para aprobación. Pedido del dueño del producto.

## Intención y por qué
El dueño pidió modo oscuro porque lo considera una expectativa básica de cualquier
aplicación hoy, sobre todo en celular (portal del alumno, mobile-first). Quedó
decidido: **automático según el sistema operativo, con un interruptor** para
forzar claro u oscuro. Antes se había desaconsejado (costo y white-label); esa
discusión está cerrada y no se reabre.

## Objetivos
1. Panel de staff y portal del alumno se pueden usar en oscuro con la misma
   legibilidad que en claro (contraste WCAG: texto ≥ 4,5:1, bordes/controles ≥ 3:1).
2. Tres estados: **Automático** (sigue `prefers-color-scheme`), **Claro**, **Oscuro**.
   La elección persiste en el navegador y no produce parpadeo al cargar.
3. Las 4 paletas curadas de gimnasio (`Gimnasio.PALETAS`) tienen **variante oscura
   curada** (decisión del dueño), con la misma regla que hoy: el gimnasio elige un
   paisaje entero, nunca un color suelto.
4. Una sola fuente de verdad: tokens semánticos; ningún componente nuevo hardcodea
   grises ni blanco (extiende «The Runtime Brand Rule» de `DESIGN.md`).

## No objetivos
- Que el gimnasio fuerce o desactive el modo oscuro para sus alumnos.
- Colores oscuros libres o selector de color (rompe «The Landscape Rule»).
- Oscurecer lo que se imprime o se exporta: tarjetas de ingreso imprimibles, PDF,
  QR, comprobantes, mails. Todo eso queda claro siempre (`@media print` fuerza claro).
- Cambiar `theme_color`/`background_color` del manifest PWA (estático por gimnasio).
- Rediseñar nada del tema claro.

## Anti-objetivos (fracaso aunque «funcione»)
- **Cualquier cambio visual en el tema claro.** Modo claro y Automático con SO claro
  tienen que verse idénticos a hoy.
- Texto o iconos ilegibles en alguna combinación paleta × tema.
- Parpadeo claro→oscuro al cargar, o interruptor que no persiste entre páginas
  (`hx-boost` global).
- Logos o fondos propios del gimnasio que desaparecen sobre fondo oscuro.

## Restricciones del proyecto (verificadas)
- `DESIGN.md:241`: superficies blancas y grises fijos «por diseño»; hay que
  actualizar ese documento, no ignorarlo.
- Tailwind v4 compila los colores a variables (`var(--color-gray-700)`,
  `var(--color-white)`, 33 variables; `bg-white` 31 veces en `styles/input.css`).
  Casi no hay color inline en plantillas (3 `style` con color).
- `--color-white` sirve a dos cosas: superficies (`bg-white`) y texto blanco sobre el
  color de marca (botones). **No se puede invertir `--color-white`**: hay que separar.
- `hx-boost="true"` global: htmx no reemplaza los atributos de `<html>` ni el `<head>`;
  el tema vive en `<html data-tema>` y en un script del `<head>`, nunca en `<body>`.
- Fondo propio del gimnasio (imagen o doodle con `mask-image`) y atmósfera del canvas
  (`color-mix` sobre los tokens) ya se arman en `base.html` por request.
- Service worker cache-first de `/static/`; el CSS va con hash (WhiteNoise).
- Sin cambios de modelos de datos para el interruptor (preferencia en `localStorage`).
- Regla del repo: tocar `styles/input.css` exige `npm run build:css`; nada de N+1; la
  suite no sale a la red; nuevos tests de contraste en `tenants/tests.py`.

## Enfoques considerados
- **A (elegido por el dueño): tokens semánticos + remapeo de la paleta de Tailwind.**
  Se definen tokens (superficie, texto, borde, estado) y, bajo el tema oscuro, se
  redefinen las variables de grises/estado (escala invertida por familia). Un solo
  lugar de cambio; el tema claro queda con los mismos valores.
- B: reescribir ~300 reglas con `light-dark()` / `dark:`. Más explícito, pero toca todo
  y es fácil dejar huecos. Descartado.
- C: filtro `invert()`/`hue-rotate`. Rompe fotos, logos y color de marca. Descartado.

## Diseño (A)

### Mecanismo de tema
- `<html data-tema="claro|oscuro">` sólo cuando la persona eligió; sin atributo =
  Automático. Un script **síncrono en el `<head>`**, antes del CSS, lee
  `localStorage["tema"]` y pone el atributo (evita el parpadeo).
- CSS: `:root` (claro) + `@media (prefers-color-scheme: dark) { :root:not([data-tema="claro"]) {…} }`
  + `:root[data-tema="oscuro"] {…}`. Las declaraciones oscuras viven en un solo bloque
  reutilizado (no duplicadas a mano).
- `color-scheme`: `light dark` en `<meta>` y `:root`, y `light`/`dark` forzado por
  atributo, para controles nativos y barras de scroll. Reemplaza el `color-scheme:
  light` agregado en el pase visual.
- `<meta name="theme-color">`: dos metas con `media`, y el interruptor las actualiza.

### Tokens
- Separar `--superficie` (reemplaza los 31 `bg-white` de superficie) de
  `--color-white` (queda constante, para texto sobre color de marca).
- Escala de grises y de las familias de estado (amber/green/red, y las que use el
  CSS) invertida por familia bajo el tema oscuro; los pares de texto/fondo de estado
  se verifican uno por uno (no se asume que invertir alcanza).
- `color-mix(in oklab, … white|black)` del CSS actual: revisar cada uno (7 usos con
  `white`/`black` literal, además de los de `transparent` que ya siguen a los tokens).
- Sombra única `shadow-sm`: en oscuro se mantiene el sistema plano; la separación
  sigue siendo el borde (no se agregan sombras ni elevaciones nuevas).

### Paletas con variante oscura
`Gimnasio.PALETAS` pasa a tener, por paisaje, además de `fondo/primario/secundario`,
las tres de oscuro y el color del texto sobre el primario (`sobre_primario`), porque
un primario aclarado para fondo oscuro puede necesitar texto oscuro encima.
`base.html` inyecta ambos juegos (claro y oscuro) en el `<style>` por request, igual
que hoy. Valores iniciales propuestos (llamada mía, a refinar con el test de
contraste y mirando la pantalla):

| Paisaje | fondo oscuro | primario oscuro | secundario oscuro |
|---|---|---|---|
| Bosque | `#15201b` | `#4fb08c` | `#f09a86` |
| Océano | `#0f1923` | `#6f9bd1` | `#e8b45e` |
| Arena | `#1d1814` | `#e08a5f` | `#5fa79c` |
| Pizarra | `#14181d` | `#8fa6bd` | `#86b184` |

Un test (extensión de `ContrasteDePaletasTests`) exige, para cada paleta en oscuro:
`sobre_primario` sobre `primario` ≥ 4,5; `primario` sobre la superficie oscura ≥ 3;
texto principal sobre `fondo` ≥ 4,5.

### Interruptor
- Control de tres estados (Automático · Claro · Oscuro) en la barra superior, para
  staff y alumno, accesible por teclado, con etiqueta visible o `aria-label`,
  y objetivo táctil de 44 px. Es un componente nuevo: se documenta en `DESIGN.md`.
- Guarda `localStorage["tema"]` (`claro`/`oscuro`; borrar la clave = Automático).
- Sin JS el sitio sigue funcionando (Automático por CSS).

### Casos especiales
- **Logo del gimnasio:** sobre una ficha clara en oscuro, para que un logo oscuro con
  fondo transparente no desaparezca (llamada mía).
- **Fondo propio (imagen/doodle):** el velo ya usa `color-mix` sobre `--color-fondo`,
  así que sigue la variante oscura; verificar que la imagen no «brille» (llamada mía:
  más velo en oscuro).
- **Gráficos del dashboard:** rampa secuencial y paleta categórica de `DESIGN.md`
  necesitan versión para superficie oscura (validar con la skill `dataviz`); la leyenda
  y la tabla accesible se mantienen.
- **Impresión:** `@media print` fuerza tema claro.
- **Vista previa en vivo** de «Mi gimnasio» (`gimnasio_form.html`): mostrar también
  cómo queda la paleta en oscuro.

## Alcance por fases (cada una se puede mergear sola)
0. **Refactor a tokens sin cambio visual:** separar `--superficie`, centralizar
   grises/estado; verificar que el tema claro es idéntico (comparación de estilos
   computados en pantallas clave).
1. **Oscuro en panel de staff y portal del alumno + paletas + interruptor + persistencia
   + script anti-parpadeo.** Tests de contraste.
2. **Resto de pantallas con sesión:** tienda, pagos, ingreso, turnos, importaciones,
   plataforma (superadmin); gráficos; componentes con color propio.
3. **Pantallas públicas:** login por gimnasio y landing `g/<slug>/`.
4. **Portada `/` y maquetas de producto:** al final y opcional (las maquetas son una
   demostración con `data-demo`; llamada mía: la portada queda **solo clara** hasta
   decidir).

## Llamadas mías tras la revisión del lector (revisables)
- **Alcance del plan:** se planifican en detalle las fases 0 y 1; las fases 2–4 quedan
  como esquema y se detallan cuando la anterior esté mergeada y verificada.
- **«Tema claro idéntico»:** la compuerta de la fase 0 es un volcado con script de los
  estilos computados de pantallas nombradas, antes y después. Se admite sólo una
  diferencia de redondeo (±1/255 por canal, p. ej. por `color-mix` en oklab); cualquier
  otra diferencia bloquea el merge.
- **Separar blanco de superficie y blanco de texto:** se clasifica uso por uso durante
  la fase 0 (nada de reemplazo ciego); los 31 `bg-white` son superficie salvo evidencia.
- **Pares de contraste de estado:** la lista de pares texto/fondo se arma desde el CSS
  compilado y queda como test (no como documento aparte).
- **Interruptor:** en la barra superior; si a 390 px no entra con 44 px, pasa al menú
  de navegación (alumno: dentro de su `☰`). Se decide mirando la pantalla.
- **Valores de la tabla de paletas oscuras:** son puntos de partida; el test de contraste
  puede cambiarlos y `sobre_primario` lo elige quien construye.
- **Vista previa de «Mi gimnasio»:** una sola vista previa que sigue el tema actual de la
  página (el interruptor ya permite ver el otro); sin vista lado a lado.
- **Logo en oscuro:** ficha blanca fija con esquinas redondeadas.

## Qué queda a criterio del que construye
Valores finales de la escala invertida y de las variantes oscuras (siempre sujetos al
test de contraste y a mirar la pantalla), nombres de los tokens, cómo reutilizar el
bloque oscuro sin duplicarlo, y el orden de pantallas dentro de cada fase.

## Verificación
- Tests de contraste por paleta (claro y oscuro) y de que el `<head>` trae el script
  del tema antes del CSS.
- Navegador real, 4 paletas × claro/oscuro, en staff, alumno y celular (iframe de
  390 px); modo Automático cambiando el tema del sistema; persistencia entre páginas
  con `hx-boost`; recarga sin parpadeo.
- Tema claro idéntico al actual (estilos computados antes/después).
- Suite completa (línea base 1723 + los tests nuevos; el test de `DateInput` falla
  por `.worktrees/` y no es regresión).
- Verificar en producción el CSS hasheado, no el hash local.
