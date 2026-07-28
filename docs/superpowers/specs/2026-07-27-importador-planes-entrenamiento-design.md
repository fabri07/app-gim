# Importador de planes de entrenamiento desde Excel (Proyecto 2)

## Contexto

app_gim hoy solo permite cargar rutinas ítem por ítem desde la UI
(`rutinas:item_crear`). La mayoría de los profesores ya tienen sus planes
armados en Excel/Google Sheets — replicarlos a mano en el sistema es
fricción pura y frena la adopción. Es el **Proyecto 2** de un trabajo en dos
partes: el **Proyecto 1** (progresión semanal, campo `semana` 1-4 en los
items de rutina — spec `2026-07-27-progresion-semanal-rutinas-design.md`)
ya está mergeado a `main` y es un prerequisito de este: sin él, una planilla
con contenido distinto por semana no tendría dónde aterrizar.

Se evaluó reusar el sistema de ingesta de otro proyecto propio (Véktor,
~16.300 líneas, FastAPI+SQLAlchemy, salud financiera de PyMEs) y se
descartó: está fuertemente acoplado al dominio financiero (mapeo de
columnas hardcodeado a "ventas/gastos/stock/clientes/proveedores",
reconciliación contable factura-remito). Se rescata solo la idea general
del pipeline (detectar → parsear → preview → confirmar), no el código —
la única pieza remotamente portable de Véktor (`file_parsing.py`) igual
tiene el vocabulario de campos canónicos hardcodeado al dominio financiero.

**Fuera de alcance:** integración OAuth en vivo con Google Sheets (el
profesor sigue exportando a `.xlsx` a mano) — queda como Proyecto 3 futuro.

## Decisiones de producto

1. **Entrada**: subida de archivo `.xlsx` exportado, vía formulario normal
   (`multipart/form-data`), no lectura en vivo de Google Sheets.
2. **Flujo en 2 pasos**: subir → preview (el staff revisa qué se va a
   crear) → confirmar. Nada se escribe en la base de datos de dominio antes
   de confirmar — es el primer flujo de este tipo en el proyecto (los dos
   precedentes de subida de archivo, comprobantes de pago y logo del
   gimnasio, son POST directo sin preview).
3. **La salida es SIEMPRE una `RutinaPlantilla` reutilizable**, nunca una
   `RutinaAsignada` directa a un alumno. Si la planilla de origen era la
   versión personalizada de un alumno puntual, el staff la asigna después
   con el flujo `rutinas:asignar` que ya existe. El import no necesita
   saber nada de alumnos.
4. **Dos flujos de import independientes**: (a) plantillas de rutina
   completas (que además auto-crea los `Ejercicio` que falten), y (b)
   biblioteca de ejercicios sola (planilla sin días/semanas/series, solo
   nombre + grupo muscular + video opcional).
5. **Matching de nombres de ejercicio tolerante** a mayúsculas, acentos y
   errores de tipeo, contra la biblioteca ya cargada del gimnasio.
6. **Encabezados de columna con detección flexible** (por alias
   normalizado, no una plantilla rígida de columnas en orden fijo) — cada
   profesor ya tiene su planilla armada a su manera.
7. **Multi-hoja**: cada hoja del `.xlsx` se procesa como una
   `RutinaPlantilla` separada, nombrada según el nombre de la hoja (ej. una
   hoja "Hombres" y otra "Mujeres" → dos plantillas).
8. **Filas inválidas se saltean**, no invalidan el archivo entero — se
   listan en el preview con el motivo para que el staff decida si corrige
   el Excel y vuelve a subir, o sigue igual.
9. **Sin columna de semana detectada** → todos los items de esa hoja van a
   semana 1 (el `default` ya existente del campo, del Proyecto 1).
10. **Metadata que la planilla no trae**: `objetivo`/`nivel` de
    `RutinaPlantilla` (no vienen del Excel) y `grupo_muscular` de cada
    `Ejercicio` nuevo (`Ejercicio.grupo_muscular` es un choice cerrado de 8
    valores sin "otro" — no hay forma de inferirlo con confianza) se
    completan en la pantalla de preview, antes de confirmar: `objetivo`/
    `nivel` una vez por hoja detectada, `grupo_muscular` una vez por
    ejercicio nuevo (no por fila).
