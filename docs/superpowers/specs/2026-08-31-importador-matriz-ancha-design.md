# Importador de planes: leer las planillas reales de los entrenadores

## Context

El primer cliente pago ("Vida Plena") intentó importar el plan de su alumna Eve
Colazo y el importador leyó **0 ejercicios** en los dos archivos que probó
(`capturas/AGOSTO 26 EVE COLAZO.xlsx` y `capturas/Plan de entrenamiento Eve
Colazo.xlsx`; capturas del error en la misma carpeta).

El parser de hoy (`importaciones/parsing.py`) asume un único layout: **tabla
larga** — fila 1 = encabezados, una fila por cada combinación semana/día/ejercicio.
La planilla real es otra cosa, una **matriz ancha**:

- Encabezado en **dos filas**: fila de grupos con celdas combinadas (`EJERCICIOS`,
  `Videos`, `SEMANA 1`…`SEMANA 4`) y fila de subcampos (`Series`, `Reps`,
  `Carga`, `RPE`, repetidos dentro de cada bloque de semana).
- El **día** vive en una celda combinada vertical de la columna izquierda, con su
  descripción adentro: `DÍA 2\n• TREN SUPERIOR\n• CORE`.
- Dos columnas de ejercicio: el **código de bloque** (`A1.`, `B2.` — superseries
  reales: A1+A2+A3 se ejecutan juntos) y el **nombre**.
- En el archivo original el encabezado **no está en la fila 1**: arranca en las
  filas 12-13, con logo, objetivo, fechas y "Cumplim: 25%" arriba. Y el workbook
  trae **7 hojas**, 6 auxiliares (`AUX` 3206 filas, `Movilidad Articular` 1020,
  `Avatar`, `Logros`, `Carga de Datos`, `Plantilla - aux`).

De ahí salen exactamente los dos errores reportados: en el archivo reducido
`' SEMANA 1'` se detecta por prefijo como columna "semana" y después no encuentra
`series` (está en la fila 2) → *"No se pudo importar: falta la columna 'series'"*;
en el original no encuentra ni `ejercicio`.

El archivo dice "Powered by **Simplify Trainers**": no es un formato casero, es
una plantilla comercial, así que va a repetirse. Pero — decisión explícita del
dueño del producto — **no hay que optimizar para este formato**: muchos
entrenadores no saben Excel y van a traer planillas sin formato de tabla. El
objetivo es un parser que se adapte, no uno que reconozca una marca.

**Decisiones tomadas (2026-08-31):**

1. **Auto-detección de layout + diccionario de sinónimos ampliado** (ES/EN, jerga
   de entrenamiento). Sin pantalla de mapeo manual de columnas.
2. **El RPE del archivo se descarta.** En la app el RPE lo carga el alumno sobre
   SU rutina asignada como feedback de esa semana; el del Excel es de otra persona
   y de un ciclo cerrado — meterlo en una plantilla reutilizable la contamina.
3. **El bloque y el nombre del día son campos propios del modelo**, no texto
   embutido en `notas`: superseries agrupadas y días titulados en el portal y en
   el PDF.
4. **Se elige la hoja antes del preview.**

Pedido menor del mismo cliente, sin relación con el parser: la columna "Estado"
del listado de Ejercicios dice "Activo" en las 748 filas y no aporta nada.

### Lo que ya se validó antes de escribir este plan

Prototipé la detección y la extracción contra los dos archivos reales: los dos
producen **172 items (43 ejercicios × 4 días × 4 semanas), 42 nombres distintos**,
y las 4 hojas auxiliares se descartan correctamente. Además, medido sobre esos
archivos:

- **Series y repeticiones son siempre enteros** (`4`, `20`) — no hay rangos tipo
  `"3-4"`. Tolerar rangos es **no-objetivo** de este trabajo; si aparece más
  adelante, va con un helper compartido por los dos layouts, nunca con criterios
  divergentes.
- **Los merges no son el costo dominante**: la hoja `AUX` (3206 × 51) expande solo
  **1037 celdas** combinadas. El costo real de una hoja auxiliar es leer sus
  ~163.000 valores de celda, así que la mitigación correcta es la **ventana de
  filas en la detección**, no acotar `_mapa_merges`.

**Convención del repo:** antes de tocar código, este plan se baja a
`docs/superpowers/specs/2026-08-31-importador-matriz-ancha-design.md` y
`docs/superpowers/plans/2026-08-31-importador-matriz-ancha-plan.md` y se commitea,
igual que las 9 features anteriores de este tamaño.

