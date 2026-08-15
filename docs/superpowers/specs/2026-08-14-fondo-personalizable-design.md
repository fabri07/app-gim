# Fondo personalizable del gimnasio

## Problema

Desde el rediseño "Un Paisaje por Gimnasio" (2026-08-13, `85ca0a3`), el canvas
de fondo (`body` en el panel/portal, `.landing` en la vidriera pública) lleva
una atmósfera de 3 blobs radiales suaves sobre los colores del paisaje
elegido ("The Atmospheric Canvas Rule", `DESIGN.md`). Es prolijo y
consistente, pero el dueño de un gimnasio no tiene forma de traer nada propio
al fondo: ni una foto de su local/alumnos, ni una identidad más "de gimnasio"
que un degradé de color.

## Decisiones

Tomadas con el dueño del producto en brainstorming. No reabrir sin motivo
nuevo.

| Tema | Decisión |
|---|---|
| Dónde aplica | Landing pública, portal del alumno y panel de staff — las tres superficies |
| Modos disponibles | 3, elegibles por el dueño: paisaje de color (el actual, default), imagen propia, doodle temático curado |
| Origen de los doodles | Curados a mano por el proyecto (Impeccable), no un banco externo — mismo criterio cerrado que los 4 paisajes |
| Límites de imagen propia | ≤ 5 MB, resolución mínima 1280×720, solo JPEG/PNG |
| Color de botones/acentos (`primario`/`secundario`) | Siempre viene de `paleta`, sin importar `fondo_tipo` — nunca se personaliza junto con la imagen/doodle |

### Por qué `primario`/`secundario` no se tocan

Se evaluó extraer el color dominante de la imagen propia (mismo mecanismo que
ya existe para el logo en `tenants/paisaje_matching.py`) y usarlo también
para acentos/botones. Se descarta: sumaría una segunda fuente de color no
armonizada específicamente para eso (a diferencia de los 4 paisajes, que sí
están curados para ser legibles combinados) y el riesgo de una combinación
imagen+acento ilegible no se justifica frente a la mejora. `paleta` sigue
siendo la única fuente de `--color-primario`/`--color-secundario` pase lo que
pase con el fondo — mismo espíritu que "The Landscape Rule" ya aplica a la
sugerencia de paisaje por logo.

## Diseño

### Modelo: `tenants/models.py::Gimnasio`

Tres campos nuevos. `paleta` no cambia.

```python
class FondoTipo(models.TextChoices):
    COLOR = "color", "Paisaje de color"
    IMAGEN = "imagen", "Imagen propia"
    DOODLE = "doodle", "Doodle temático"

class Doodle(models.TextChoices):
    MANCUERNAS = "mancuernas", "Mancuernas"
    SOGAS = "sogas", "Sogas de battle rope"
    DISCOS = "discos", "Discos apilados"
    KETTLEBELL = "kettlebell", "Kettlebells"

fondo_tipo = models.CharField(
    max_length=10, choices=FondoTipo.choices, default=FondoTipo.COLOR,
)
fondo_imagen = models.ImageField(upload_to="fondos/", blank=True)
fondo_doodle = models.CharField(
    max_length=20, choices=Doodle.choices, blank=True,
)
```

`default=FondoTipo.COLOR` es la pieza que evita backfill: todo gimnasio
existente sigue viendo exactamente la atmósfera de blobs de hoy sin
migración de datos, mismo patrón que `0005_paleta_curada`.

### Validación de `fondo_imagen`

`GimnasioForm.clean_fondo_imagen()`, en `tenants/forms.py`:

- Rechaza si `archivo.size > 5 * 1024 * 1024`.
- Abre el archivo con `PIL.Image.open` (mismo import que ya usa
  `paisaje_matching.py`) y rechaza si `width < 1280 or height < 720`.
- Deja pasar solo `image/jpeg`/`image/png` (`Image.open(...).format`).

Se valida en el form, no en el modelo: mismo criterio que el resto del
proyecto (`GimnasioForm` ya es donde vive la validación de campos
editables por el dueño).

### Renderizado: `templates/base.html` y `templates/tenants/landing.html`

Ambos ya inyectan `--color-fondo`/`--color-primario`/`--color-secundario`/
`--font-gimnasio` en un bloque `<style>` por request (`base.html:15-29`,
`landing.html:6` inline). Se extiende ESE mismo bloque con una rama por
`fondo_tipo` que sobreescribe `background-image` de `body`/`.landing` — gana
por orden de cascada sobre la regla de `styles/input.css` (`body { ... }` en
`@layer base`, `styles/input.css:41-60`) porque el `<style>` inyectado se
declara después del `<link>` a `app.css`, misma especificidad de selector.

- **`color`** (default): sin cambios — sigue la atmósfera de 3
  `radial-gradient()` de siempre.