11. **Matches ambiguos de ejercicio** (ej. "Sentadila" en la planilla vs.
    "Sentadilla" ya cargada): el preview viene **pre-marcado en "usar el
    existente sugerido"** — el staff tiene que elegir activamente "crear
    nuevo" si en realidad es otro ejercicio. Decisión explícita del
    usuario: prioriza menos clics en el caso común (typos) por sobre el
    riesgo de una fusión incorrecta, que queda a cargo del staff detectar
    en el preview.

## Diseño

### 1. Nueva app `importaciones`

No vive dentro de `rutinas` ni `ejercicios`: el import de plantillas toca
ambos dominios (crea `RutinaPlantilla`/`Item` Y `Ejercicio`), y su modelo
central es transitorio/staging — ciclo de vida distinto al catálogo
permanente del gimnasio (mismo espíritu que separar `RutinaPlantilla`
editable de `RutinaAsignada` snapshot). Precedente directo: `turnos` y
`calendario` son apps nuevas y chicas para una sola feature vertical, en
vez de vivir dentro de `alumnos`.

```python
# config/settings.py — INSTALLED_APPS, después de 'rutinas'
'rutinas',
'importaciones',   # depende de ejercicios + rutinas + tenants
'pagos',
```

### 2. Modelo de staging — `importaciones/models.py`

Persiste el archivo subido + el resultado del parseo entre el POST de
subida y el POST de confirmación. Se descartaron dos alternativas:

- **Sesión de Django**: no hay precedente de guardar datos de negocio en
  sesión en este repo (todo pasa por modelos `TenantOwnedModel` +
  `for_gimnasio()`); rompe el patrón de aislamiento por tenant establecido,
  y no deja auditoría de qué se importó.
- **Todo el dataset como hidden fields en el form de preview**: una
  plantilla de 4 semanas × 5 días × 6 ejercicios × 2 hojas ya son ~240
  filas; con varios campos ocultos por fila se puede superar
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` (default 1000 en Django) y tirar
  `SuspiciousOperation`.

```python
from django.conf import settings
from django.db import models
from core.models import TenantOwnedModel


class Importacion(TenantOwnedModel):
    class Tipo(models.TextChoices):
        PLANTILLAS = "plantillas", "Plantillas de rutina"
        BIBLIOTECA = "biblioteca", "Biblioteca de ejercicios"

    class Estado(models.TextChoices):
        EN_REVISION = "en_revision", "Pendiente de revisión"
        CONFIRMADA = "confirmada", "Confirmada"
        DESCARTADA = "descartada", "Descartada"

    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    archivo = models.FileField(upload_to="importaciones/")
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.EN_REVISION
    )
    resultado = models.JSONField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "importación"
        verbose_name_plural = "importaciones"
        ordering = ["-creado"]