---

## Fase 1 — Que el archivo del cliente entre (desplegable sola)

### 1.1 · El orden de detección es lo más importante del diseño

**Se prueba matriz ancha PRIMERO, siempre.** No es una preferencia: si se prueba
el layout largo primero, la planilla real del cliente **matchea y produce basura
en silencio**, que es peor que los 0 items de hoy. La fila de grupos contiene
`EJERCICIOS` (alias de `ejercicio`) y la de subcampos contiene `Series`/`Reps`/
`Carga` — un buscador de encabezado multi-fila encontraría los tres campos
requeridos en la fila 13 y armaría items leyendo `series` de la columna de la
SEMANA 1 y el ejercicio de la columna del grupo. Filas plausibles, datos mal
alineados, nadie se entera.

Al revés no puede pasar: la detección ancha exige **≥2 celdas** que matcheen
`^(semana|sem|week|wk|microciclo|micro)\s*\d+$`, y `Semana` a secas — el
encabezado del layout largo — no matchea porque el regex exige el dígito. Una hoja
larga da 0 matches y cae limpio al fallback.

`detectar_matriz_ancha` corta en el primer paso que falla:

1. Fila con ≥2 `SEMANA n` dentro de la ventana de escaneo → si no, `None`.
2. ≥2 subcampos conocidos en esa fila o la siguiente → si no, `None`.
3. Cortar bloques y mapear subcampos; si un bloque queda sin `series` **y** sin
   `repeticiones`, `None`.
4. Perfilar las columnas de la izquierda por contenido; sin columna de nombre,
   `None`.
5. **Guarda anti-falso-positivo:** exigir ≥3 filas de datos con nombre no vacío.
   Sin esto, una hoja auxiliar con una tabla resumen "SEMANA 1 / SEMANA 2"
   (progreso, asistencia) pasa los cuatro pasos y genera items fantasma.

Y si el layout ancho se detecta pero no salen ejercicios, la hoja se excluye **con
motivo explícito** ("detecté una matriz por semanas pero no encontré ejercicios"),
nunca vacía y muda — es el constraint no negociable que ya fijó el review original
del importador.

### 1.2 · Lectura de la matriz ancha — `importaciones/parsing/ancha.py`

- **Bloques de semana**: de la columna del label hasta la anterior al siguiente.
- **Columnas de la izquierda, perfiladas por CONTENIDO** (no por encabezado, que
  acá está combinado o vacío): la de más textos largos es el **nombre**; la que
  más matchea `^[a-z]\s*\d{0,2}\.?$` es el **bloque**; las que matchean
  `^(dia|day|sesion|session|jornada)\s*\d+` son candidatas a **día**. Se perfila
  sobre una muestra de ~40 filas, no sobre la hoja entera.
- **Día: forward-fill** del último marcador visto bajando por las filas. Entre
  varias columnas gana la celda con **más texto**, así se captura
  `DÍA 2\n• TREN SUPERIOR\n• CORE` y no el `DÍA 2` pelado de al lado. Robusto a
  merges, a la ausencia de merge y a etiquetas partidas — los dos archivos reales
  difieren justo en eso.
- Un `ItemParseado` por (fila de ejercicio × semana con datos). `orden` sigue el
  contador por `(semana, dia)` que ya existe (`parsing.py:269`).
- La columna RPE se detecta **para saltearla**, no para guardarla.

