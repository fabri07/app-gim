# Plan de implementación — Importador de matriz ancha

Spec: `docs/superpowers/specs/2026-08-31-importador-matriz-ancha-design.md`.
Rama: `importador-matriz-ancha`.

**Línea de base al empezar (2026-08-31):** 834 tests, **832 verdes y 2 rojos
preexistentes** en `notificaciones.tests.EnviarRecordatoriosCommandTests`
(`test_correrlo_dos_veces_el_mismo_dia_no_duplica` y
`test_alumno_sin_perfil_no_queda_bloqueado_para_siempre`). No son de este trabajo:
el fixture hace `dia_vencimiento_pago = min(hoy.day + 1, 28)`, así que del 29 al 31
de cada mes el pago cae fuera de la ventana de [0,3] días y no se envía nada. Es un
test con fecha frágil, no un bug de producción. **Queda fuera de esta rama.**

---

## Fase 1 — Que el archivo del cliente entre

### Tarea 1 · Columna "Estado" (independiente, no toca el parser)
`templates/ejercicios/ejercicio_list.html`, `templates/ejercicios/categoria_list.html`.
Sacar la columna de valor constante, badge "Inactivo"/"Inactiva" junto al nombre
solo cuando corresponde, bajar `colspan`, y corregir el `<th>` "Grupo muscular" →
"Categoría" (único remanente del proyecto). Tests de vista que afirmen que la
columna no está y que el badge sí aparece para un ejercicio inactivo.

### Tarea 2 · Partir `parsing.py` en paquete, sin cambiar comportamiento
`git mv importaciones/parsing.py importaciones/parsing/comun.py` y recortar a
`comun.py` / `tabular.py` / `ancha.py` (vacío por ahora) / `__init__.py` (fachada).
**Criterio de aceptación: `importaciones/tests.py` no se edita y sigue dando lo
mismo.** Verificar además que las dos migraciones históricas que importan
`normalizar_texto` siguen resolviendo (`python manage.py migrate --plan`).

### Tarea 3 · `buscar_fila_encabezado` (TDD)
`comun.py`. Encabezado en fila 1 / en fila 12 / inexistente / fila de datos que no
se confunde. Enganchar los dos lectores tabulares. Reescribir
`BibliotecaSinColumnaNombreTests` y `PreviewBibliotecaSinColumnaNombreTests` con el
porqué en el docstring. Actualizar el copy del error en `services.py`.

### Tarea 4 · `detectar_matriz_ancha` (TDD)
`ancha.py`. Los 5 pasos del spec §1.1, en orden, cortando en el primero que falla.
Test clave: **una hoja ancha nunca cae al parser largo**. Test de costo con un `ws`
instrumentado. El dispatcher en `__init__.py` recién se conecta acá.

### Tarea 5 · `leer_hoja_ancha` (TDD)
`ancha.py`. Bloques, subcampos, forward-fill del día, celda con más texto, RPE
descartado, semana sin datos no emite. `ItemParseado` gana `bloque` y `dia_nombre`
al final con default `""`.

### Tarea 6 · Sinónimos y guardas
Ampliar `ALIAS_PLANTILLA` (ES/EN). Guardas de largo leyendo
`RutinaPlantillaItem._meta` (reusar el patrón de `_motivo_si_no_entra`) y descarte
de `semana > SEMANAS_POR_CICLO`. `FilaInvalida` por semana + agrupado por fila en
el template del preview.

### Tarea 7 · Elegir la hoja
`SeleccionHojasView` + url + template. `resultado["hojas_elegidas"]`. **Pasar el
pareo hoja↔decisión de posicional a por `nombre_hoja` en los cuatro lugares a la
vez** (`views.py:144`, `views.py:169-172`, `services.py:140-146`, `services.py:200`).

### Tarea 8 · Preview informativo + ejemplo descargable
Columnas Semana/Bloque/Kilos y resumen por hoja. Vista `plantillas/ejemplo.xlsx`
generada con openpyxl, con `hx-boost="false"`.

### Checkpoint Fase 1
Suite verde (salvo los 2 rojos preexistentes) + **subida manual de los dos `.xlsx`
reales de `capturas/`**. Fase 1 es desplegable sola.

---

## Fase 2 — `bloque` y `dia_nombre` en el modelo

### Tarea 9 · Campos + migración
`RutinaPlantillaItem` y `RutinaAsignadaItem`, `rutinas/0008`, sin backfill. El
parser trunca leyendo de `_meta`.

### Tarea 10 · Los cuatro caminos de escritura, juntos
`crear_desde_plantilla`, `duplicar()`, `importaciones/services.py` (con `.get()`),
`RutinaPlantillaItemForm`. Test `ConfirmarImportacionSinCamposNuevosTests` con un
`resultado` viejo sin las claves.

### Tarea 11 · Lectura y presentación
`agrupacion.py` (regla semana más baja), `mi_dia_detalle.html`, `pdf.py` (sincronía
con el portal), `plantilla_detail.html`/`asignada_detail.html` (colspan),
`home.html` + `tenants/views.py` (`dias_disponibles`), `rutinas/admin.py`.

---

## Cierre
Entrada en `ISSUES.md`. Actualizar `CLAUDE.md`: la sección del importador describe
`parsing.py` como archivo único, y la de rutinas todavía dice
`grupo_muscular_snapshot` (renombrado a `categoria_snapshot` en `rutinas/0007`).
`git status` antes de commitear: los submódulos de `parsing/`, la migración y los
templates nuevos son archivos NUEVOS y untracked rompen producción sin síntoma.