```

`archivo` se guarda por trazabilidad/debug ("¿qué subió realmente el
staff?"), pero el código **nunca lo vuelve a abrir** después del preview —
todo lo necesario para confirmar ya está en `resultado`. Usa el mismo
storage por default que `PagoMensual.comprobante`/`Gimnasio.logo`
(`FileSystemStorage` en dev, R2 en prod).

El POST de confirmación manda solo **decisiones** del staff (objetivo/nivel
por hoja, resolución por ejercicio ambiguo/nuevo), nunca el dataset
completo — así nunca se acerca al límite de campos sin importar el tamaño
de la planilla.

**Esquema de `resultado` para `tipo=PLANTILLAS`:**

```jsonc
{
  "hojas": [
    {
      "nombre_hoja": "Hombres",
      "dias_por_semana": 4,
      "items": [
        {
          "semana": 1, "dia": 1, "orden": 1,
          "ejercicio_original": "press de banca",
          "ejercicio_normalizado": "press de banca",
          "series": 4, "repeticiones": "8-12", "descanso": "90s", "notas": ""
        }
      ],
      "filas_invalidas": [
        {"fila_excel": 7, "motivo": "Falta la columna 'series' o no es un número"}
      ]
    }
  ],
  "ejercicios_distintos": {
    "press de banca": {"tipo": "exacto", "ejercicio_id": 12, "nombre": "Press de banca"},
    "sentadila":       {"tipo": "ambiguo", "candidato_id": 7, "candidato_nombre": "Sentadilla", "score": 91},
    "hip thrust":      {"tipo": "nuevo"}
  },
  "advertencias_columnas": ["Se encontraron 2 columnas parecidas a 'series' en la hoja 'Mujeres'; se usó la columna C."]
}
```

`ejercicios_distintos` está indexado por nombre normalizado — separado de
`items` (por fila) porque el matching se resuelve una sola vez por nombre
distinto, no por fila (ver §5).

**Esquema para `tipo=BIBLIOTECA`:**

```jsonc
{
  "items": [
    {"nombre_original": "Press de banca", "nombre_normalizado": "press de banca",
     "grupo_muscular_original": "pecho", "grupo_muscular_resuelto": "pecho",
     "url_video": "", "match": {"tipo": "nuevo"}}
  ],
  "filas_invalidas": [{"fila_excel": 4, "motivo": "Falta el nombre del ejercicio"}]
}
```

**Idempotencia**: `confirmar_importacion_*` valida `estado == EN_REVISION`
antes de escribir nada y lo cambia a `CONFIRMADA` dentro de la misma
transacción. Las vistas de preview resuelven la `Importacion` filtrando
`estado=EN_REVISION` (mismo truco que `AsignarRutinaForm` filtrando
`RutinaPlantilla.objects.for_gimnasio(gimnasio).filter(activa=True)`), así
que reabrir el link de preview de una importación ya confirmada da 404 en
vez de permitir crear las plantillas dos veces.

Estado `DESCARTADA` + vista POST-only `.../descartar/` (mismo patrón que
`RutinaPlantillaItemDeleteView`) para que el staff pueda cancelar una
importación que no quiere confirmar.

### 3. Capa de parsing — `importaciones/parsing.py` (100% puro, sin DB)

Solo conoce `openpyxl` y dataclasses propias — no importa `Ejercicio` ni
`RutinaPlantilla`. Testeable con `SimpleTestCase` (bloquea acceso a DB por
diseño, actúa de guardrail de que el módulo se mantiene puro).

```python
@dataclass(frozen=True)
class ItemParseado:
    semana: int
    dia: int
    orden: int
    ejercicio_original: str
    series: int
    repeticiones: str
    descanso: str
    notas: str

@dataclass(frozen=True)
class FilaInvalida:
    fila_excel: int
    motivo: str

@dataclass(frozen=True)
class HojaParseada:
    nombre_hoja: str
    dias_por_semana: int
    items: list[ItemParseado]
    filas_invalidas: list[FilaInvalida]

def detectar_columnas(encabezados: list[str], alias_por_campo: dict[str, list[str]]) -> tuple[dict[str, int], list[str]]:
    """Devuelve (campo_canonico -> índice de columna, advertencias),
    tolerando mayúsculas/acentos/orden. `advertencias` lista los casos de
    columnas duplicadas (dos candidatas para el mismo campo canónico -> se
    usa la primera, izquierda a derecha, y se avisa)."""