**`FilaInvalida` cambia de cardinalidad y eso se filtra a la UI.** En el layout
largo una fila de Excel produce un item; en el ancho produce hasta 4. Un `series`
no numérico en la semana 2 **no puede** invalidar la fila entera. El mismo
`fila_excel` puede aparecer con varios motivos ("Semana 2: series no es un
número"). `templates/importaciones/plantillas_preview.html:47-56` asume hoy
fila↔motivo 1:1 — hay que agrupar por fila y concatenar motivos. Es la fuga más
probable de este refactor hacia la vista.

**Diccionario de sinónimos** (`ALIAS_PLANTILLA`, `parsing.py:17`): sumar
`microciclo`/`micro`/`wk` a semana; `sesion`/`session`/`jornada` a día;
`movement`/`nombre` a ejercicio; `set` a series; `repetitions` a repeticiones;
`kgs`/`load`/`weight` a kilos; `recuperacion` a descanso; `notes`/`obs` a notas.
Alias nuevos: `bloque` (`bloque`/`block`/`cod`) y `rpe` (`rpe`/`rir`), este último
solo para ignorarlo.

**Guardas de largo que faltan** (lección de `ISSUES.md [2026-08-27]`: SQLite no
valida largos, Postgres sí, y `bulk_create` no corre validadores):

- `repeticiones` (max 20), `kilos` (30), `descanso` (30): fila descartada en el
  **preview** con motivo y número de fila, leyendo los límites de
  `RutinaPlantillaItem._meta` — reusando el patrón de
  `services.py::_motivo_si_no_entra` (`services.py:249`), que hoy solo cubre
  biblioteca.
- `semana` > `SEMANAS_POR_CICLO` (4): hoy `bulk_create` la insertaría igual,
  saltándose el `MaxValueValidator`. Se descarta con aviso.

### 1.3 · Búsqueda de la fila de encabezado (sirve a los tres lectores)

Hoy `leer_hoja_plantilla` (`parsing.py:214`) y `leer_hoja_biblioteca`
(`parsing.py:301`) hacen `next(ws.iter_rows(min_row=1, max_row=1))`: rígido en la
fila 1. Pasa a buscarse en las primeras **~15 filas** la primera donde se detecten
todos los campos requeridos (`ejercicio`+`series`+`repeticiones` para plantillas,
`nombre` para biblioteca), devolviendo también el índice de esa fila.

**Esto cambia a propósito el comportamiento de dos clases de test**, y es una
mejora, no una regresión: hoy un archivo con un título arriba de la tabla es un
error explicado; a partir de ahora **se importa bien**.

- `BibliotecaSinColumnaNombreTests` (`tests.py:1798`) y
  `PreviewBibliotecaSinColumnaNombreTests` (`tests.py:1849`) se **reescriben, no
  se borran**: el caso "título arriba" se muda a una clase nueva que afirma que el
  ejercicio **se importa** (ojo: `fila_excel=3`, no 2), y las originales sobreviven
  con un fixture donde ninguna de las primeras 15 filas tiene columna de nombre.
  Sigue fijándose la propiedad que importa — *la app te dice qué leyó* — y solo
  cambia qué fila se ecoa.
- El copy de `services.py:294-302` pasa de *"En la primera fila leí…"* a *"En la
  fila N leí… / Miré las primeras 15 filas y no encontré ninguna con los títulos
  de las columnas"*, y suma el link al ejemplo descargable de §1.5.

Riesgo a vigilar: que una fila de DATOS se confunda con encabezado. El test
`test_columna_ejercicio_ausente_devuelve_hoja_sin_items` ya es el guardarraíl
(`["Dia","Series","Repeticiones"]` + datos `[1, 4, "8-12"]`); hay que confirmar que
sigue pasando por el motivo correcto.

### 1.4 · Elegir la hoja antes del preview

Nueva pantalla `plantillas/<pk>/hojas/` → `SeleccionHojasView`
(`importaciones/views.py`, entre `plantillas_subir` y `plantillas_preview` en
`urls.py:17-18`). Lista una fila por hoja con lo detectado (layout, nº de
ejercicios, días, semanas) y un checkbox **pre-tildado solo en las que parsearon
items**. La elección se guarda en `resultado["hojas_elegidas"]` (lista de nombres)
— **sin campo de modelo nuevo, sin estado nuevo, sin reabrir el archivo**,
respetando la invariante documentada en `importaciones/models.py:1-9` ("el archivo
nunca se vuelve a abrir después del preview").

**El pareo hoja↔decisión deja de ser posicional.** Hoy `decisiones["hojas"]` es
una lista alineada por índice, consumida con `zip()` y validada por longitud en
cuatro lugares que hay que mover juntos: `views.py:144`, `views.py:169-172`,
`services.py:140-146` y `services.py:200`. Pasa a parearse por `nombre_hoja` — el
campo hidden que `HojaMetadataForm` (`forms.py:37`) ya declara y **nadie lee**.
Sin esto, filtrar hojas rompe el alineamiento en silencio.

`leer_hoja_biblioteca` sigue usando la primera hoja (`parsing.py:349`); no se le
agrega selección, así que `ParsearArchivoBibliotecaTests.test_usa_la_primera_hoja`
queda intacto.

Se actualiza el copy de `plantillas_subir.html:8-12`, que hoy promete "cada hoja
del archivo se convierte en una plantilla".

### 1.5 · El preview tiene que mostrar lo que entendió, y un ejemplo descargable

`plantillas_preview.html:30-45` muestra Día/Ejercicio/Series/Repeticiones. Con 172
items y 4 semanas eso no alcanza para verificar nada. Agregar **Semana**,
**Bloque** y **Kilos**, y encabezar cada hoja con un resumen (*"4 días · 4 semanas
· 43 ejercicios · leído como matriz ancha, encabezado en la fila 12"*). Es la única
defensa del entrenador contra una lectura mal alineada.

Y como nada en el proyecto dice hoy qué columnas acepta el importador (los alias
viven solo en el código), vista nueva **`plantillas/ejemplo.xlsx`** que genera el
archivo al vuelo con openpyxl — **no un binario versionado**, que se desincroniza
del parser. Link desde `plantillas_subir.html` y desde el mensaje de error de
columna faltante, con `hx-boost="false"` (descarga: el gotcha recurrente del
proyecto). Para el entrenador que "no sabe nada de Excel", un ejemplo para llenar
vale más que cualquier mensaje de error.

### 1.6 · Estructura del módulo

**Restricción que fija todo:** `importaciones.parsing` lo importan seis módulos,
**incluidas dos migraciones históricas** —
`rutinas/migrations/0006_backfill_grupo_muscular_snapshot.py:24` y
`ejercicios/migrations/0003_backfill_categorias.py:30`. `from importaciones.parsing
import normalizar_texto` tiene que seguir funcionando con esa ruta exacta. Eso
descarta renombrar y obliga a que la fachada re-exporte.

Paquete **`importaciones/parsing/`**, cuatro archivos:

| Módulo | Contenido |
|---|---|
| `__init__.py` (~45 l.) | Fachada: re-exports de toda la API pública actual + `parsear_archivo_*` + `leer_hoja_plantilla`, que es el **dispatcher de 4 líneas**. Cero cambios de import fuera de la app. |
| `comun.py` (~240 l.) | `normalizar_texto`, `ALIAS_*`, `detectar_columnas`, las dataclasses, los helpers de merges/celdas, `buscar_fila_encabezado`, `ColumnaRequeridaFaltante`. |
| `tabular.py` (~190 l.) | `leer_hoja_larga` + `leer_hoja_biblioteca`. Van juntos porque son **el mismo patrón**: un encabezado, un registro por fila. |
| `ancha.py` (~300 l.) | Todo lo de la matriz ancha: regex, corte de bloques, perfilado por contenido, forward-fill, emisión. |

Se parte porque el archivo se iría a ~700 líneas haciendo tres cosas que nadie lee
juntas, y porque hoy ya mezcla "cómo se normaliza texto" (que consumen otras apps
y dos migraciones) con "cómo se lee una celda de openpyxl" (que no le importa a
nadie más). No es indirección gratis: `__init__.py` no tiene lógica salvo el
dispatcher, y a cambio el diff fuera de `importaciones/` es cero.

**Mecánica: `git mv importaciones/parsing.py importaciones/parsing/comun.py`** y
recortar desde ahí. `parsing.py` está lleno de comentarios que citan incidentes
concretos (la importación de 748 ejercicios, el 502 de gunicorn, el link de 306
caracteres); perder el blame ahí borra la mitad del valor del archivo.

**El lector largo se mueve, no se reescribe** — así las clases que lo cubren
(`LeerHojaPlantillaTests`, `DetectarColumnasTests`,
`DeteccionTolerantePorContenidoTests`, `ParsearArchivoPlantillasTests`) siguen
pasando **sin editarlas**, que es la única prueba real de que no hubo regresión.

**`ItemParseado` gana `bloque` y `dia_nombre` al FINAL, con default `""`.** No es
cosmético: `LeerHojaPlantillaTests.test_lee_filas_validas` compara contra un
`ItemParseado(...)` construido con kwargs, y como el lector largo también deja
`""`, la igualdad del frozen dataclass sigue dando `True` y ese test no se toca.

**No pasar a `load_workbook(read_only=True)`.** Tentador para el costo, pero en
ese modo `ws.merged_cells` no es confiable y no hay acceso aleatorio por
`ws.cell(row, col)` — que es exactamente lo que hace todo el lector ancho. Dejarlo
escrito como comentario para que nadie lo "optimice" después.

**Costo:** la detección nunca mira `ws.max_row` — ventana fija de ~40 filas ×
`min(ws.max_column, 80)`. La hoja `AUX` de 3206 filas se descarta habiendo leído
~40. (Medido: los merges no son el problema, ver Context.)

**Sin CSS nuevo:** el badge de bloque reusa `.badge` (`styles/input.css:275`), así
que no hace falta correr `npm run build:css`.

### 1.7 · Columna "Estado" (independiente de todo lo demás)

Mismo criterio ya aplicado y documentado en `ISSUES.md [2026-08-27]` para el
preview de biblioteca: *"una columna de valor constante no informa nada"*, y lo
único que aportaba (la excepción) pasa a badge.

- `templates/ejercicios/ejercicio_list.html:48` — se va la columna `Estado`; badge
  "Inactivo" al lado del nombre solo cuando `not e.activo`. `colspan` 5 → 4.
- `templates/ejercicios/categoria_list.html:22` — igual.
- En la misma fila de encabezados, `ejercicio_list.html:47` dice todavía "Grupo
  muscular". Es el **único** lugar del proyecto que quedó con esa etiqueta (grep
  sobre `templates/`) y contradice la regla explícita de `CLAUDE.md`: la etiqueta
  visible en toda la UI es "Categoría" — MOVILIDAD o MUSCLE UP no son grupos
  musculares.

Aclaración para el cliente: `activo` **sí** es editable hoy
(`ejercicios/forms.py:38` y `:89`, renderizado por `form.as_p`). Lo que sobra es la
columna, no el control.

---

## Fase 2 — `bloque` y `dia_nombre` a través de todo el stack

`bloque = CharField(max_length=10, blank=True)` y
`dia_nombre = CharField(max_length=80, blank=True)` en `RutinaPlantillaItem`
**y** su espejo `RutinaAsignadaItem` (`rutinas/models.py:103` y `:252`). Migración
`rutinas/0008`, sin backfill. **El parser trunca a esos largos leyéndolos de
`_meta`**, no copiándolos: `dia_nombre` sale de una celda combinada con viñetas, y
una celda mal pegada de 300 caracteres es exactamente el `DataError` que SQLite no
te avisa en local.

**`dia_nombre` va denormalizado por item, no como modelo `RutinaPlantillaDia`.**
Es el mismo patrón que `categoria_snapshot` (`models.py:273`), que ya guarda un
texto repetido por item y se resuelve con la regla "semana más baja" en
`agrupacion.py:83-91`. Un modelo `Dia` obligaría a una migración de datos, una FK
en `crear_desde_plantilla`, y a cambiar la forma de `dias_disponibles`
(`tenants/views.py:106`) y del agrupado del PDF (`pdf.py:140`), todo para una
etiqueta cosmética. Costo aceptado: en el alta manual el texto se retipea por
ejercicio.

**`Meta.ordering` NO cambia.** Sigue `["semana","dia","orden"]`: el importador ya
asigna `orden` en el orden del archivo, así que A1,A2,A3,B1… salen agrupados
solos, y meter `bloque` en el ordering mandaría los items manuales (bloque vacío)
al principio. `bloque` es display-only.

Los cuatro caminos de escritura, que se tocan juntos:

| Lugar | Cambio |
|---|---|
| `rutinas/models.py:212-236` `crear_desde_plantilla` | sumar ambos campos al `bulk_create` |
| `rutinas/models.py:83-99` `duplicar()` | ídem |
| `importaciones/services.py:227-240` | ídem, leyendo con **`item.get("bloque","")`** — una `Importacion` EN_REVISION creada antes del deploy no tiene esas claves en su JSON. **Sin migración del JSON**: `resultado` es un blob de preview con vida útil acotada (ya existe `descartar_importaciones_viejas`); el `.get()` con default *es* la migración. |
| `rutinas/forms.py:30-43` `RutinaPlantillaItemForm` | sumar los dos campos |

Lectura y presentación:

- `rutinas/agrupacion.py:86-107` — devolver `"bloque"` y `"dia_nombre"` desde
  `item_semana_mas_baja`, misma regla que `categoria_display`. El `sort` sigue por
  `orden` (L109).
- `templates/rutinas/mi_dia_detalle.html:8` — `Día N — Tren superior · Core`;
  badge de bloque en la celda del ejercicio (L69-72, junto a `categoria_display`).
- `rutinas/pdf.py:73-82` `_fila_ejercicio` — bloque como prefijo del nombre
  (`"A1 · Press banca"`), sin agregar columna: `_COLUMNAS` (L20) ya son 7 en A4
  vertical. `pdf.py:144-146` — nombre del día en el título de cada día. **Regla de
  sincronía de `CLAUDE.md`**: el desglose del PDF y el de la tabla del portal se
  mantienen alineados.
- `templates/rutinas/plantilla_detail.html` (colspan 10→11) y
  `asignada_detail.html` (11→12).
- `templates/tenants/home.html:412-417` + `tenants/views.py:106-110` —
  `dias_disponibles` pasa de lista de enteros a lista de `{numero, nombre}` (el
  `{% url %}` sigue necesitando el entero).
- `rutinas/admin.py:44` y `:51` — `list_display`, o el campo queda invisible en el
  admin (precedente documentado en
  `docs/superpowers/specs/2026-07-27-progresion-semanal-rutinas-design.md`).

---

## Verificación

**Correr `python manage.py test -v 2`.** La suite venía en 834 verdes al último
commit; confirmarlo **antes** de empezar, no darlo por hecho.

Tests nuevos:

- `BuscarFilaEncabezadoTests` — encabezado en fila 1 / en fila 12 / inexistente /
  una fila de datos que no se confunde con encabezado.
- `DeteccionLayoutTests` — larga→larga, ancha→ancha, auxiliar→ninguna, y el caso
  clave: **una hoja ancha nunca cae al parser largo** (§1.1).
- `LeerHojaAnchaTests` — bloques y subcampos, forward-fill del día, gana la celda
  con más texto, semana sin datos no emite item, RPE descartado, guarda
  anti-falso-positivo, hoja ancha sin ejercicios devuelve `motivo_exclusion`.
- `FilaInvalidaPorSemanaTests` — una fila con `series` malo en una sola semana
  emite las otras 3 e informa el motivo de esa; el preview agrupa por fila.
- Largos: `repeticiones`/`kilos` que no entran se descartan en el preview con
  motivo (no en el `INSERT`); `semana=5` no se inserta.
- Escala: el costo en queries del confirm no crece con la cantidad de filas —
  extender `ImportacionBibliotecaEscalaTests` (`tests.py:2353`) a plantillas. 172
  items sobre Render free con 30 s de gunicorn es el mismo riesgo que causó el 502
  de agosto.
- `CostoDeteccionTests` — envolver `ws` en un stub que cuente accesos y afirmar que
  la detección no supera la ventana de filas. (Un test de wall-clock sería flaky.)
- Selección de hojas: pareo por nombre y no posicional; hoja no elegida no crea
  plantilla; POST con un nombre de hoja inexistente falla.
- `ConfirmarImportacionSinCamposNuevosTests` — `Importacion` armada a mano con un
  `resultado` **sin** las claves nuevas: confirma y crea los items con `bloque=""`.
  Es el único test que cubre el escenario de deploy real.
- Fase 2: `bloque`/`dia_nombre` sobreviven a `crear_desde_plantilla` y a
  `duplicar()` (patrón de `CrearDesdePlantillaTests:268` y
  `DuplicarPlantillaTests:806`); `agrupacion` los resuelve por semana más baja.
- `ejercicio_list`/`categoria_list` no muestran columna Estado y sí el badge
  Inactivo.

Tests que **se reescriben** (§1.3, cambio de comportamiento deliberado):
`BibliotecaSinColumnaNombreTests` y `PreviewBibliotecaSinColumnaNombreTests`. El
porqué va en el docstring de la clase nueva — si alguien los "arregla" para que
vuelvan a pasar en vez de reescribirlos, se pierde la mejora sin que nadie lo note.

**Verificación manual, obligatoria** (`feedback_dev_workflow`: el code-review no
reemplaza el QA end-to-end): `runserver` y subir **los dos archivos reales de
`capturas/`** de punta a punta — subir → elegir hoja → preview → confirmar → abrir
la plantilla → asignarla a un alumno → ver el día en el portal → bajar el PDF.
Esperado: 43 ejercicios, 4 días, 4 semanas, días titulados, bloques visibles.

**Antes de commitear:** `git status` para confirmar que no quedó ningún archivo
NUEVO untracked (la migración, los submódulos de `parsing/`, los templates nuevos).
Ya rompió producción dos veces sin ningún síntoma en tests ni en logs.

Al cerrar: entrada en `ISSUES.md` y actualizar `CLAUDE.md`, que en la sección
"Importador de Excel (Proyecto 2)" describe `parsing.py` como archivo único y en la
de rutinas todavía dice `grupo_muscular_snapshot`, campo renombrado a
`categoria_snapshot` en `rutinas/0007`.
