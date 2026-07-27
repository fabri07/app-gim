# Progresión semanal en rutinas (semana 1-4)

## Contexto

Es el **Proyecto 1** de un trabajo en dos partes: incorporar una forma de
ingestar planes de entrenamiento que hoy los profesores llevan en Excel/Google
Sheets (evaluado copiando/reduciendo el sistema de ingesta de Véktor,
descartado por sobre-acoplado al dominio financiero — ver discusión previa).
Antes de diseñar ese importador (Proyecto 2, spec posterior) hace falta que el
modelo de datos soporte lo que esas planillas describen: **plantillas
"modelo" (por género) con una progresión de 4 semanas**, donde cada semana
puede tener ejercicios distintos.

Hoy `rutinas/models.py` no tiene noción de semana: `RutinaPlantillaItem.dia`
es "día N de la rutina" (1..`dias_por_semana`), un ciclo plano que se repite
igual indefinidamente. `RutinaPlantilla.dias_por_semana` ya resuelve la
variabilidad de días por alumno (una plantilla distinta por cantidad de
días — "Full body 2 días", "Full body 3 días", etc.), eso **no cambia**.

**Fuera de alcance:** el importador de Excel/Sheet en sí (Proyecto 2). Este
spec es exclusivamente el cambio de modelo + UI de staff/portal para que una
plantilla y una rutina asignada puedan tener contenido distinto por semana.

## Decisiones de producto

1. El ciclo es **siempre 4 semanas**, fijo en todo el sistema (constante de
   código, no un campo configurable por plantilla).
2. Entre semana 1 y semana 4 **los ejercicios pueden ser distintos** (no es
   solo una progresión de carga/intensidad sobre los mismos ejercicios) — cada
   item de una plantilla/asignación queda asociado a una semana concreta.
3. La cantidad de días por semana sigue resolviéndose como hoy: **plantillas
   separadas** por cantidad de días, no selección de subconjunto de días al
   asignar. Sin cambios en `RutinaPlantilla.dias_por_semana`.
4. La "semana actual" de un alumno se calcula **automáticamente por fecha**
   (`fecha_inicio` de su `RutinaAsignada` + hoy), nunca la toca el staff a
   mano.
5. Al llegar a semana 4 **no hay loop automático**: el ciclo se sostiene en
   semana 4 indefinidamente hasta que el staff cierra la asignación
   (`fecha_fin`) y crea una nueva — mismo mecanismo manual que ya existe hoy
   para `RutinaAsignada`, sin lógica nueva de cron/scheduling.

## Diseño

### 1. Modelo — `rutinas/models.py`

Constante de módulo:

```python
SEMANAS_POR_CICLO = 4
```

`RutinaPlantillaItem` y `RutinaAsignadaItem` ganan:

```python
semana = models.PositiveSmallIntegerField(
    default=1,
    validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
    help_text="Semana del ciclo (1 a 4).",
)
```

- `Meta.ordering` de ambos modelos pasa de `["dia", "orden"]` a
  `["semana", "dia", "orden"]`.
- `default=1`: los items ya existentes quedan en semana 1 sin backfill manual
  — plantillas viejas siguen funcionando igual, solo "viven" enteras en
  semana 1 hasta que el staff cargue contenido para 2-4.

`RutinaAsignada` gana una property (no un campo — se recalcula sola, nunca se
desincroniza):

```python
@property
def semana_actual(self) -> int:
    dias_transcurridos = (timezone.localdate() - self.fecha_inicio).days
    if dias_transcurridos < 0:
        return 1
    return min(SEMANAS_POR_CICLO, (dias_transcurridos // 7) + 1)
```

Semana 1 = días 0-6 desde `fecha_inicio`, semana 2 = días 7-13, etc.; clamp en
4 una vez alcanzada. `fecha_inicio` en el futuro (asignación cargada por
adelantado) → semana 1, no negativo.

### 2. Copiado — `duplicar()` y `crear_desde_plantilla()`