def leer_hoja_plantilla(ws) -> HojaParseada: ...
def leer_hoja_biblioteca(ws) -> tuple[list[dict], list[FilaInvalida]]: ...
def parsear_archivo_plantillas(archivo) -> list[HojaParseada]: ...
def parsear_archivo_biblioteca(archivo) -> tuple[list[dict], list[FilaInvalida]]: ...
```

**Diccionarios de alias** (campo chico y cerrado — el matching de
encabezados es por **igualdad exacta tras normalizar**, no difuso: el
universo de alias es chico y un fuzzy-match mal calibrado podría matchear
"descanso" con "descripción" por accidente. El fuzzy es solo para nombres
de ejercicio dentro de las filas, ver §4):

```python
ALIAS_PLANTILLA = {
    "semana":       ["semana", "week", "sem"],
    "dia":          ["dia", "día", "day"],
    "ejercicio":    ["ejercicio", "ejercicios", "exercise", "movimiento"],
    "series":       ["series", "serie", "sets"],
    "repeticiones": ["repeticiones", "reps", "repes", "rep"],
    "descanso":     ["descanso", "pausa", "rest"],
    "notas":        ["notas", "nota", "observaciones", "comentarios"],
}
ALIAS_BIBLIOTECA = {
    "nombre":         ["nombre", "ejercicio", "ejercicios", "exercise"],
    "grupo_muscular": ["grupo muscular", "grupo_muscular", "musculo", "músculo", "zona"],
    "url_video":      ["video", "url_video", "link", "youtube"],
}
```

**Casos límite:**

- **Columna requerida ausente en toda la hoja** (`ejercicio`, `series`,
  `repeticiones` en plantillas; `nombre` en biblioteca): esa hoja
  queda sin items válidos y se excluye del preview con un motivo explícito
  ("no se pudo importar: falta la columna 'X'") — no puede crear una
  `RutinaPlantilla` con 0 items. Campos opcionales ausentes (`dia`, `semana`,
  `descanso`, `notas`; `grupo_muscular`, `url_video` en biblioteca) se
  tratan como ausentes en todas las filas (mismo criterio que la decisión 9
  para `semana`: si la columna `dia` no existe, todas las filas van a
  `dia=1`).
- **Columna duplicada** (dos columnas matchean el mismo alias): se usa la
  primera de izquierda a derecha y se agrega una advertencia a
  `resultado["advertencias_columnas"]`, no bloqueante.
- **Celdas combinadas** (merge vertical, típico de "Semana 1" mergeada
  sobre 5 filas de días en una planilla armada a mano): `openpyxl` devuelve
  `None` para todas las celdas de un rango combinado excepto la esquina
  superior-izquierda. Se lee `ws.merged_cells.ranges` una vez por hoja, se
  construye un mapa `(fila, col) -> (fila_ancla, col_ancla)`, y se resuelve
  cada celda a través de ese mapa antes de aplicar las reglas de
  "ausente/vacía" — determinístico, sin heurística de "rellenar hacia
  abajo" que confundiría un merge real con una celda genuinamente vacía.
- `dias_por_semana` de la plantilla resultante = `max(dia)` sobre filas
  **válidas** de esa hoja (una fila inválida con un `dia` absurdo no infla
  el cálculo).
- `orden` se asigna secuencialmente dentro de cada `(semana, dia)`, no
  globalmente.

### 4. Capa de matching — `importaciones/matching.py` (mixta)

```python
def normalizar_texto(texto: str) -> str:
    """lowercase + strip de tildes (unicodedata.normalize('NFKD', ...) +
    filtrar combining marks) + colapsar espacios. Sin dependencias nuevas."""

@dataclass(frozen=True)
class MatchResultado:
    tipo: Literal["exacto", "ambiguo", "nuevo"]
    ejercicio: Ejercicio | None
    candidato: Ejercicio | None
    score: int | None

UMBRAL_AMBIGUO = 87  # rapidfuzz WRatio — a calibrar con planillas reales
PISO_SCORE = 60

def resolver_nombre(nombre_normalizado: str, indice: dict[str, Ejercicio]) -> MatchResultado:
    """PURA: recibe el índice ya armado (nombre_normalizado -> Ejercicio),
    no toca la DB. Testeable pasando un dict a mano."""

def construir_indice_ejercicios(gimnasio) -> dict[str, Ejercicio]:
    """ÚNICA función de este módulo que toca DB: Ejercicio.objects
    .for_gimnasio(gimnasio), construye {normalizar_texto(e.nombre): e}."""

ALIAS_GRUPO_MUSCULAR = {
    "abdomen": Ejercicio.GrupoMuscular.CORE, "abs": Ejercicio.GrupoMuscular.CORE,
    "gluteos": Ejercicio.GrupoMuscular.PIERNAS,
    # ...
}
def resolver_grupo_muscular(texto: str) -> str | None:
    """PURA: normaliza y matchea contra choices + alias. None si no hay
    match confiable (el staff lo completa a mano en preview, decisión 10 —
    nunca un default silencioso)."""