- **`imagen`**: reemplaza `background-image` por la foto subida, con un velo
  de `--color-fondo` encima (vía `color-mix()`, mismo criterio de "nunca un
  hex fijo" que ya sigue el resto del sistema) para que `.tarjeta`/`.tabla`
  sigan legibles:
  ```css
  body {
    background-image:
      linear-gradient(color-mix(in oklab, var(--color-fondo) 55%, transparent), color-mix(in oklab, var(--color-fondo) 55%, transparent)),
      url("{{ gimnasio.fondo_imagen.url }}");
    background-size: cover;
    background-position: center;
    background-attachment: scroll; /* nunca fixed -- jank en mobile Safari, ya documentado en DESIGN.md */
  }
  ```
- **`doodle`**: el SVG curado (`static/img/doodles/<doodle>.svg`, monocromo)
  se aplica como `mask-image` sobre un elemento propio, con
  `background-color: var(--color-secundario)` como "tinta" — así el mismo
  archivo estático sirve para cualquier gimnasio sin generar SVGs por
  request:
  ```css
  body { background-image: none; }
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: -1;
    background-color: color-mix(in oklab, var(--color-secundario) 22%, transparent);
    -webkit-mask-image: url("{% static 'img/doodles/'|add:gimnasio.fondo_doodle|add:'.svg' %}");
    mask-image: url("{% static 'img/doodles/'|add:gimnasio.fondo_doodle|add:'.svg' %}");
    -webkit-mask-repeat: repeat;
    mask-repeat: repeat;
    -webkit-mask-size: 180px;
    mask-size: 180px;
  }
  ```
  El prefijo `-webkit-` es necesario para Safari/iOS (el portal del alumno es
  mobile-first, mismo motivo por el que `background-attachment: fixed` ya
  está descartado más arriba). `body`/`html` no crean stacking context propio
  hoy (sin `transform`/
  `filter`/`opacity`), así que `z-index: -1` en el pseudo-elemento queda
  detrás del contenido normal sin tocar nada más.

`DESIGN.md` § "The Atmospheric Canvas Rule" necesita una actualización breve
documentando que la atmósfera ahora tiene 3 variantes posibles (color/
imagen/doodle) en vez de una sola — tarea del `impeccable-documenter` al
cerrar la implementación, no de este spec.

### UI: `templates/tenants/gimnasio_form.html`

Nueva sección "Fondo", junto a "Identidad"/paleta/tipografía existentes:

- 3 radios (`fondo_tipo`): "Paisaje de color" / "Imagen propia" / "Doodle
  temático".
- Si `imagen`: el `<input type="file">` ya usa el mismo patrón que el campo
  `logo` (el form YA tiene `hx-boost="false"` por el upload de logo, no hace
  falta agregarlo). Nota de ayuda con los límites (5 MB, mínimo 1280×720).
- Si `doodle`: grilla de 4 miniaturas (una por `Doodle.choices`), radios
  ocultos detrás de cada miniatura clicable — mismo patrón visual que la
  selección de paisaje ya usa hoy.
- El preview en vivo "Así lo ve tu alumno" (JS vanilla ya existente en este
  template) se extiende para reflejar el modo elegido sin recargar,
  reusando la misma lógica que ya swappea colores/tipografía al cambiar de
  paisaje.

### Assets: doodles curados

4 SVG propios (`static/img/doodles/mancuernas.svg`, `sogas.svg`,
`discos.svg`, `kettlebell.svg`), monocromos, pensados para tileado sin
costuras visibles a ~180px. Tarea de diseño separada (agente
`impeccable-asset-producer`), a ejecutar una vez aprobado este spec —
no bloquea el resto de la implementación (Django/modelo/vista pueden
avanzar con un placeholder y sumar los SVG finales al final).

### Testing

- `tenants/tests.py`: validador de `fondo_imagen` (tamaño, resolución,
  formato) — casos límite igual que `PaisajeMatchingTests`, con imágenes de
  prueba armadas en memoria (`io.BytesIO` + `PIL.Image`), sin depender de
  archivos reales en el repo.
- `GimnasioUpdateViewTests`: guardar cada uno de los 3 `fondo_tipo` persiste
  correctamente; subir una imagen que excede el límite no guarda y muestra
  el error del form.
- Smoke test de render: por cada `fondo_tipo`, `base.html` (vía cualquier
  vista autenticada, p. ej. `home`) contiene el fragmento CSS esperado
  (`assertContains` sobre `mask-image`/`url(.../fondos/`/ausencia de ambos
  en modo `color`).
- Aislamiento de tenant: no aplica nuevo — `fondo_*` vive en `Gimnasio`
  mismo, ya cubierto por `TenantIsolationTests` existente.

## Fuera de alcance

- Recolorear el doodle con algo distinto a `--color-secundario` (p. ej. un
  color propio por gimnasio) — el catálogo de paletas ya resuelve eso.
- Subida de doodles propios por el dueño — catálogo cerrado a propósito
  (`ver "Por qué no hay permisos granulares"` en el spec de sub-cuentas
  como precedente del mismo criterio: no construir lo que nadie pidió
  todavía).
- Recorte/edición de la imagen subida (crop, brillo, etc.) — el dueño sube
  una imagen ya lista; si el encuadre no convence, sube otra.