`RutinaPlantilla.duplicar()` (`rutinas/models.py:59-94`) y
`RutinaAsignada.crear_desde_plantilla()` (`rutinas/models.py:168-212`) agregan
`semana=item.semana` a los `bulk_create(...)` existentes — una línea cada uno,
mismo patrón que ya usan para `dia`/`orden`/etc.

### 3. Migración

Una migración estándar (`rutinas/migrations/00XX_item_semana.py`) agregando
la columna con `default=1` en ambos modelos Item. No requiere backfill de
datos ni migración de código aparte.

### 4. Forms — `rutinas/forms.py`

`RutinaPlantillaItemForm.Meta.fields` (línea 33-41) agrega `"semana"` junto a
`"dia"`. `RutinaPlantillaForm` y `AsignarRutinaForm` no cambian.

### 5. Admin — `rutinas/admin.py`

`RutinaPlantillaItemAdmin.list_display` (línea 44) y
`RutinaAsignadaItemAdmin.list_display` (línea 51) agregan `"semana"` — si no,
el campo queda invisible en el listado del admin (aparece en el form del
inline por default, pero no en la tabla).

### 6. Templates de staff

- `templates/rutinas/plantilla_detail.html`: columna **Semana** nueva antes
  de "Día" en la tabla de ejercicios (sigue siendo una sola tabla plana,
  ordenada `semana, dia, orden` — sin pestañas ni agrupación visual nueva).
- `templates/rutinas/asignada_detail.html`: misma columna **Semana**, más una
  línea nueva en la tarjeta de datos: `Semana actual: {{ asignada.semana_actual }} de 4`.

### 7. Portal del alumno — `tenants/views.py` + `templates/tenants/home.html`

`HomeView._portal_alumno` (`tenants/views.py:90-124`) agrega, junto a
`rutina_actual`, el subconjunto de items de la semana que corresponde hoy —
calculado en la vista, no en el template:

```python
rutina_actual = alumno.rutinas_asignadas.filter(activa=True).first()
...
"rutina_actual": rutina_actual,
"items_semana_actual": (
    rutina_actual.items.filter(semana=rutina_actual.semana_actual)
    if rutina_actual else []
),
```

`templates/tenants/home.html` cambia el loop de `rutina_actual.items.all` a
`items_semana_actual`, y agrega un subtítulo `Semana {{ rutina_actual.semana_actual }} de 4`
sobre la tabla — el alumno ve solo lo que le toca hoy, no las 4 semanas
completas (mobile-first, "entiende su rutina sin explicación adicional",
CLAUDE.md Fase 3).

## Tests

- **Modelo**: `semana` fuera de 1-4 rechazado por los validators; default=1 al
  crear sin especificar; `duplicar()` y `crear_desde_plantilla()` copian
  `semana` correctamente; el ordering de una consulta real refleja
  `semana, dia, orden`.
- **`RutinaAsignada.semana_actual`**: `fecha_inicio` = hoy → 1; hace 6 días →
  1; hace 7 días → 2; hace 30+ días → clamp en 4; `fecha_inicio` en el futuro
  → 1 (no negativo/error).
- **Form**: `RutinaPlantillaItemForm` rechaza `semana=0` y `semana=5`.
- **Vistas de staff**: crear/editar un item con `semana` se refleja bien
  ordenado en `plantilla_detail`; `asignada_detail` muestra el
  `semana_actual` correcto para una asignación con `fecha_inicio` conocida.
- **Portal del alumno**: con una `RutinaAsignada` que tiene items en varias
  semanas, `items_semana_actual` devuelve solo los de la semana que
  corresponde según `fecha_inicio`; sin rutina activa sigue devolviendo
  estado vacío (regresión).
- No se agregan tests de aislamiento de tenant nuevos: `semana` es un entero
  simple sin FK, no abre ningún vector de fuga entre gimnasios distinto al
  que ya cubre `TenantIsolationTests`.