```

**Pipeline por nombre, resuelto UNA VEZ por nombre distinto, no por
fila**: si "Press de banca" aparece en 12 filas entre 2 hojas, el staff no
responde la misma pregunta 12 veces, y el sistema no crea 12 `Ejercicio`
distintos si decide "nuevo". `previsualizar_importacion_plantillas`
recolecta primero el `set()` de `ejercicio_normalizado` de todas las filas
válidas de todas las hojas, y recién ahí llama a `resolver_nombre` una vez
por nombre distinto.

1. `normalizar_texto(nombre_original)`.
2. **Exact match** contra `construir_indice_ejercicios(gimnasio)` →
   `tipo="exacto"`. Cubre todo el caso de mayúsculas/acentos sin necesitar
   `rapidfuzz`.
3. Si no hay exacto: `rapidfuzz.process.extractOne(nombre_normalizado,
   choices=indice.keys(), scorer=rapidfuzz.fuzz.WRatio)`.
   - `score >= UMBRAL_AMBIGUO` (87) → `tipo="ambiguo"`, con `candidato` y
     `score`. En el preview, **pre-marcado en "usar el existente"**
     (decisión 11).
   - `score < PISO_SCORE` (60) → `tipo="nuevo"`, sin candidato sugerido
     (evita ruido de sugerencias sin sentido).
   - `PISO_SCORE <= score < UMBRAL_AMBIGUO` → también `tipo="ambiguo"`,
     sugerencia de menor confianza, mismo bucket de UI.

Umbrales como constantes nombradas (no números mágicos enterrados),
cubiertas por tests de regresión con pares concretos ("Sentadila"/
"Sentadilla") — a calibrar con una planilla real del gimnasio antes de
producción.

**`grupo_muscular` de ejercicios nuevos** (decisión 10): en el import de
**biblioteca**, si la columna existe se intenta `resolver_grupo_muscular`;
sin match confiable, `None` y el staff lo elige en preview. En el import de
**plantillas** (sin columna de grupo muscular en absoluto), cada ejercicio
`tipo="nuevo"` requiere que el staff elija `grupo_muscular` en el preview
antes de poder confirmar — no hay forma de evitarlo sin violar la
restricción not-blank del modelo.

### 5. Capa de orquestación — `importaciones/services.py` (impura, transaccional)

Análoga a `RutinaAsignada.crear_desde_plantilla`/`turnos/services.py`:

```python
class ImportacionInvalida(Exception):
    """Mensaje en español listo para messages.error(), análoga a ErrorDeReserva."""

def previsualizar_importacion_plantillas(*, gimnasio, archivo, usuario) -> Importacion: ...
def previsualizar_importacion_biblioteca(*, gimnasio, archivo, usuario) -> Importacion: ...

def confirmar_importacion_plantillas(*, importacion, gimnasio, decisiones) -> list[RutinaPlantilla]:
    """decisiones = {
         "hojas": [{"incluir": True, "objetivo": "...", "nivel": "..."}],
         "ejercicios": {"sentadila": {"accion": "usar_existente", "ejercicio_id": 7},
                        "hip thrust": {"accion": "crear_nuevo", "grupo_muscular": "piernas"}},
       }
    Valida importacion.gimnasio_id == gimnasio.id y estado == EN_REVISION
    (si no, ImportacionInvalida). Todo en transaction.atomic()."""

def confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones) -> list[Ejercicio]: ...
```

### 6. Vistas y forms — `importaciones/views.py`, `importaciones/forms.py`

`StaffRequiredMixin` + `TenantScopedMixin`, mismo patrón que el resto de
`rutinas`:

- `SubirPlantillasView`/`SubirBibliotecaView` (`FormView`): valida
  extensión `.xlsx` + tamaño máximo (5MB — primer `FileField` de este repo
  con validación explícita de tamaño/tipo, no hay precedente), llama al
  `previsualizar_*` correspondiente, redirige al preview.
- `PreviewPlantillasView`/`PreviewBibliotecaView` (`View`, `get`/`post`):
  `get_importacion()` filtra `Importacion.objects.for_gimnasio(gimnasio)`
  + `estado=EN_REVISION` (aísla por tenant y da 404 sobre ya confirmadas).
  GET renderiza formsets con `initial=` desde `importacion.resultado`; POST
  valida y llama a `confirmar_importacion_*`.
- `DescartarImportacionView` (POST-only, patrón `RutinaPlantillaItemDeleteView`).

Tres `forms.formset_factory` (mecanismo idiomático de Django para N
repeticiones de un sub-form — no hay precedente de campos dinámicos hechos
a mano en este repo):

```python
class SubirArchivoForm(forms.Form):
    archivo = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])]
    )
    # clean_archivo intenta openpyxl.load_workbook y traduce
    # InvalidFileException/BadZipFile a ValidationError en español.

class HojaMetadataForm(forms.Form):
    nombre_hoja = forms.CharField(widget=forms.HiddenInput)
    incluir = forms.BooleanField(required=False, initial=True)
    objetivo = forms.CharField(max_length=120)
    nivel = forms.ChoiceField(choices=RutinaPlantilla.Nivel.choices)

HojaMetadataFormSet = forms.formset_factory(HojaMetadataForm, extra=0)

class ResolucionEjercicioForm(forms.Form):
    nombre_normalizado = forms.CharField(widget=forms.HiddenInput)
    accion = forms.ChoiceField(choices=[("usar_existente", "Usar existente"),
                                         ("crear_nuevo", "Crear como nuevo")])
    ejercicio_existente_id = forms.IntegerField(required=False)
    grupo_muscular = forms.ChoiceField(
        choices=Ejercicio.GrupoMuscular.choices, required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("accion") == "crear_nuevo" and not cleaned.get("grupo_muscular"):
            self.add_error("grupo_muscular", "Elegí un grupo muscular para el ejercicio nuevo.")
        return cleaned

ResolucionEjercicioFormSet = forms.formset_factory(ResolucionEjercicioForm, extra=0)

class ResolucionGrupoMuscularForm(forms.Form):
    valor_original = forms.CharField(widget=forms.HiddenInput)
    grupo_muscular = forms.ChoiceField(choices=Ejercicio.GrupoMuscular.choices)

ResolucionGrupoMuscularFormSet = forms.formset_factory(ResolucionGrupoMuscularForm, extra=0)
```

### 7. URLs y templates

```python
# importaciones/urls.py
app_name = "importaciones"
urlpatterns = [
    path("", ImportacionListView.as_view(), name="listado"),
    path("plantillas/subir/", SubirPlantillasView.as_view(), name="plantillas_subir"),
    path("plantillas/<int:pk>/preview/", PreviewPlantillasView.as_view(), name="plantillas_preview"),
    path("plantillas/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="plantillas_descartar"),
    path("biblioteca/subir/", SubirBibliotecaView.as_view(), name="biblioteca_subir"),
    path("biblioteca/<int:pk>/preview/", PreviewBibliotecaView.as_view(), name="biblioteca_preview"),
    path("biblioteca/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="biblioteca_descartar"),
]
```

`templates/importaciones/plantillas_subir.html`/`biblioteca_subir.html`:
mismo patrón que `pagos/pago_confirmar.html` —
`<form method="post" enctype="multipart/form-data" hx-boost="false">`.

`templates/importaciones/plantillas_preview.html`: tres bloques dentro de
un único `<form method="post" hx-boost="false">` — (1) una tarjeta por hoja
con el formset de objetivo/nivel/incluir y la cantidad de items válidos,
(2) tabla de filas inválidas por hoja con motivo, (3) tabla de ejercicios a
resolver (ambiguos + nuevos) con el formset de resolución, mostrando nombre
original, candidato sugerido si lo hay, y score.

### 8. Otros archivos a tocar

- `config/urls.py`: `path('importaciones/', include('importaciones.urls'))`.
- `requirements.txt`: `+openpyxl`, `+rapidfuzz` (versión exacta a fijar al
  implementar).
- `templates/base.html` (o el partial de nav correspondiente): links
  "Importar rutinas"/"Importar ejercicios" cerca de los de `rutinas`/`ejercicios`.
- `importaciones/admin.py`: registro simple de `Importacion` (list_display
  tipo/estado/gimnasio/creado/creado_por, `resultado` readonly) para debug.

## Tests

- **`parsing.py`** (`SimpleTestCase`, sin DB): alias case/acentos-insensible;
  columna requerida ausente en toda la hoja excluye esa hoja con motivo;
  columna opcional ausente → filas con default; columna duplicada → usa la
  primera + advertencia; celdas combinadas resueltas vía el mapa de rangos
  (test construyendo un `openpyxl.Workbook()` en memoria con
  `ws.merge_cells(...)`); fila inválida (falta ejercicio/series/repeticiones)
  cae a `filas_invalidas` sin frenar el resto; `dia`/`semana` ausentes →
  todas las filas en 1; `dias_por_semana` = `max(dia)` solo sobre filas
  válidas; `orden`
  secuencial dentro de `(semana, dia)`; multi-hoja produce una
  `HojaParseada` por hoja con `nombre_hoja` correcto.
- **`matching.py`**: `normalizar_texto` (mayúsculas/acentos → mismo
  string); exacto tras normalizar sin necesitar `rapidfuzz`; typo genuino →
  `tipo="ambiguo"` con `candidato`/`score` (fija el umbral como test de
  regresión); nombre sin relación → `tipo="nuevo"` sin candidato;
  deduplicación (mismo nombre en 12 filas se resuelve una sola vez — test
  sobre `previsualizar_importacion_plantillas`); `resolver_grupo_muscular`
  exact-match + alias, sin match → `None`; `construir_indice_ejercicios`
  con `TestCase` real, aislamiento de tenant.
- **`services.py`** (`TestCase`): preview no crea `RutinaPlantilla`/
  `Ejercicio` todavía, solo la `Importacion`; confirmación crea
  plantilla+items correctos por hoja incluida; "usar_existente" reutiliza
  el mismo `pk` (no duplica); "crear_nuevo" crea exactamente un `Ejercicio`
  por nombre distinto (no uno por fila) con el `grupo_muscular` elegido;
  re-confirmar una `Importacion` ya `CONFIRMADA` lanza `ImportacionInvalida`
  sin crear nada más; atomicidad (error a mitad de camino no deja
  `RutinaPlantilla`/`Ejercicio` huérfano); biblioteca análogo, más
  re-subir con nombres ya existentes no duplica; aislamiento de tenant en
  `Importacion.objects.for_gimnasio()` y en confirmar una `Importacion` de
  otro gimnasio.
- **Vistas** (`self.client`, `TestCase`, mirror de `RutinasViewsTests`):
  anónimo → redirect a login; rol `alumno` → 403; archivo no-`.xlsx` → error
  de form, no crea `Importacion`; preview de `Importacion` de otro gimnasio
  → 404; GET a preview de una ya `CONFIRMADA` → 404; flujo feliz end-to-end
  (`.xlsx` multi-hoja generado con `openpyxl` en el test, subido vía
  `SimpleUploadedFile`, preview, POST de confirmación, verificar cantidad
  de `RutinaPlantilla` creadas); regresión de `DATA_UPLOAD_MAX_NUMBER_FIELDS`
  con una hoja de ~500 filas, confirmando que el diseño de §2 (decisiones
  en el confirm POST, no el dataset completo) sostiene ese caso.

## Riesgos / decisiones a validar antes de producción

- Umbrales de `rapidfuzz` (87/60) son puntos de partida razonables para
  nombres cortos en español, pero conviene calibrarlos con una planilla
  real de un gimnasio antes de confiar en el default.
- Límite de tamaño de archivo (5MB) es una cifra propia sin precedente en
  el repo — ajustable si hace falta.
- `Importacion` en estado `EN_REVISION` abandonadas (el staff nunca vuelve
  a confirmar/descartar): no bloqueante para este alcance (quedan como
  filas de auditoría "colgadas"); un management command de purga por
  antigüedad queda fuera de este spec, a evaluar si se vuelve un problema
  real.
