# Importador de planes de entrenamiento desde Excel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nueva app `importaciones` que deja al staff subir un `.xlsx` con sus planes de entrenamiento (o su biblioteca de ejercicios) y, tras una pantalla de preview donde revisa/corrige lo que se va a crear, confirma y la app crea `RutinaPlantilla`/`RutinaPlantillaItem`/`Ejercicio` reales.

**Architecture:** App nueva de 3 capas crecientemente acopladas a Django: `parsing.py` (100% puro, `openpyxl`, sin DB), `matching.py` (mixto: normalización + `rapidfuzz` puros, una sola función toca DB), `services.py` (transaccional, orquesta y escribe). Un modelo de staging `Importacion` persiste el archivo + el resultado parseado (`JSONField`) entre el POST de subida y el POST de confirmación — el confirm POST manda solo decisiones del staff, nunca el dataset completo.

**Tech Stack:** Django 5.2, `openpyxl` (parsing de `.xlsx`), `rapidfuzz` (matching difuso), `django.test.SimpleTestCase`/`TestCase`.

Spec: `docs/superpowers/specs/2026-07-27-importador-planes-entrenamiento-design.md`.

## Global Constraints

- El ciclo de semanas es el del Proyecto 1, ya en `main`: `RutinaPlantillaItem.semana` (1-4, default=1), `SEMANAS_POR_CICLO = 4` en `rutinas/models.py`. Este plan NO lo modifica.
- La salida del import SIEMPRE es `RutinaPlantilla` — nunca `RutinaAsignada`. El import no consulta `Alumno` en ningún punto.
- Sin integración OAuth con Google Sheets — solo archivo `.xlsx` subido por `multipart/form-data`.
- Nada se escribe en `RutinaPlantilla`/`RutinaPlantillaItem`/`Ejercicio` hasta que el staff confirma el preview. La subida (`previsualizar_*`) solo crea la fila `Importacion`.
- Matches ambiguos de nombre de ejercicio (`rapidfuzz` score entre `PISO_SCORE=60` y `UMBRAL_AMBIGUO=87`, o `>= 87`) quedan **pre-marcados en "usar existente"** en el preview — el staff elige activamente "crear nuevo" si corresponde.
- `Ejercicio.grupo_muscular` es `choices` cerrado de 8 valores, sin "otro" — todo ejercicio nuevo (import de plantillas o de biblioteca sin match confiable) requiere que el staff lo elija en el preview antes de confirmar. Nunca un default silencioso.
- Filas inválidas se saltean y se listan con motivo — nunca invalidan el archivo/hoja entera, salvo que falte una columna REQUERIDA en TODA la hoja (ahí se excluye esa hoja, no el archivo). Para plantillas, las columnas requeridas son `ejercicio`, `series` y `repeticiones` — `dia` (igual que `semana`) es opcional: si la columna no existe, todas las filas van a `dia=1` (mismo criterio de default que ya aplica a `semana`, decisión 9 del spec). Para biblioteca, la única columna requerida es `nombre`.
- Multi-hoja: cada hoja del `.xlsx` → una `RutinaPlantilla` independiente, nombrada según el nombre de la hoja.
- `normalizar_texto` (lowercase + sin tildes + espacios colapsados) vive en `importaciones/parsing.py` (no en `matching.py` como sugería un borrador temprano del spec — es una utilidad de texto sin ninguna dependencia, y `parsing.py` la necesita primero, para detectar encabezados; `matching.py` la importa desde ahí para normalizar nombres de ejercicio). Un solo lugar, sin duplicación.
- La validación de que el archivo sea un `.xlsx` legítimo (no solo la extensión) vive en `services.py` (`previsualizar_importacion_*` captura `ERRORES_ARCHIVO_INVALIDO` y levanta `ImportacionInvalida`), no en `SubirArchivoForm.clean_archivo` como sugería un borrador temprano del spec — mantiene toda la lógica de parseo en un solo lugar (`services.py` ya es el único punto que abre el archivo con `openpyxl`) en vez de duplicar el intento de apertura en el form. El form solo valida la extensión (barato, feedback inmediato); la vista traduce `ImportacionInvalida` a un error de form vía `form.add_error(None, str(exc))`.
- Este repo no testea `admin.py` en ningún app (ver `rutinas/admin.py`, sin tests) — el `admin.py` de esta app tampoco lleva test dedicado.
- Todas las vistas de gestión combinan `tenants.mixins.StaffRequiredMixin` + `core.mixins.TenantScopedMixin` (`StaffRequiredMixin` primero en el MRO), mismo patrón que el resto del repo.
- `forms.Form` planos que corren bajo `TenantScopedMixin` deben aceptar `gimnasio` como kwarg en `__init__` (el mixin lo inyecta siempre vía `get_form_kwargs()`), aunque no lo usen — mismo patrón que `AsignarRutinaForm` en `rutinas/forms.py`.

---

### Task 1: App `importaciones` — modelo `Importacion`, migración, admin, settings

**Files:**
- Create: `importaciones/__init__.py`, `importaciones/apps.py`, `importaciones/models.py`, `importaciones/admin.py`, `importaciones/migrations/__init__.py`, `importaciones/migrations/0001_initial.py` (generada), `importaciones/tests.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`), `requirements.txt` (agregar `openpyxl`)

**Interfaces:**
- Produces: modelo `Importacion(TenantOwnedModel)` en `importaciones/models.py`, con `Importacion.Tipo` (`PLANTILLAS`/`BIBLIOTECA`) y `Importacion.Estado` (`EN_REVISION`/`CONFIRMADA`/`DESCARTADA`) como `TextChoices` anidados. Usado por todas las tareas siguientes.

- [ ] **Step 1: Crear el esqueleto de la app**

```bash
mkdir -p importaciones/migrations
touch importaciones/__init__.py importaciones/migrations/__init__.py
```

`importaciones/apps.py`:

```python
from django.apps import AppConfig


class ImportacionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'importaciones'
```

- [ ] **Step 2: Escribir los tests que fallan**

`importaciones/tests.py`:

```python
"""Tests de `importaciones`. Ver `rutinas/tests.py` para el estilo de
fixtures de este repo."""

from django.test import TestCase

from importaciones.models import Importacion
from tenants.models import Gimnasio


class ImportacionModeloTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio Central", slug="gimnasio-central"
        )

    def test_creacion_con_defaults(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.PLANTILLAS,
            resultado={"hojas": []},
        )
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertIsNone(importacion.confirmado_en)
        self.assertIsNone(importacion.creado_por)

    def test_str(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio,
            tipo=Importacion.Tipo.BIBLIOTECA,
            resultado={"items": []},
        )
        self.assertIn("Gimnasio Central", str(importacion))


class ImportacionAislamientoTenantTests(TestCase):
    def test_for_gimnasio_no_mezcla_gimnasios(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        Importacion.objects.create(
            gimnasio=gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        Importacion.objects.create(
            gimnasio=gimnasio_b, tipo=Importacion.Tipo.PLANTILLAS, resultado={}
        )
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_a).count(), 1)
        self.assertEqual(Importacion.objects.for_gimnasio(gimnasio_b).count(), 1)
```

- [ ] **Step 3: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.models'` (la app ni el modelo existen todavía).

- [ ] **Step 4: Implementar el modelo**

`importaciones/models.py`:

```python
"""Staging de importaciones desde Excel (Proyecto 2).

`Importacion` persiste el archivo subido + el resultado del parseo entre el
POST de subida y el POST de confirmación -- ver spec
`2026-07-27-importador-planes-entrenamiento-design.md` §2 para por qué NO es
sesión de Django ni hidden-fields en el form de preview. El código nunca
vuelve a abrir `archivo` después del preview; todo lo que hace falta para
confirmar ya está en `resultado`.
"""

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
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        # SET_NULL: borrar el usuario que subió el archivo no debe borrar
        # el historial de importaciones del gimnasio.
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "importación"
        verbose_name_plural = "importaciones"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.gimnasio}"
```

Agregar `'importaciones'` a `INSTALLED_APPS` en `config/settings.py`, inmediatamente después de `'rutinas'`:

```python
    'rutinas',
    'importaciones',
    'pagos',
```

Generar la migración:

Run: `python manage.py makemigrations importaciones`
Expected: crea `importaciones/migrations/0001_initial.py`.

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Admin (sin test dedicado — ver Global Constraints)**

`importaciones/admin.py`:

```python
from django.contrib import admin

from importaciones.models import Importacion


@admin.register(Importacion)
class ImportacionAdmin(admin.ModelAdmin):
    list_display = ("tipo", "gimnasio", "estado", "creado", "creado_por")
    list_filter = ("gimnasio", "tipo", "estado")
    readonly_fields = ("resultado",)
```

- [ ] **Step 7: Agregar `openpyxl` a `requirements.txt`**

```
# Parte C (integración Google Calendar del alumno)
google-auth==2.55.2
google-auth-oauthlib==1.4.0
google-api-python-client==2.198.0
cryptography==48.0.1

# Proyecto 2 (importador de planes desde Excel)
openpyxl==3.1.5
```

Run: `pip install openpyxl==3.1.5` (o la versión que quede fijada en el entorno) para que las tareas siguientes puedan importarlo.

- [ ] **Step 8: Commit**

```bash
git add importaciones/ config/settings.py requirements.txt
git commit -m "feat(importaciones): scaffold de la app + modelo Importacion"
```

---

### Task 2: `parsing.py` — normalización de texto y detección de columnas

**Files:**
- Create: `importaciones/parsing.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Produces: `normalizar_texto(texto: str) -> str`, `ALIAS_PLANTILLA: dict[str, list[str]]`, `ALIAS_BIBLIOTECA: dict[str, list[str]]`, `detectar_columnas(encabezados: list[str | None], alias_por_campo: dict[str, list[str]]) -> tuple[dict[str, int], list[str]]`. `normalizar_texto` es consumida por `matching.py` (Task 5). Todo lo demás de este archivo, por el resto de `parsing.py` (Tasks 3-4).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from django.test import SimpleTestCase

from importaciones.parsing import (
    ALIAS_BIBLIOTECA,
    ALIAS_PLANTILLA,
    detectar_columnas,
    normalizar_texto,
)


class NormalizarTextoTests(SimpleTestCase):
    def test_lowercase_y_sin_tildes(self):
        self.assertEqual(normalizar_texto("Press de Banca"), "press de banca")
        self.assertEqual(normalizar_texto("PRÉSS DÉ BÁNCA"), "press de banca")

    def test_colapsa_espacios(self):
        self.assertEqual(normalizar_texto("  Sentadilla   libre  "), "sentadilla libre")

    def test_texto_vacio(self):
        self.assertEqual(normalizar_texto(""), "")
        self.assertEqual(normalizar_texto(None), "")


class DetectarColumnasTests(SimpleTestCase):
    def test_matchea_por_alias_case_y_acento_insensible(self):
        encabezados = ["Semana", "Día", "Ejercicio", "Series", "Repeticiones", "Descanso", "Notas"]
        campos, advertencias = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(
            campos,
            {"semana": 0, "dia": 1, "ejercicio": 2, "series": 3,
             "repeticiones": 4, "descanso": 5, "notas": 6},
        )
        self.assertEqual(advertencias, [])

    def test_orden_de_columnas_no_importa(self):
        encabezados = ["Ejercicio", "Series", "Dia"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos, {"ejercicio": 0, "series": 1, "dia": 2})

    def test_columna_no_encontrada_no_esta_en_el_resultado(self):
        encabezados = ["Ejercicio", "Series"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertNotIn("descanso", campos)
        self.assertNotIn("semana", campos)

    def test_columna_duplicada_usa_la_primera_y_avisa(self):
        encabezados = ["Series", "Ejercicio", "Sets"]  # "Series" y "Sets" son alias de "series"
        campos, advertencias = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos["series"], 0)
        self.assertEqual(len(advertencias), 1)
        self.assertIn("series", advertencias[0].lower())

    def test_encabezado_none_se_ignora(self):
        # Celda de header vacía (columna sin usar en la planilla real).
        encabezados = ["Ejercicio", None, "Series"]
        campos, _ = detectar_columnas(encabezados, ALIAS_PLANTILLA)
        self.assertEqual(campos, {"ejercicio": 0, "series": 2})

    def test_alias_biblioteca(self):
        encabezados = ["Nombre", "Grupo Muscular", "Video"]
        campos, _ = detectar_columnas(encabezados, ALIAS_BIBLIOTECA)
        self.assertEqual(campos, {"nombre": 0, "grupo_muscular": 1, "url_video": 2})
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.NormalizarTextoTests importaciones.tests.DetectarColumnasTests -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.parsing'`.

- [ ] **Step 3: Implementar**

`importaciones/parsing.py`:

```python
"""Parsing puro de archivos `.xlsx` (Proyecto 2).

Este módulo NO importa nada de Django ni de los modelos de dominio
(`Ejercicio`, `RutinaPlantilla`): recibe un archivo, devuelve dataclasses.
Es lo que lo hace testeable con `SimpleTestCase` sin fixtures de tenant, y
lo que permite reusarlo desde `services.py` sin acoplar el parseo a la
persistencia. `normalizar_texto` vive acá (no en `matching.py`) porque este
módulo la necesita primero, para detectar encabezados; `matching.py` la
importa desde acá para normalizar nombres de ejercicio -- un solo lugar.
"""

import unicodedata

ALIAS_PLANTILLA = {
    "semana": ["semana", "week", "sem"],
    "dia": ["dia", "día", "day"],
    "ejercicio": ["ejercicio", "ejercicios", "exercise", "movimiento"],
    "series": ["series", "serie", "sets"],
    "repeticiones": ["repeticiones", "reps", "repes", "rep"],
    "descanso": ["descanso", "pausa", "rest"],
    "notas": ["notas", "nota", "observaciones", "comentarios"],
}

ALIAS_BIBLIOTECA = {
    "nombre": ["nombre", "ejercicio", "ejercicios", "exercise"],
    "grupo_muscular": ["grupo muscular", "grupo_muscular", "musculo", "músculo", "zona"],
    "url_video": ["video", "url_video", "link", "youtube"],
}


def normalizar_texto(texto):
    """lowercase + sin tildes + espacios colapsados. `None` -> `""`."""
    if not texto:
        return ""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", str(texto))
        if not unicodedata.combining(c)
    )
    return " ".join(sin_tildes.lower().split())


def detectar_columnas(encabezados, alias_por_campo):
    """Devuelve (campo_canonico -> índice de columna, advertencias).

    Para cada campo canónico, busca la PRIMERA columna (izquierda a
    derecha) cuyo encabezado normalizado esté en su lista de alias. Un
    campo sin ninguna columna que matchee simplemente no aparece en el
    dict de salida -- el caller decide si es requerido u opcional.
    """
    normalizados = [normalizar_texto(e) for e in encabezados]
    campos = {}
    advertencias = []
    for campo, alias in alias_por_campo.items():
        indices = [i for i, valor in enumerate(normalizados) if valor in alias]
        if not indices:
            continue
        campos[campo] = indices[0]
        if len(indices) > 1:
            advertencias.append(
                f"Se encontraron {len(indices)} columnas parecidas a "
                f"'{campo}'; se usó la columna {indices[0] + 1}."
            )
    return campos, advertencias
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.NormalizarTextoTests importaciones.tests.DetectarColumnasTests -v 2`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/parsing.py importaciones/tests.py
git commit -m "feat(importaciones): normalizar_texto + detectar_columnas"
```

---

### Task 3: `parsing.py` — celdas combinadas y `leer_hoja_plantilla`

**Files:**
- Modify: `importaciones/parsing.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `normalizar_texto`, `detectar_columnas`, `ALIAS_PLANTILLA` (Task 2).
- Produces: dataclasses `ItemParseado`, `FilaInvalida`, `HojaParseada`; helpers privados `_mapa_merges(ws)`, `_valor_celda(ws, fila, col, mapa_merges) -> object`, `_fila_vacia(valores) -> bool`; función `leer_hoja_plantilla(ws) -> HojaParseada`. Consumidas por `leer_hoja_biblioteca`/`parsear_archivo_plantillas`/`parsear_archivo_biblioteca` (Task 4, que reusa `_mapa_merges`/`_valor_celda`/`_fila_vacia` para el import de biblioteca también) y por `services.py` (Task 6).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py` (import nuevo: `import openpyxl` arriba del archivo):

```python
import openpyxl

from importaciones.parsing import (
    FilaInvalida,
    HojaParseada,
    ItemParseado,
    leer_hoja_plantilla,
)


def _hoja_plantilla_basica():
    """Workbook en memoria con encabezados + 2 filas válidas, sin celdas
    combinadas. Reutilizado por varios tests de este módulo."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones", "Descanso", "Notas"])
    ws.append([1, 1, "Press de banca", 4, "8-12", "90s", ""])
    ws.append([1, 1, "Sentadilla", 3, "10", "60s", "Cuidar la técnica"])
    return ws


class LeerHojaPlantillaTests(SimpleTestCase):
    def test_lee_filas_validas(self):
        hoja = leer_hoja_plantilla(_hoja_plantilla_basica())
        self.assertEqual(len(hoja.items), 2)
        self.assertEqual(hoja.items[0], ItemParseado(
            semana=1, dia=1, orden=1, ejercicio_original="Press de banca",
            series=4, repeticiones="8-12", descanso="90s", notas="",
        ))
        self.assertEqual(hoja.items[1].orden, 2)  # segundo item del mismo (semana, dia)
        self.assertEqual(hoja.filas_invalidas, [])

    def test_dias_por_semana_es_el_maximo_dia_de_filas_validas(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([3, "Sentadilla", 3, "10"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.dias_por_semana, 3)

    def test_fila_sin_semana_cae_en_semana_1(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])  # sin columna Semana
        ws.append([1, "Press de banca", 4, "8-12"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items[0].semana, 1)

    def test_fila_con_series_invalida_se_saltea_con_motivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", "cuatro", "8-12"])  # "series" no numérico
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items, [])
        self.assertEqual(len(hoja.filas_invalidas), 1)
        self.assertEqual(hoja.filas_invalidas[0].fila_excel, 2)  # fila 1 = header
        self.assertIn("series", hoja.filas_invalidas[0].motivo.lower())

    def test_fila_totalmente_vacia_se_saltea_sin_motivo_ruidoso(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([None, None, None, None])
        ws.append([1, "Sentadilla", 3, "10"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(len(hoja.items), 2)
        self.assertEqual(hoja.filas_invalidas, [])  # vacía != inválida, se ignora en silencio

    def test_columna_ejercicio_ausente_devuelve_hoja_sin_items(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Dia", "Series", "Repeticiones"])  # sin "Ejercicio"
        ws.append([1, 4, "8-12"])
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items, [])
        self.assertEqual(hoja.dias_por_semana, 0)

    def test_orden_secuencial_dentro_de_semana_y_dia(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, 1, "Press de banca", 4, "8-12"])
        ws.append([1, 1, "Sentadilla", 3, "10"])
        ws.append([2, 1, "Peso muerto", 3, "8"])  # otra semana: orden vuelve a 1
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual([i.orden for i in hoja.items], [1, 2, 1])

    def test_celda_combinada_de_semana_se_resuelve_por_el_ancla(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Semana", "Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, 1, "Press de banca", 4, "8-12"])
        ws.append([None, 2, "Sentadilla", 3, "10"])  # "Semana" mergeada con la fila de arriba
        ws.merge_cells("A2:A3")
        hoja = leer_hoja_plantilla(ws)
        self.assertEqual(hoja.items[1].semana, 1)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.LeerHojaPlantillaTests -v 2`
Expected: ERROR — `ImportError` (`leer_hoja_plantilla`/`ItemParseado`/etc. no existen todavía).

- [ ] **Step 3: Implementar**

Agregar a `importaciones/parsing.py` (después de `detectar_columnas`):

```python
from dataclasses import dataclass, field


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
    items: list = field(default_factory=list)
    filas_invalidas: list = field(default_factory=list)


def _mapa_merges(ws):
    """(fila, col) 1-indexed -> (fila_ancla, col_ancla) para cada celda
    dentro de un rango combinado. openpyxl devuelve `None` para toda celda
    de un merge salvo la esquina superior-izquierda; sin este mapa, una
    columna mergeada verticalmente (típico de "Semana 1" armada a mano)
    se leería como si esas filas no tuvieran valor."""
    mapa = {}
    for rango in ws.merged_cells.ranges:
        ancla = (rango.min_row, rango.min_col)
        for fila in range(rango.min_row, rango.max_row + 1):
            for col in range(rango.min_col, rango.max_col + 1):
                mapa[(fila, col)] = ancla
    return mapa


def _valor_celda(ws, fila, col, mapa_merges):
    fila_ancla, col_ancla = mapa_merges.get((fila, col), (fila, col))
    return ws.cell(row=fila_ancla, column=col_ancla).value


def _fila_vacia(valores):
    return all(v is None or str(v).strip() == "" for v in valores)


def leer_hoja_plantilla(ws):
    """Parsea una hoja de un archivo de PLANTILLAS. `ws` es una worksheet
    de `openpyxl` ya abierta (no toca el filesystem acá)."""
    encabezados = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    campos, advertencias = detectar_columnas(encabezados, ALIAS_PLANTILLA)

    if "ejercicio" not in campos or "series" not in campos or "repeticiones" not in campos:
        return HojaParseada(nombre_hoja=ws.title, dias_por_semana=0)

    mapa_merges = _mapa_merges(ws)
    ncols = len(encabezados)
    items = []
    filas_invalidas = []
    contador_orden = {}  # (semana, dia) -> próximo orden

    for fila_idx in range(2, ws.max_row + 1):
        valores = [_valor_celda(ws, fila_idx, c, mapa_merges) for c in range(1, ncols + 1)]
        if _fila_vacia(valores):
            continue

        ejercicio = valores[campos["ejercicio"]]
        if not ejercicio or not str(ejercicio).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta el nombre del ejercicio"))
            continue

        series_raw = valores[campos["series"]]
        try:
            series = int(series_raw)
        except (TypeError, ValueError):
            filas_invalidas.append(
                FilaInvalida(fila_idx, "La columna 'series' no es un número")
            )
            continue

        repeticiones = valores[campos["repeticiones"]]
        if repeticiones is None or not str(repeticiones).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta 'repeticiones'"))
            continue

        semana_raw = valores[campos["semana"]] if "semana" in campos else None
        try:
            semana = int(semana_raw) if semana_raw is not None else 1
        except (TypeError, ValueError):
            semana = 1

        dia_raw = valores[campos["dia"]] if "dia" in campos else None
        try:
            dia = int(dia_raw) if dia_raw is not None else 1
        except (TypeError, ValueError):
            filas_invalidas.append(FilaInvalida(fila_idx, "La columna 'dia' no es un número"))
            continue

        clave_orden = (semana, dia)
        contador_orden[clave_orden] = contador_orden.get(clave_orden, 0) + 1

        descanso = valores[campos["descanso"]] if "descanso" in campos else None
        notas = valores[campos["notas"]] if "notas" in campos else None

        items.append(ItemParseado(
            semana=semana,
            dia=dia,
            orden=contador_orden[clave_orden],
            ejercicio_original=str(ejercicio).strip(),
            series=series,
            repeticiones=str(repeticiones).strip(),
            descanso=str(descanso).strip() if descanso else "",
            notas=str(notas).strip() if notas else "",
        ))

    dias_por_semana = max((i.dia for i in items), default=0)
    return HojaParseada(
        nombre_hoja=ws.title,
        dias_por_semana=dias_por_semana,
        items=items,
        filas_invalidas=filas_invalidas,
    )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.LeerHojaPlantillaTests -v 2`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/parsing.py importaciones/tests.py
git commit -m "feat(importaciones): leer_hoja_plantilla + resolución de celdas combinadas"
```

---

### Task 4: `parsing.py` — biblioteca y orquestación multi-hoja

**Files:**
- Modify: `importaciones/parsing.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `detectar_columnas`, `ALIAS_BIBLIOTECA`, `_mapa_merges`, `_valor_celda`, `_fila_vacia`, `FilaInvalida`, `leer_hoja_plantilla` (Tasks 2-3).
- Produces: `leer_hoja_biblioteca(ws) -> tuple[list[dict], list[FilaInvalida]]`, `parsear_archivo_plantillas(archivo) -> list[HojaParseada]`, `parsear_archivo_biblioteca(archivo) -> tuple[list[dict], list[FilaInvalida]]`. Consumidas por `services.py` (Tasks 6, 8). También produce el helper de test `_archivo_xlsx(wb) -> SimpleUploadedFile` (función a nivel de módulo en `importaciones/tests.py`, no dentro de ninguna clase) — lo reusan, sin redefinirlo, los tests de las Tasks 6-8 y 10-12.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
import io

from django.core.files.uploadedfile import SimpleUploadedFile

from importaciones.parsing import (
    leer_hoja_biblioteca,
    parsear_archivo_biblioteca,
    parsear_archivo_plantillas,
)


def _archivo_xlsx(wb):
    """Serializa un Workbook de openpyxl a un SimpleUploadedFile, como
    llegaría desde un <input type=file>."""
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        "plan.xlsx", buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class LeerHojaBibliotecaTests(SimpleTestCase):
    def test_lee_ejercicios_validos(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular", "Video"])
        ws.append(["Press de banca", "Pecho", "https://youtube.com/x"])
        ws.append(["Sentadilla", "Piernas", ""])
        items, invalidas = leer_hoja_biblioteca(ws)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["nombre_original"], "Press de banca")
        self.assertEqual(items[0]["grupo_muscular_original"], "Pecho")
        self.assertEqual(items[0]["url_video"], "https://youtube.com/x")
        self.assertEqual(invalidas, [])

    def test_fila_sin_nombre_se_saltea_con_motivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["", "Pecho"])
        items, invalidas = leer_hoja_biblioteca(ws)
        self.assertEqual(items, [])
        self.assertEqual(len(invalidas), 1)

    def test_columna_grupo_muscular_es_opcional(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Press de banca"])
        items, _ = leer_hoja_biblioteca(ws)
        self.assertIsNone(items[0]["grupo_muscular_original"])


class ParsearArchivoPlantillasTests(SimpleTestCase):
    def test_multi_hoja_produce_una_hojaparseada_por_hoja(self):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Press de banca", 4, "8-12"])
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Sentadilla", 3, "10"])

        hojas = parsear_archivo_plantillas(_archivo_xlsx(wb))

        self.assertEqual(len(hojas), 2)
        self.assertEqual({h.nombre_hoja for h in hojas}, {"Hombres", "Mujeres"})


class ParsearArchivoBibliotecaTests(SimpleTestCase):
    def test_usa_la_primera_hoja(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])
        items, invalidas = parsear_archivo_biblioteca(_archivo_xlsx(wb))
        self.assertEqual(len(items), 1)
        self.assertEqual(invalidas, [])
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.LeerHojaBibliotecaTests importaciones.tests.ParsearArchivoPlantillasTests importaciones.tests.ParsearArchivoBibliotecaTests -v 2`
Expected: ERROR — `ImportError` (funciones no existen todavía).

- [ ] **Step 3: Implementar**

Agregar a `importaciones/parsing.py` (arriba del archivo, agregar `import openpyxl`; al final del archivo, agregar):

```python
def leer_hoja_biblioteca(ws):
    """Parsea una hoja del import de BIBLIOTECA: solo nombre + grupo
    muscular (opcional) + video (opcional), sin días/semanas/series."""
    encabezados = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    campos, _ = detectar_columnas(encabezados, ALIAS_BIBLIOTECA)

    if "nombre" not in campos:
        return [], []

    mapa_merges = _mapa_merges(ws)
    ncols = len(encabezados)
    items = []
    filas_invalidas = []

    for fila_idx in range(2, ws.max_row + 1):
        valores = [_valor_celda(ws, fila_idx, c, mapa_merges) for c in range(1, ncols + 1)]
        if _fila_vacia(valores):
            continue

        nombre = valores[campos["nombre"]]
        if not nombre or not str(nombre).strip():
            filas_invalidas.append(FilaInvalida(fila_idx, "Falta el nombre del ejercicio"))
            continue

        grupo_muscular = valores[campos["grupo_muscular"]] if "grupo_muscular" in campos else None
        url_video = valores[campos["url_video"]] if "url_video" in campos else None

        items.append({
            "nombre_original": str(nombre).strip(),
            "grupo_muscular_original": str(grupo_muscular).strip() if grupo_muscular else None,
            "url_video": str(url_video).strip() if url_video else "",
        })

    return items, filas_invalidas


def parsear_archivo_plantillas(archivo):
    """Abre `archivo` (un `UploadedFile` de Django) y devuelve una
    `HojaParseada` por cada hoja del workbook (decisión 7 del spec:
    multi-hoja -> multi-plantilla)."""
    wb = openpyxl.load_workbook(archivo, data_only=True)
    return [leer_hoja_plantilla(wb[nombre]) for nombre in wb.sheetnames]


def parsear_archivo_biblioteca(archivo):
    """El import de biblioteca usa solo la primera hoja del archivo."""
    wb = openpyxl.load_workbook(archivo, data_only=True)
    return leer_hoja_biblioteca(wb[wb.sheetnames[0]])
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.LeerHojaBibliotecaTests importaciones.tests.ParsearArchivoPlantillasTests importaciones.tests.ParsearArchivoBibliotecaTests -v 2`
Expected: PASS (5 tests). Correr también toda la clase de parsing (`importaciones.tests` filtrando las clases de Tasks 2-4) para confirmar que nada se rompió.

- [ ] **Step 5: Commit**

```bash
git add importaciones/parsing.py importaciones/tests.py
git commit -m "feat(importaciones): leer_hoja_biblioteca + parsear_archivo_plantillas/biblioteca"
```

---

### Task 5: `matching.py` — normalización de nombres, `rapidfuzz`, grupo muscular

**Files:**
- Create: `importaciones/matching.py`
- Modify: `requirements.txt` (agregar `rapidfuzz`)
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `normalizar_texto` (Task 2, importada desde `importaciones.parsing`), `Ejercicio` (`ejercicios/models.py`, ya existente).
- Produces: dataclass `MatchResultado`, constantes `UMBRAL_AMBIGUO=87`/`PISO_SCORE=60`, `resolver_nombre(nombre_normalizado, indice) -> MatchResultado`, `construir_indice_ejercicios(gimnasio) -> dict[str, Ejercicio]`, `ALIAS_GRUPO_MUSCULAR`, `resolver_grupo_muscular(texto) -> str | None`. Consumidas por `services.py` (Tasks 6-8).

- [ ] **Step 1: Agregar `rapidfuzz` a `requirements.txt`**

```
# Proyecto 2 (importador de planes desde Excel)
openpyxl==3.1.5
rapidfuzz==3.13.0
```

Run: `pip install rapidfuzz==3.13.0` (o la versión que quede fijada).

- [ ] **Step 2: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from ejercicios.models import Ejercicio
from importaciones.matching import (
    MatchResultado,
    construir_indice_ejercicios,
    resolver_grupo_muscular,
    resolver_nombre,
)


class ResolverNombreTests(SimpleTestCase):
    def setUp(self):
        # Índice armado a mano -- resolver_nombre es pura, no toca DB.
        self.indice = {"press de banca": "PRESS_ID", "sentadilla": "SENTADILLA_ID"}

    def test_match_exacto_tras_normalizar(self):
        resultado = resolver_nombre("press de banca", self.indice)
        self.assertEqual(resultado.tipo, "exacto")
        self.assertEqual(resultado.ejercicio, "PRESS_ID")

    def test_typo_da_ambiguo_con_candidato(self):
        resultado = resolver_nombre("sentadila", self.indice)  # falta una "l"
        self.assertEqual(resultado.tipo, "ambiguo")
        self.assertEqual(resultado.candidato, "SENTADILLA_ID")
        self.assertGreaterEqual(resultado.score, 60)

    def test_nombre_sin_relacion_da_nuevo(self):
        resultado = resolver_nombre("hip thrust", self.indice)
        self.assertEqual(resultado.tipo, "nuevo")
        self.assertIsNone(resultado.candidato)

    def test_indice_vacio_siempre_da_nuevo(self):
        resultado = resolver_nombre("cualquier cosa", {})
        self.assertEqual(resultado.tipo, "nuevo")


class ResolverGrupoMuscularTests(SimpleTestCase):
    def test_match_exacto_contra_choices(self):
        self.assertEqual(resolver_grupo_muscular("Pecho"), Ejercicio.GrupoMuscular.PECHO)

    def test_match_por_alias(self):
        self.assertEqual(resolver_grupo_muscular("Abdomen"), Ejercicio.GrupoMuscular.CORE)

    def test_sin_match_devuelve_none(self):
        self.assertIsNone(resolver_grupo_muscular("no existe esto"))


class ConstruirIndiceEjerciciosTests(TestCase):
    def test_indexa_por_nombre_normalizado_y_aisla_por_tenant(self):
        gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")
        ejercicio_a = Ejercicio.objects.create(
            gimnasio=gimnasio_a, nombre="Press de Banca",
            grupo_muscular=Ejercicio.GrupoMuscular.PECHO,
        )
        Ejercicio.objects.create(
            gimnasio=gimnasio_b, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        indice = construir_indice_ejercicios(gimnasio_a)
        self.assertEqual(indice, {"press de banca": ejercicio_a})
```

- [ ] **Step 3: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ResolverNombreTests importaciones.tests.ResolverGrupoMuscularTests importaciones.tests.ConstruirIndiceEjerciciosTests -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.matching'`.

- [ ] **Step 4: Implementar**

`importaciones/matching.py`:

```python
"""Matching difuso de nombres de ejercicio y de grupo muscular
(Proyecto 2). Ver spec §4-5 para el pipeline completo.

`resolver_nombre` y `resolver_grupo_muscular` son puras: no tocan la base.
Solo `construir_indice_ejercicios` toca DB (una única consulta, scopeada
por tenant)."""

from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from ejercicios.models import Ejercicio
from importaciones.parsing import normalizar_texto

UMBRAL_AMBIGUO = 87
PISO_SCORE = 60


@dataclass(frozen=True)
class MatchResultado:
    tipo: Literal["exacto", "ambiguo", "nuevo"]
    # Tipados como `Ejercicio | None` porque en producción SIEMPRE son
    # instancias reales (vienen del índice armado por
    # construir_indice_ejercicios). Los tests puros de más abajo pasan un
    # `indice` armado a mano con strings en vez de `Ejercicio` -- Python no
    # valida tipos de dataclass en runtime, así que eso no rompe nada, solo
    # hace que el type hint documente el contrato de producción, no el de
    # los tests unitarios de esta función.
    ejercicio: Ejercicio | None = None
    candidato: Ejercicio | None = None
    score: int | None = None


def resolver_nombre(nombre_normalizado, indice):
    """`indice` es un dict `{nombre_normalizado: Ejercicio}` ya armado
    (ver `construir_indice_ejercicios`) -- recibirlo como parámetro, en vez
    de tomar `gimnasio`, es lo que hace esta función pura y testeable con
    un dict a mano."""
    if nombre_normalizado in indice:
        return MatchResultado(tipo="exacto", ejercicio=indice[nombre_normalizado])

    if not indice:
        return MatchResultado(tipo="nuevo")

    candidatos = list(indice.keys())
    mejor = process.extractOne(nombre_normalizado, candidatos, scorer=fuzz.WRatio)
    if mejor is None:
        return MatchResultado(tipo="nuevo")

    nombre_candidato, score, _ = mejor
    score = int(score)
    if score < PISO_SCORE:
        return MatchResultado(tipo="nuevo")
    return MatchResultado(tipo="ambiguo", candidato=indice[nombre_candidato], score=score)


def construir_indice_ejercicios(gimnasio):
    """Única función de este módulo que toca DB."""
    ejercicios = Ejercicio.objects.for_gimnasio(gimnasio)
    return {normalizar_texto(e.nombre): e for e in ejercicios}


ALIAS_GRUPO_MUSCULAR = {
    "abdomen": Ejercicio.GrupoMuscular.CORE,
    "abs": Ejercicio.GrupoMuscular.CORE,
    "gluteos": Ejercicio.GrupoMuscular.PIERNAS,
    "glúteos": Ejercicio.GrupoMuscular.PIERNAS,
    "pierna": Ejercicio.GrupoMuscular.PIERNAS,
    "espalda alta": Ejercicio.GrupoMuscular.ESPALDA,
    "espalda baja": Ejercicio.GrupoMuscular.ESPALDA,
    "hombro": Ejercicio.GrupoMuscular.HOMBROS,
    "brazo": Ejercicio.GrupoMuscular.BRAZOS,
    "biceps": Ejercicio.GrupoMuscular.BRAZOS,
    "bíceps": Ejercicio.GrupoMuscular.BRAZOS,
    "triceps": Ejercicio.GrupoMuscular.BRAZOS,
    "tríceps": Ejercicio.GrupoMuscular.BRAZOS,
    "full body": Ejercicio.GrupoMuscular.CUERPO_COMPLETO,
    "cuerpo completo": Ejercicio.GrupoMuscular.CUERPO_COMPLETO,
}


def resolver_grupo_muscular(texto):
    """Normaliza y matchea contra las choices de `Ejercicio.GrupoMuscular`
    + el diccionario de alias de arriba. `None` si no hay match confiable
    -- nunca un default silencioso (decisión 10 del spec)."""
    normalizado = normalizar_texto(texto)
    for valor, _ in Ejercicio.GrupoMuscular.choices:
        if normalizado == valor or normalizado == normalizar_texto(
            dict(Ejercicio.GrupoMuscular.choices)[valor]
        ):
            return valor
    return ALIAS_GRUPO_MUSCULAR.get(normalizado)
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.ResolverNombreTests importaciones.tests.ResolverGrupoMuscularTests importaciones.tests.ConstruirIndiceEjerciciosTests -v 2`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add importaciones/matching.py importaciones/tests.py requirements.txt
git commit -m "feat(importaciones): matching difuso de ejercicios (rapidfuzz) + grupo muscular"
```

---

### Task 6: `services.py` — `previsualizar_importacion_plantillas`

**Files:**
- Create: `importaciones/services.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `parsear_archivo_plantillas` (Task 4), `normalizar_texto`, `construir_indice_ejercicios`, `resolver_nombre` (Tasks 2, 5), `Importacion` (Task 1).
- Produces: `ImportacionInvalida(Exception)`, `previsualizar_importacion_plantillas(*, gimnasio, archivo, usuario) -> Importacion`. La `Importacion` devuelta queda en `estado=EN_REVISION`; su `resultado` sigue el esquema del spec §2. Consumida por `views.py` (Task 10).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from importaciones.services import (
    ImportacionInvalida,
    previsualizar_importacion_plantillas,
)
from rutinas.models import RutinaPlantilla


class PrevisualizarImportacionPlantillasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

    def _archivo_dos_hojas(self):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hombres"
        ws1.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws1.append([1, "Press de banca", 4, "8-12"])
        ws1.append([1, "sentadila", 3, "10"])  # typo de un ejercicio ya cargado
        ws2 = wb.create_sheet("Mujeres")
        ws2.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws2.append([1, "Press de banca", 3, "10-12"])  # mismo ejercicio nuevo, otra hoja
        return _archivo_xlsx(wb)

    def test_crea_importacion_en_revision_sin_tocar_rutinaplantilla(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        self.assertEqual(importacion.tipo, Importacion.Tipo.PLANTILLAS)
        self.assertEqual(importacion.estado, Importacion.Estado.EN_REVISION)
        self.assertEqual(importacion.gimnasio, self.gimnasio)
        self.assertEqual(importacion.creado_por, self.usuario)
        self.assertEqual(RutinaPlantilla.objects.count(), 0)

    def test_resultado_tiene_una_entrada_por_hoja(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        nombres_hoja = {h["nombre_hoja"] for h in importacion.resultado["hojas"]}
        self.assertEqual(nombres_hoja, {"Hombres", "Mujeres"})

    def test_ejercicio_repetido_entre_hojas_se_resuelve_una_sola_vez(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        # "Press de banca" aparece en las dos hojas -> una sola entrada en ejercicios_distintos
        self.assertIn("press de banca", importacion.resultado["ejercicios_distintos"])
        entrada = importacion.resultado["ejercicios_distintos"]["press de banca"]
        self.assertEqual(entrada["tipo"], "nuevo")

    def test_typo_de_ejercicio_existente_da_ambiguo_con_candidato(self):
        importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=self._archivo_dos_hojas(), usuario=self.usuario,
        )
        entrada = importacion.resultado["ejercicios_distintos"]["sentadila"]
        self.assertEqual(entrada["tipo"], "ambiguo")
        self.assertEqual(entrada["candidato_id"], self.ejercicio_existente.pk)

    def test_archivo_no_valido_lanza_importacioninvalida(self):
        archivo_roto = SimpleUploadedFile("plan.xlsx", b"esto no es un xlsx")
        with self.assertRaises(ImportacionInvalida):
            previsualizar_importacion_plantillas(
                gimnasio=self.gimnasio, archivo=archivo_roto, usuario=self.usuario,
            )
```

Agregar `from django.contrib.auth.models import User` a los imports de `importaciones/tests.py` si no está ya.

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.PrevisualizarImportacionPlantillasTests -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.services'`.

- [ ] **Step 3: Implementar**

`importaciones/services.py`:

```python
"""Orquestación de las importaciones (Proyecto 2): arma el preview y, tras
la confirmación del staff, crea los registros reales. Siempre transaccional
-- mismo patrón que `RutinaAsignada.crear_desde_plantilla`
(`rutinas/models.py`) y `turnos/services.py`."""

import zipfile
from dataclasses import asdict

from openpyxl.utils.exceptions import InvalidFileException

from ejercicios.models import Ejercicio
from importaciones.matching import construir_indice_ejercicios, resolver_nombre
from importaciones.models import Importacion
from importaciones.parsing import normalizar_texto, parsear_archivo_plantillas

# Un .xlsx corrupto o que en realidad no es un .xlsx (otra extensión
# renombrada a mano) puede fallar de dos formas al abrirlo con openpyxl:
# InvalidFileException (formato no reconocido) o BadZipFile (un .xlsx es
# un zip; si el contenido no es un zip válido, falla ahí). Ambas se tratan
# igual: mensaje en español, no un 500.
ERRORES_ARCHIVO_INVALIDO = (InvalidFileException, KeyError, zipfile.BadZipFile)


class ImportacionInvalida(Exception):
    """Mensaje en español listo para messages.error() -- análoga a
    ErrorDeReserva en turnos/services.py."""


def previsualizar_importacion_plantillas(*, gimnasio, archivo, usuario):
    try:
        hojas = parsear_archivo_plantillas(archivo)
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx válido."
        )

    indice = construir_indice_ejercicios(gimnasio)

    nombres_distintos = {
        normalizar_texto(item.ejercicio_original)
        for hoja in hojas
        for item in hoja.items
    }

    ejercicios_distintos = {}
    for nombre_normalizado in nombres_distintos:
        resultado = resolver_nombre(nombre_normalizado, indice)
        if resultado.tipo == "exacto":
            ejercicios_distintos[nombre_normalizado] = {
                "tipo": "exacto",
                "ejercicio_id": resultado.ejercicio.pk,
                "nombre": resultado.ejercicio.nombre,
            }
        elif resultado.tipo == "ambiguo":
            ejercicios_distintos[nombre_normalizado] = {
                "tipo": "ambiguo",
                "candidato_id": resultado.candidato.pk,
                "candidato_nombre": resultado.candidato.nombre,
                "score": resultado.score,
            }
        else:
            ejercicios_distintos[nombre_normalizado] = {"tipo": "nuevo"}

    resultado_json = {
        "hojas": [
            {
                "nombre_hoja": hoja.nombre_hoja,
                "dias_por_semana": hoja.dias_por_semana,
                "items": [
                    {**asdict(item), "ejercicio_normalizado": normalizar_texto(item.ejercicio_original)}
                    for item in hoja.items
                ],
                "filas_invalidas": [asdict(f) for f in hoja.filas_invalidas],
            }
            for hoja in hojas
        ],
        "ejercicios_distintos": ejercicios_distintos,
        "advertencias_columnas": [],
    }

    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.PLANTILLAS,
        archivo=archivo,
        resultado=resultado_json,
        creado_por=usuario,
    )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.PrevisualizarImportacionPlantillasTests -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/services.py importaciones/tests.py
git commit -m "feat(importaciones): previsualizar_importacion_plantillas"
```

---

### Task 7: `services.py` — `confirmar_importacion_plantillas`

**Files:**
- Modify: `importaciones/services.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `Importacion`, `RutinaPlantilla`/`RutinaPlantillaItem` (`rutinas/models.py`), `Ejercicio` (`ejercicios/models.py`), `ImportacionInvalida` (Task 6).
- Produces: `confirmar_importacion_plantillas(*, importacion, gimnasio, decisiones) -> list[RutinaPlantilla]`. `decisiones` sigue el esquema `{"hojas": [{"incluir": bool, "objetivo": str, "nivel": str}, ...], "ejercicios": {nombre_normalizado: {"accion": "usar_existente"|"crear_nuevo", "ejercicio_id": int|None, "grupo_muscular": str|None}}}`. Consumida por `views.py` (Task 10).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from django.db import transaction

from importaciones.services import confirmar_importacion_plantillas
from rutinas.models import RutinaPlantillaItem


class ConfirmarImportacionPlantillasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        ws.append([1, "sentadila", 3, "10"])
        self.importacion = previsualizar_importacion_plantillas(
            gimnasio=self.gimnasio, archivo=_archivo_xlsx(wb), usuario=self.usuario,
        )

    def _decisiones_completas(self, accion_sentadila="usar_existente"):
        return {
            "hojas": [{"incluir": True, "objetivo": "Hipertrofia", "nivel": "principiante"}],
            "ejercicios": {
                "press de banca": {"accion": "crear_nuevo", "grupo_muscular": "pecho"},
                "sentadila": {
                    "accion": accion_sentadila,
                    "ejercicio_id": self.ejercicio_existente.pk if accion_sentadila == "usar_existente" else None,
                    "grupo_muscular": "piernas" if accion_sentadila == "crear_nuevo" else None,
                },
            },
        }

    def test_crea_una_plantilla_por_hoja_incluida(self):
        plantillas = confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.assertEqual(len(plantillas), 1)
        self.assertEqual(plantillas[0].nombre, "Hombres")
        self.assertEqual(plantillas[0].objetivo, "Hipertrofia")
        self.assertEqual(plantillas[0].nivel, "principiante")
        self.assertEqual(plantillas[0].dias_por_semana, 1)
        self.assertEqual(plantillas[0].items.count(), 2)

    def test_usar_existente_no_duplica_el_ejercicio(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(accion_sentadila="usar_existente"),
        )
        self.assertEqual(Ejercicio.objects.filter(nombre__iexact="sentadilla").count(), 1)
        item_sentadilla = RutinaPlantillaItem.objects.get(ejercicio=self.ejercicio_existente)
        self.assertEqual(item_sentadilla.series, 3)

    def test_crear_nuevo_crea_exactamente_un_ejercicio(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.assertEqual(
            Ejercicio.objects.filter(gimnasio=self.gimnasio, nombre="Press de banca").count(), 1
        )

    def test_marca_la_importacion_como_confirmada(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        self.importacion.refresh_from_db()
        self.assertEqual(self.importacion.estado, Importacion.Estado.CONFIRMADA)
        self.assertIsNotNone(self.importacion.confirmado_en)

    def test_confirmar_dos_veces_falla_sin_duplicar(self):
        confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio,
            decisiones=self._decisiones_completas(),
        )
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=self.gimnasio,
                decisiones=self._decisiones_completas(),
            )
        self.assertEqual(RutinaPlantilla.objects.count(), 1)

    def test_importacion_de_otro_gimnasio_falla(self):
        otro_gimnasio = Gimnasio.objects.create(nombre="Otro", slug="otro")
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_plantillas(
                importacion=self.importacion, gimnasio=otro_gimnasio,
                decisiones=self._decisiones_completas(),
            )

    def test_hoja_no_incluida_no_crea_plantilla(self):
        decisiones = self._decisiones_completas()
        decisiones["hojas"][0]["incluir"] = False
        plantillas = confirmar_importacion_plantillas(
            importacion=self.importacion, gimnasio=self.gimnasio, decisiones=decisiones,
        )
        self.assertEqual(plantillas, [])
        self.assertEqual(RutinaPlantilla.objects.count(), 0)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ConfirmarImportacionPlantillasTests -v 2`
Expected: ERROR — `ImportError: cannot import name 'confirmar_importacion_plantillas'`.

- [ ] **Step 3: Implementar**

Agregar a `importaciones/services.py` (imports adicionales: `from django.db import transaction`, `from django.utils import timezone`, `from rutinas.models import RutinaPlantilla, RutinaPlantillaItem`):

```python
def confirmar_importacion_plantillas(*, importacion, gimnasio, decisiones):
    if importacion.gimnasio_id != gimnasio.id:
        raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
    if importacion.estado != Importacion.Estado.EN_REVISION:
        raise ImportacionInvalida("Esta importación ya fue procesada.")

    resultado = importacion.resultado
    ejercicios_por_nombre = {}  # nombre_normalizado -> Ejercicio, resuelto una vez

    def _obtener_ejercicio(nombre_normalizado):
        if nombre_normalizado in ejercicios_por_nombre:
            return ejercicios_por_nombre[nombre_normalizado]
        decision = decisiones["ejercicios"][nombre_normalizado]
        if decision["accion"] == "usar_existente":
            ejercicio = Ejercicio.objects.get(
                pk=decision["ejercicio_id"], gimnasio=gimnasio,
            )
        else:
            info = resultado["ejercicios_distintos"][nombre_normalizado]
            nombre_original = next(
                item["ejercicio_original"]
                for hoja in resultado["hojas"]
                for item in hoja["items"]
                if item["ejercicio_normalizado"] == nombre_normalizado
            )
            ejercicio = Ejercicio.objects.create(
                gimnasio=gimnasio,
                nombre=nombre_original,
                grupo_muscular=decision["grupo_muscular"],
            )
        ejercicios_por_nombre[nombre_normalizado] = ejercicio
        return ejercicio

    plantillas_creadas = []
    with transaction.atomic():
        for hoja, decision_hoja in zip(resultado["hojas"], decisiones["hojas"]):
            if not decision_hoja["incluir"]:
                continue
            plantilla = RutinaPlantilla.objects.create(
                gimnasio=gimnasio,
                nombre=hoja["nombre_hoja"],
                objetivo=decision_hoja["objetivo"],
                nivel=decision_hoja["nivel"],
                dias_por_semana=hoja["dias_por_semana"],
            )
            RutinaPlantillaItem.objects.bulk_create([
                RutinaPlantillaItem(
                    rutina=plantilla,
                    ejercicio=_obtener_ejercicio(item["ejercicio_normalizado"]),
                    semana=item["semana"],
                    dia=item["dia"],
                    orden=item["orden"],
                    series=item["series"],
                    repeticiones=item["repeticiones"],
                    descanso=item["descanso"],
                    notas=item["notas"],
                )
                for item in hoja["items"]
            ])
            plantillas_creadas.append(plantilla)

        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.save(update_fields=["estado", "confirmado_en"])

    return plantillas_creadas
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.ConfirmarImportacionPlantillasTests -v 2`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/services.py importaciones/tests.py
git commit -m "feat(importaciones): confirmar_importacion_plantillas"
```

---

### Task 8: `services.py` — flujo de biblioteca (`previsualizar_importacion_biblioteca` + `confirmar_importacion_biblioteca`)

**Files:**
- Modify: `importaciones/services.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `parsear_archivo_biblioteca` (Task 4), `normalizar_texto`, `construir_indice_ejercicios`, `resolver_nombre`, `resolver_grupo_muscular` (Tasks 2, 5), `Importacion`, `Ejercicio`.
- Produces: `previsualizar_importacion_biblioteca(*, gimnasio, archivo, usuario) -> Importacion`, `confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones) -> list[Ejercicio]`. `decisiones` = `{"items": {nombre_normalizado: {"incluir": bool, "grupo_muscular": str}}}`. Consumidas por `views.py` (Task 11).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from importaciones.services import (
    confirmar_importacion_biblioteca,
    previsualizar_importacion_biblioteca,
)


class ImportacionBibliotecaTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.usuario = User.objects.create_user(username="staff", password="clave12345")
        self.ejercicio_existente = Ejercicio.objects.create(
            gimnasio=self.gimnasio, nombre="Sentadilla",
            grupo_muscular=Ejercicio.GrupoMuscular.PIERNAS,
        )

    def _archivo(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular", "Video"])
        ws.append(["Press de banca", "Pecho", "https://youtube.com/x"])
        ws.append(["sentadila", "Piernas", ""])  # typo de la ya existente
        return _archivo_xlsx(wb)

    def test_previsualizar_no_crea_ejercicios(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        self.assertEqual(importacion.tipo, Importacion.Tipo.BIBLIOTECA)
        self.assertEqual(Ejercicio.objects.count(), 1)  # solo la que ya existía

    def test_previsualizar_resuelve_grupo_muscular_automaticamente(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        item = next(i for i in importacion.resultado["items"] if i["nombre_original"] == "Press de banca")
        self.assertEqual(item["grupo_muscular_resuelto"], "pecho")

    def test_confirmar_crea_solo_los_ejercicios_nuevos(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        creados = confirmar_importacion_biblioteca(
            importacion=importacion, gimnasio=self.gimnasio,
            decisiones={"items": {
                "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
                "sentadila": {"incluir": False, "grupo_muscular": None},
            }},
        )
        self.assertEqual(len(creados), 1)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio).count(), 2)

    def test_confirmar_dos_veces_falla(self):
        importacion = previsualizar_importacion_biblioteca(
            gimnasio=self.gimnasio, archivo=self._archivo(), usuario=self.usuario,
        )
        decisiones = {"items": {
            "press de banca": {"incluir": True, "grupo_muscular": "pecho"},
            "sentadila": {"incluir": False, "grupo_muscular": None},
        }}
        confirmar_importacion_biblioteca(importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones)
        with self.assertRaises(ImportacionInvalida):
            confirmar_importacion_biblioteca(importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ImportacionBibliotecaTests -v 2`
Expected: ERROR — `ImportError` (funciones no existen todavía).

- [ ] **Step 3: Implementar**

Agregar a `importaciones/services.py`:

```python
from importaciones.matching import resolver_grupo_muscular
from importaciones.parsing import parsear_archivo_biblioteca


def previsualizar_importacion_biblioteca(*, gimnasio, archivo, usuario):
    try:
        items_parseados, filas_invalidas = parsear_archivo_biblioteca(archivo)
    except ERRORES_ARCHIVO_INVALIDO:
        raise ImportacionInvalida(
            "No se pudo leer el archivo. Verificá que sea un .xlsx válido."
        )

    indice = construir_indice_ejercicios(gimnasio)
    items = []
    for item in items_parseados:
        nombre_normalizado = normalizar_texto(item["nombre_original"])
        match = resolver_nombre(nombre_normalizado, indice)
        match_json = (
            {"tipo": "exacto", "ejercicio_id": match.ejercicio.pk}
            if match.tipo == "exacto"
            else {"tipo": "ambiguo", "candidato_id": match.candidato.pk, "score": match.score}
            if match.tipo == "ambiguo"
            else {"tipo": "nuevo"}
        )
        grupo_resuelto = (
            resolver_grupo_muscular(item["grupo_muscular_original"])
            if item["grupo_muscular_original"]
            else None
        )
        items.append({
            **item,
            "nombre_normalizado": nombre_normalizado,
            "grupo_muscular_resuelto": grupo_resuelto,
            "match": match_json,
        })

    resultado_json = {
        "items": items,
        "filas_invalidas": [asdict(f) for f in filas_invalidas],
    }

    return Importacion.objects.create(
        gimnasio=gimnasio,
        tipo=Importacion.Tipo.BIBLIOTECA,
        archivo=archivo,
        resultado=resultado_json,
        creado_por=usuario,
    )


def confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones):
    """Mismo patrón anti-TOCTOU que `confirmar_importacion_plantillas` (Task 7,
    fix post-review): el guard de tenant/estado corre DENTRO de la
    transacción, contra una fila re-leída con `select_for_update()` -- dos
    confirmaciones concurrentes de la misma Importacion no deben poder crear
    ejercicios duplicados. `grupo_muscular` se valida contra las choices
    reales antes de crear (Ejercicio.objects.create() no llama a
    full_clean(), así que un valor fuera de las 8 choices cerradas se
    guardaría en silencio sin esta validación)."""
    creados = []
    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        if importacion.gimnasio_id != gimnasio.id:
            raise ImportacionInvalida("Esta importación no pertenece a tu gimnasio.")
        if importacion.estado != Importacion.Estado.EN_REVISION:
            raise ImportacionInvalida("Esta importación ya fue procesada.")

        for item in importacion.resultado["items"]:
            decision = decisiones["items"][item["nombre_normalizado"]]
            if not decision["incluir"] or item["match"]["tipo"] == "exacto":
                # "exacto" ya existe en la biblioteca: no se recrea.
                continue
            grupo_muscular = decision["grupo_muscular"]
            if grupo_muscular not in Ejercicio.GrupoMuscular.values:
                raise ImportacionInvalida(
                    f"Grupo muscular inválido para '{item['nombre_original']}'."
                )
            ejercicio = Ejercicio.objects.create(
                gimnasio=gimnasio,
                nombre=item["nombre_original"],
                grupo_muscular=grupo_muscular,
                url_video=item["url_video"],
            )
            creados.append(ejercicio)

        importacion.estado = Importacion.Estado.CONFIRMADA
        importacion.confirmado_en = timezone.now()
        importacion.save(update_fields=["estado", "confirmado_en"])

    return creados
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.ImportacionBibliotecaTests -v 2`
Expected: PASS (4 tests). Correr también `python manage.py test importaciones -v 2` completo para confirmar que nada de lo anterior se rompió.

- [ ] **Step 5: Commit**

```bash
git add importaciones/services.py importaciones/tests.py
git commit -m "feat(importaciones): flujo de biblioteca de ejercicios"
```

---

### Task 9: `forms.py`

**Files:**
- Create: `importaciones/forms.py`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `RutinaPlantilla` (`nivel` choices), `Ejercicio` (`grupo_muscular` choices).
- Produces: `SubirPlantillasForm`, `SubirBibliotecaForm`, `HojaMetadataFormSet`, `ResolucionEjercicioFormSet`, `ResolucionGrupoMuscularFormSet`. Consumidos por `views.py` (Tasks 10-11).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
from importaciones.forms import (
    HojaMetadataFormSet,
    ResolucionEjercicioFormSet,
    ResolucionGrupoMuscularFormSet,
    SubirBibliotecaForm,
    SubirPlantillasForm,
)


class SubirPlantillasFormTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")

    def test_acepta_xlsx(self):
        wb = openpyxl.Workbook()
        archivo = _archivo_xlsx(wb)
        form = SubirPlantillasForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertTrue(form.is_valid(), form.errors)

    def test_rechaza_extension_invalida(self):
        archivo = SimpleUploadedFile("plan.csv", b"a,b,c", content_type="text/csv")
        form = SubirPlantillasForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)

    def test_biblioteca_tambien_valida_extension(self):
        archivo = SimpleUploadedFile("plan.txt", b"nada")
        form = SubirBibliotecaForm(data={}, files={"archivo": archivo}, gimnasio=self.gimnasio)
        self.assertFalse(form.is_valid())


class HojaMetadataFormSetTests(SimpleTestCase):
    def test_requiere_objetivo_y_nivel(self):
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "", "form-0-nivel": "",
        }
        formset = HojaMetadataFormSet(datos)
        self.assertFalse(formset.is_valid())

    def test_valido_con_todos_los_campos(self):
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
        }
        formset = HojaMetadataFormSet(datos)
        self.assertTrue(formset.is_valid(), formset.errors)


class ResolucionEjercicioFormSetTests(SimpleTestCase):
    def test_crear_nuevo_requiere_grupo_muscular(self):
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_normalizado": "hip thrust", "form-0-accion": "crear_nuevo",
            "form-0-grupo_muscular": "",
        }
        formset = ResolucionEjercicioFormSet(datos)
        self.assertFalse(formset.is_valid())

    def test_usar_existente_no_requiere_grupo_muscular(self):
        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_normalizado": "sentadila", "form-0-accion": "usar_existente",
            "form-0-ejercicio_existente_id": "7", "form-0-grupo_muscular": "",
        }
        formset = ResolucionEjercicioFormSet(datos)
        self.assertTrue(formset.is_valid(), formset.errors)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.SubirPlantillasFormTests importaciones.tests.HojaMetadataFormSetTests importaciones.tests.ResolucionEjercicioFormSetTests -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.forms'`.

- [ ] **Step 3: Implementar**

`importaciones/forms.py`:

```python
"""Forms del importador (Proyecto 2). Los de subida son `forms.Form`
planos (no `ModelForm`, no hay modelo destino directo) que aceptan
`gimnasio` en `__init__` solo porque `TenantScopedMixin.get_form_kwargs()`
siempre lo inyecta -- mismo patrón que `AsignarRutinaForm` en
`rutinas/forms.py`. Los formsets de preview usan `forms.formset_factory`
(mecanismo idiomático de Django para N repeticiones de un sub-form)."""

from django import forms
from django.core.validators import FileExtensionValidator

from ejercicios.models import Ejercicio
from rutinas.models import RutinaPlantilla


class SubirArchivoForm(forms.Form):
    archivo = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])]
    )

    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.gimnasio = gimnasio


class SubirPlantillasForm(SubirArchivoForm):
    pass


class SubirBibliotecaForm(SubirArchivoForm):
    pass


class HojaMetadataForm(forms.Form):
    nombre_hoja = forms.CharField(widget=forms.HiddenInput)
    incluir = forms.BooleanField(required=False, initial=True)
    objetivo = forms.CharField(max_length=120)
    nivel = forms.ChoiceField(choices=RutinaPlantilla.Nivel.choices)


HojaMetadataFormSet = forms.formset_factory(HojaMetadataForm, extra=0)


class ResolucionEjercicioForm(forms.Form):
    nombre_normalizado = forms.CharField(widget=forms.HiddenInput)
    accion = forms.ChoiceField(choices=[
        ("usar_existente", "Usar existente"),
        ("crear_nuevo", "Crear como nuevo"),
    ])
    ejercicio_existente_id = forms.IntegerField(required=False)
    grupo_muscular = forms.ChoiceField(
        choices=Ejercicio.GrupoMuscular.choices, required=False,
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("accion") == "crear_nuevo" and not cleaned.get("grupo_muscular"):
            self.add_error(
                "grupo_muscular", "Elegí un grupo muscular para el ejercicio nuevo."
            )
        return cleaned


ResolucionEjercicioFormSet = forms.formset_factory(ResolucionEjercicioForm, extra=0)


class ResolucionGrupoMuscularForm(forms.Form):
    valor_original = forms.CharField(widget=forms.HiddenInput)
    grupo_muscular = forms.ChoiceField(choices=Ejercicio.GrupoMuscular.choices)


ResolucionGrupoMuscularFormSet = forms.formset_factory(ResolucionGrupoMuscularForm, extra=0)
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.SubirPlantillasFormTests importaciones.tests.HojaMetadataFormSetTests importaciones.tests.ResolucionEjercicioFormSetTests -v 2`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/forms.py importaciones/tests.py
git commit -m "feat(importaciones): forms de subida y formsets de resolución del preview"
```

---

### Task 10: Vistas, urls y templates — flujo de plantillas

**Files:**
- Create: `importaciones/views.py`, `importaciones/urls.py`, `templates/importaciones/plantillas_subir.html`, `templates/importaciones/plantillas_preview.html`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `SubirPlantillasForm`, `HojaMetadataFormSet`, `ResolucionEjercicioFormSet` (Task 9), `previsualizar_importacion_plantillas`, `confirmar_importacion_plantillas`, `ImportacionInvalida` (Tasks 6-7), `Importacion` (Task 1), `StaffRequiredMixin` (`tenants/mixins.py`), `TenantScopedMixin` (`core/mixins.py`).
- Produces: URLs con namespace `importaciones` (`plantillas_subir`, `plantillas_preview`, `plantillas_descartar`). Este proyecto todavía no las registra en `config/urls.py` — eso es Task 12 (mismo criterio que `rutinas/urls.py`, que documenta explícitamente que el registro en el router raíz queda para quien integre todas las apps).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py` (imports nuevos: `from django.urls import reverse`, `from tenants.models import Perfil`):

```python
from django.urls import reverse

from tenants.models import Perfil


class ImportacionPlantillasViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.gimnasio_b = Gimnasio.objects.create(nombre="Gym B", slug="gym-b")

        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF)

        self.staff_b = User.objects.create_user(username="staff_b", password="clave12345")
        Perfil.objects.create(usuario=self.staff_b, gimnasio=self.gimnasio_b, rol=Perfil.Rol.STAFF)

        self.usuario_alumno = User.objects.create_user(username="usuario_alumno", password="clave12345")
        Perfil.objects.create(usuario=self.usuario_alumno, gimnasio=self.gimnasio_a, rol=Perfil.Rol.ALUMNO)

    def _archivo_valido(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Hombres"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        ws.append([1, "Press de banca", 4, "8-12"])
        return _archivo_xlsx(wb)

    def test_anonimo_redirige_a_login(self):
        response = self.client.get(reverse("importaciones:plantillas_subir"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_alumno_recibe_403(self):
        self.client.login(username="usuario_alumno", password="clave12345")
        response = self.client.get(reverse("importaciones:plantillas_subir"))
        self.assertEqual(response.status_code, 403)

    def test_subir_archivo_invalido_no_crea_importacion(self):
        self.client.login(username="staff_a", password="clave12345")
        archivo = SimpleUploadedFile("plan.txt", b"nada")
        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": archivo},
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Importacion.objects.count(), 0)

    def test_preview_de_otro_gimnasio_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_b, tipo=Importacion.Tipo.PLANTILLAS, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_de_importacion_ya_confirmada_da_404(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS,
            estado=Importacion.Estado.CONFIRMADA, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_flujo_completo_subir_preview_confirmar(self):
        self.client.login(username="staff_a", password="clave12345")

        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": self._archivo_valido()},
        )
        self.assertEqual(response.status_code, 302)
        importacion = Importacion.objects.get()
        self.assertRedirects(
            response, reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hombres")
        self.assertContains(response, "Press de banca")

        datos_confirmacion = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Hombres", "form-0-incluir": "on",
            "form-0-objetivo": "Hipertrofia", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": "1", "ejercicios-INITIAL_FORMS": "1",
            "ejercicios-0-nombre_normalizado": "press de banca",
            "ejercicios-0-accion": "crear_nuevo",
            "ejercicios-0-grupo_muscular": "pecho",
        }
        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos_confirmacion,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RutinaPlantilla.objects.count(), 1)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.CONFIRMADA)

        # Reabrir el preview de una importación ya confirmada da 404.
        response = self.client.get(
            reverse("importaciones:plantillas_preview", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_descartar_marca_la_importacion_como_descartada(self):
        importacion = Importacion.objects.create(
            gimnasio=self.gimnasio_a, tipo=Importacion.Tipo.PLANTILLAS, resultado={"hojas": [], "ejercicios_distintos": {}},
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:plantillas_descartar", args=[importacion.pk])
        )
        self.assertEqual(response.status_code, 302)
        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, Importacion.Estado.DESCARTADA)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ImportacionPlantillasViewsTests -v 2`
Expected: ERROR — `ModuleNotFoundError: No module named 'importaciones.urls'` (ni vistas ni URLs existen todavía).

- [ ] **Step 3: Implementar**

`importaciones/views.py`:

```python
"""Vistas de gestión del importador (Proyecto 2). Mismo patrón que
`rutinas/views.py`: StaffRequiredMixin + TenantScopedMixin, vistas finas
que delegan toda la lógica a `services.py`."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, View

from core.mixins import TenantScopedMixin
from importaciones.forms import HojaMetadataFormSet, ResolucionEjercicioFormSet, SubirPlantillasForm
from importaciones.models import Importacion
from importaciones.services import (
    ImportacionInvalida,
    confirmar_importacion_plantillas,
    previsualizar_importacion_plantillas,
)
from tenants.mixins import StaffRequiredMixin


class SubirPlantillasView(StaffRequiredMixin, TenantScopedMixin, FormView):
    form_class = SubirPlantillasForm
    template_name = "importaciones/plantillas_subir.html"

    def form_valid(self, form):
        try:
            importacion = previsualizar_importacion_plantillas(
                gimnasio=self.gimnasio,
                archivo=form.cleaned_data["archivo"],
                usuario=self.request.user,
            )
        except ImportacionInvalida as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("importaciones:plantillas_preview", pk=importacion.pk)


class PreviewPlantillasView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/plantillas_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.PLANTILLAS,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _formsets_iniciales(self, importacion):
        resultado = importacion.resultado
        hojas_initial = [
            {"nombre_hoja": h["nombre_hoja"], "incluir": True, "objetivo": "", "nivel": ""}
            for h in resultado["hojas"]
        ]
        ejercicios_initial = [
            {
                "nombre_normalizado": nombre,
                "accion": "usar_existente" if info["tipo"] in ("exacto", "ambiguo") else "crear_nuevo",
                "ejercicio_existente_id": info.get("ejercicio_id") or info.get("candidato_id"),
            }
            for nombre, info in resultado["ejercicios_distintos"].items()
            if info["tipo"] != "exacto"  # los exactos no requieren decisión del staff
        ]
        hoja_formset = HojaMetadataFormSet(initial=hojas_initial, prefix="form")
        ejercicio_formset = ResolucionEjercicioFormSet(initial=ejercicios_initial, prefix="ejercicios")
        return hoja_formset, ejercicio_formset

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        hoja_formset, ejercicio_formset = self._formsets_iniciales(importacion)
        return self.render(request, importacion, hoja_formset, ejercicio_formset)

    def render(self, request, importacion, hoja_formset, ejercicio_formset):
        from django.shortcuts import render
        # `hoja_formset.forms` preserva el orden de `hojas_initial`, que a
        # su vez preserva el orden de `resultado["hojas"]` -- zippearlos es
        # lo que le permite al template mostrar cada form junto a SU hoja
        # (indexar a mano, ej. `hoja_formset.forms.0` dentro de un loop,
        # siempre traería el primer form sin importar qué hoja se está
        # renderizando).
        return render(request, self.template_name, {
            "importacion": importacion,
            "hojas_con_form": list(zip(importacion.resultado["hojas"], hoja_formset.forms)),
            "hoja_formset": hoja_formset,
            "ejercicio_formset": ejercicio_formset,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        hoja_formset = HojaMetadataFormSet(request.POST, prefix="form")
        ejercicio_formset = ResolucionEjercicioFormSet(request.POST, prefix="ejercicios")

        if not (hoja_formset.is_valid() and ejercicio_formset.is_valid()):
            return self.render(request, importacion, hoja_formset, ejercicio_formset)

        decisiones = {
            "hojas": [
                {"incluir": f["incluir"], "objetivo": f["objetivo"], "nivel": f["nivel"]}
                for f in hoja_formset.cleaned_data
            ],
            "ejercicios": {
                **{
                    nombre: {"accion": "usar_existente", "ejercicio_id": info["ejercicio_id"]}
                    for nombre, info in importacion.resultado["ejercicios_distintos"].items()
                    if info["tipo"] == "exacto"
                },
                **{
                    f["nombre_normalizado"]: {
                        "accion": f["accion"],
                        "ejercicio_id": f["ejercicio_existente_id"],
                        "grupo_muscular": f["grupo_muscular"],
                    }
                    for f in ejercicio_formset.cleaned_data
                },
            },
        }

        try:
            plantillas = confirmar_importacion_plantillas(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self.render(request, importacion, hoja_formset, ejercicio_formset)

        messages.success(request, f"Se crearon {len(plantillas)} plantilla(s).")
        return redirect("rutinas:plantilla_listado")


class DescartarImportacionView(StaffRequiredMixin, TenantScopedMixin, View):
    def post(self, request, *args, **kwargs):
        importacion = get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=kwargs["pk"], estado=Importacion.Estado.EN_REVISION,
        )
        importacion.estado = Importacion.Estado.DESCARTADA
        importacion.save(update_fields=["estado"])
        messages.success(request, "Importación descartada.")
        return redirect("importaciones:plantillas_subir")
```

`importaciones/urls.py`:

```python
"""URLs del importador (Proyecto 2). No se incluye acá en `config/urls.py`
-- eso queda para Task 12, mismo criterio que documenta `rutinas/urls.py`."""

from django.urls import path

from importaciones.views import (
    DescartarImportacionView,
    PreviewPlantillasView,
    SubirPlantillasView,
)

app_name = "importaciones"

urlpatterns = [
    path("plantillas/subir/", SubirPlantillasView.as_view(), name="plantillas_subir"),
    path("plantillas/<int:pk>/preview/", PreviewPlantillasView.as_view(), name="plantillas_preview"),
    path("plantillas/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="plantillas_descartar"),
]
```

`templates/importaciones/plantillas_subir.html`:

```html
{% extends 'base.html' %}
{% block title %}Importar plantillas · App Gimnasios{% endblock %}

{% block content %}
<div class="contenido--ancho">
  <h1>Importar plantillas de rutina</h1>
  <div class="tarjeta">
    <p class="texto-suave">
      Subí un archivo .xlsx con tus planes de entrenamiento. Cada hoja del
      archivo se convierte en una plantilla reutilizable — vas a poder
      revisar todo antes de confirmar.
    </p>
    <form method="post" enctype="multipart/form-data" hx-boost="false">
      {% csrf_token %}
      {{ form.as_p }}
      <button type="submit" class="boton">Subir y previsualizar</button>
    </form>
  </div>
</div>
{% endblock %}
```

`templates/importaciones/plantillas_preview.html`:

```html
{% extends 'base.html' %}
{% block title %}Revisar importación · App Gimnasios{% endblock %}
{% block main_class %}contenido--ancho{% endblock %}

{% block content %}
<div class="contenido--ancho">
  <h1>Revisar antes de confirmar</h1>
  <form method="post" hx-boost="false">
    {% csrf_token %}
    {{ hoja_formset.management_form }}
    {% for hoja, hoja_form in hojas_con_form %}
      <div class="tarjeta">
        <h2>{{ hoja.nombre_hoja }}</h2>
        <p class="texto-suave">{{ hoja.items|length }} ejercicios detectados</p>
        {{ hoja_form }}
        {% if hoja.filas_invalidas %}
          <table class="tabla">
            <thead><tr><th>Fila</th><th>Motivo</th></tr></thead>
            <tbody>
              {% for fila in hoja.filas_invalidas %}
                <tr><td>{{ fila.fila_excel }}</td><td>{{ fila.motivo }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
    {% endfor %}

    {{ ejercicio_formset.management_form }}
    {% if ejercicio_formset.forms %}
      <div class="tarjeta">
        <h2>Ejercicios a resolver</h2>
        {% for f in ejercicio_formset %}
          <p>{{ f.nombre_normalizado.value }}</p>
          {{ f }}
        {% endfor %}
      </div>
    {% endif %}

    <button type="submit" class="boton">Confirmar importación</button>
  </form>
  <form method="post" action="{% url 'importaciones:plantillas_descartar' importacion.pk %}" hx-boost="false">
    {% csrf_token %}
    <button type="submit" class="boton-peligro">Descartar</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones.tests.ImportacionPlantillasViewsTests -v 2`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add importaciones/views.py importaciones/urls.py templates/importaciones/ importaciones/tests.py
git commit -m "feat(importaciones): vistas, urls y templates del flujo de plantillas"
```

---

### Task 11: Vistas, urls y templates — flujo de biblioteca

**Files:**
- Modify: `importaciones/views.py`, `importaciones/urls.py`
- Create: `templates/importaciones/biblioteca_subir.html`, `templates/importaciones/biblioteca_preview.html`
- Test: `importaciones/tests.py`

**Interfaces:**
- Consumes: `SubirBibliotecaForm`, `ResolucionGrupoMuscularFormSet` (Task 9), `previsualizar_importacion_biblioteca`, `confirmar_importacion_biblioteca` (Task 8).
- Produces: `biblioteca_subir`, `biblioteca_preview`, `biblioteca_descartar` en `importaciones:urls`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `importaciones/tests.py`:

```python
class ImportacionBibliotecaViewsTests(TestCase):
    def setUp(self):
        self.gimnasio_a = Gimnasio.objects.create(nombre="Gym A", slug="gym-a")
        self.staff_a = User.objects.create_user(username="staff_a", password="clave12345")
        Perfil.objects.create(usuario=self.staff_a, gimnasio=self.gimnasio_a, rol=Perfil.Rol.STAFF)

    def test_flujo_completo_subir_preview_confirmar(self):
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre", "Grupo Muscular"])
        ws.append(["Press de banca", "Pecho"])

        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        self.assertEqual(response.status_code, 302)
        importacion = Importacion.objects.get()

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Press de banca")

        datos = {
            "form-TOTAL_FORMS": "0", "form-INITIAL_FORMS": "0",
            # "Press de banca" resolvió grupo_muscular automáticamente ("pecho")
            # y no necesita entrada en el formset de resolución manual.
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Press de banca").count(), 1)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ImportacionBibliotecaViewsTests -v 2`
Expected: ERROR — `NoReverseMatch` (`importaciones:biblioteca_subir` no existe todavía).

- [ ] **Step 3: Implementar**

Agregar a `importaciones/views.py` (imports adicionales: `SubirBibliotecaForm`, `ResolucionGrupoMuscularFormSet`, `previsualizar_importacion_biblioteca`, `confirmar_importacion_biblioteca`):

```python
from importaciones.forms import ResolucionGrupoMuscularFormSet, SubirBibliotecaForm
from importaciones.services import confirmar_importacion_biblioteca, previsualizar_importacion_biblioteca


class SubirBibliotecaView(StaffRequiredMixin, TenantScopedMixin, FormView):
    form_class = SubirBibliotecaForm
    template_name = "importaciones/biblioteca_subir.html"

    def form_valid(self, form):
        try:
            importacion = previsualizar_importacion_biblioteca(
                gimnasio=self.gimnasio,
                archivo=form.cleaned_data["archivo"],
                usuario=self.request.user,
            )
        except ImportacionInvalida as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("importaciones:biblioteca_preview", pk=importacion.pk)


class PreviewBibliotecaView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/biblioteca_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.BIBLIOTECA,
            estado=Importacion.Estado.EN_REVISION,
        )

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        pendientes_initial = [
            {"valor_original": item["nombre_normalizado"]}
            for item in importacion.resultado["items"]
            if item["match"]["tipo"] != "exacto" and not item["grupo_muscular_resuelto"]
        ]
        formset = ResolucionGrupoMuscularFormSet(initial=pendientes_initial, prefix="form")
        return self._render(request, importacion, formset)

    def _render(self, request, importacion, formset):
        from django.shortcuts import render
        return render(request, self.template_name, {
            "importacion": importacion, "formset": formset,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        formset = ResolucionGrupoMuscularFormSet(request.POST, prefix="form")
        if not formset.is_valid():
            return self._render(request, importacion, formset)

        resueltos_a_mano = {
            f["valor_original"]: f["grupo_muscular"] for f in formset.cleaned_data
        }
        decisiones = {"items": {
            item["nombre_normalizado"]: {
                "incluir": item["match"]["tipo"] != "exacto",
                "grupo_muscular": (
                    item["grupo_muscular_resuelto"]
                    or resueltos_a_mano.get(item["nombre_normalizado"])
                ),
            }
            for item in importacion.resultado["items"]
        }}

        try:
            creados = confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self._render(request, importacion, formset)

        messages.success(request, f"Se crearon {len(creados)} ejercicio(s).")
        return redirect("ejercicios:listado")
```

Agregar a `DescartarImportacionView.post` la posibilidad de manejar también importaciones de biblioteca (ya funciona sin cambios: filtra por `pk` + `estado`, no por `tipo`) — pero el `redirect` final asume siempre `plantillas_subir`. Ajustar:

```python
class DescartarImportacionView(StaffRequiredMixin, TenantScopedMixin, View):
    def post(self, request, *args, **kwargs):
        importacion = get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=kwargs["pk"], estado=Importacion.Estado.EN_REVISION,
        )
        importacion.estado = Importacion.Estado.DESCARTADA
        importacion.save(update_fields=["estado"])
        messages.success(request, "Importación descartada.")
        if importacion.tipo == Importacion.Tipo.BIBLIOTECA:
            return redirect("importaciones:biblioteca_subir")
        return redirect("importaciones:plantillas_subir")
```

`importaciones/urls.py` — agregar al `urlpatterns`:

```python
    path("biblioteca/subir/", SubirBibliotecaView.as_view(), name="biblioteca_subir"),
    path("biblioteca/<int:pk>/preview/", PreviewBibliotecaView.as_view(), name="biblioteca_preview"),
    path("biblioteca/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="biblioteca_descartar"),
```

(y el import correspondiente de `SubirBibliotecaView`/`PreviewBibliotecaView` arriba del archivo).

`templates/importaciones/biblioteca_subir.html`:

```html
{% extends 'base.html' %}
{% block title %}Importar ejercicios · App Gimnasios{% endblock %}

{% block content %}
<div class="contenido--ancho">
  <h1>Importar biblioteca de ejercicios</h1>
  <div class="tarjeta">
    <p class="texto-suave">
      Subí un archivo .xlsx con tu lista de ejercicios (nombre, grupo
      muscular y video son opcionales salvo el nombre).
    </p>
    <form method="post" enctype="multipart/form-data" hx-boost="false">
      {% csrf_token %}
      {{ form.as_p }}
      <button type="submit" class="boton">Subir y previsualizar</button>
    </form>
  </div>
</div>
{% endblock %}
```

`templates/importaciones/biblioteca_preview.html`:

```html
{% extends 'base.html' %}
{% block title %}Revisar biblioteca · App Gimnasios{% endblock %}
{% block main_class %}contenido--ancho{% endblock %}

{% block content %}
<div class="contenido--ancho">
  <h1>Revisar antes de confirmar</h1>
  <form method="post" hx-boost="false">
    {% csrf_token %}
    <table class="tabla">
      <thead><tr><th>Nombre</th><th>Grupo muscular</th><th>Estado</th></tr></thead>
      <tbody>
        {% for item in importacion.resultado.items %}
          <tr>
            <td>{{ item.nombre_original }}</td>
            <td>{{ item.grupo_muscular_resuelto|default:"—" }}</td>
            <td>
              {% if item.match.tipo == "exacto" %}Ya existe{% else %}Nuevo{% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    {{ formset.management_form }}
    {% for f in formset %}
      <p>{{ f.valor_original.value }}</p>
      {{ f }}
    {% endfor %}
    <button type="submit" class="boton">Confirmar importación</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones -v 2`
Expected: PASS (toda la suite de `importaciones` hasta acá).

- [ ] **Step 5: Commit**

```bash
git add importaciones/views.py importaciones/urls.py templates/importaciones/ importaciones/tests.py
git commit -m "feat(importaciones): vistas, urls y templates del flujo de biblioteca"
```

---

### Task 12: Wiring final, nav, y verificación de extremo a extremo

**Files:**
- Modify: `config/urls.py`, `templates/base.html`
- Test: `importaciones/tests.py`

**Interfaces:** ninguna nueva — conecta lo construido en las tareas anteriores al resto de la app.

- [ ] **Step 1: Registrar las URLs en el router raíz**

`config/urls.py` — agregar el include, después de `'calendario/'`:

```python
    path("calendario/", include("calendario.urls")),
    path("importaciones/", include("importaciones.urls")),
```

- [ ] **Step 2: Agregar los links de navegación**

`templates/base.html` — agregar dentro de `<nav class="nav-staff" ...>`, después del link de "Rutinas":

```html
      <a href="{% url 'rutinas:plantilla_listado' %}">Rutinas</a>
      <a href="{% url 'importaciones:plantillas_subir' %}">Importar rutinas</a>
      <a href="{% url 'importaciones:biblioteca_subir' %}">Importar ejercicios</a>
```

- [ ] **Step 3: Escribir el test de regresión de `DATA_UPLOAD_MAX_NUMBER_FIELDS`**

Agregar a `importaciones/tests.py`:

```python
class RegresionCamposDelPostTests(TestCase):
    """El confirm POST manda solo decisiones (objetivo/nivel por hoja +
    resolución por ejercicio distinto), nunca el dataset entero -- por
    diseño (spec §2) no debería acercarse jamás al límite default de
    Django (1000 campos por POST), sin importar el tamaño de la planilla."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_hoja_de_500_filas_no_rompe_el_confirm_post(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Full body"
        ws.append(["Dia", "Ejercicio", "Series", "Repeticiones"])
        for i in range(500):
            ws.append([1, f"Ejercicio {i}", 3, "10"])

        self.client.login(username="staff", password="clave12345")
        response = self.client.post(
            reverse("importaciones:plantillas_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertEqual(len(importacion.resultado["ejercicios_distintos"]), 500)

        datos = {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-0-nombre_hoja": "Full body", "form-0-incluir": "on",
            "form-0-objetivo": "General", "form-0-nivel": "principiante",
            "ejercicios-TOTAL_FORMS": "500", "ejercicios-INITIAL_FORMS": "500",
        }
        for i, nombre in enumerate(importacion.resultado["ejercicios_distintos"]):
            datos[f"ejercicios-{i}-nombre_normalizado"] = nombre
            datos[f"ejercicios-{i}-accion"] = "crear_nuevo"
            datos[f"ejercicios-{i}-grupo_muscular"] = "cuerpo_completo"

        response = self.client.post(
            reverse("importaciones:plantillas_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RutinaPlantilla.objects.get().items.count(), 500)
```

- [ ] **Step 4: Correr la suite completa de `importaciones`**

Run: `python manage.py test importaciones -v 2`
Expected: PASS (toda la suite, incluido el test de regresión nuevo).

- [ ] **Step 5: Correr la suite completa del proyecto**

Run: `python manage.py test -v 1`
Expected: 0 regresiones sobre las 312 pruebas existentes (total esperado: 312 + las nuevas de `importaciones`).

- [ ] **Step 6: Verificar migraciones**

Run: `python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 7: Commit**

```bash
git add config/urls.py templates/base.html importaciones/tests.py
git commit -m "feat(importaciones): wiring final (urls, nav) + test de regresión de campos del POST"
```

- [ ] **Step 8: Chequeo manual**

Levantar `python manage.py runserver`, loguearse como staff, entrar a "Importar rutinas", subir un `.xlsx` de prueba con 2 hojas (una con datos limpios, otra con una fila con "series" en texto y una celda de semana combinada verticalmente), confirmar que el preview muestra las hojas + la fila inválida + los ejercicios a resolver, completar objetivo/nivel/grupo muscular, confirmar, y verificar en `rutinas:plantilla_listado` que las plantillas quedaron creadas con los items/semanas correctos. Repetir con "Importar ejercicios" y un archivo simple de biblioteca.

---

### Task 13: Biblioteca — resolución de grupo muscular vía un único campo JSON (fix de escalabilidad del confirm POST)

**Contexto (no estaba en el plan original — surge de la Tarea 12):** el test de regresión de la Tarea 12 (`RegresionCamposDelPostTests`) probó, y el dueño del producto confirmó, que una planilla de **plantillas** nunca va a tener más de ~300 ejercicios distintos (el invariante "el confirm POST nunca se acerca al límite de campos" queda aceptado tal cual para ese flujo, documentado en `ISSUES.md` `[2026-07-28]`). Pero una planilla de **biblioteca** sí puede traer 1000+ ejercicios — y a diferencia de plantillas, en una carga inicial (biblioteca vacía) la mayoría de esos ejercicios no tiene forma de auto-resolver `grupo_muscular` (no hay alias de nombre que lo infiera), así que terminan en el formset de resolución manual. Con el diseño actual (`ResolucionGrupoMuscularFormSet`, 2 campos de POST por ejercicio pendiente: `valor_original` + `grupo_muscular`), el techo real es ~498 ejercicios pendientes antes de superar `DATA_UPLOAD_MAX_NUMBER_FIELDS=1000` y recibir un `TooManyFieldsSent` (HTTP 400) crudo de Django en vez de un error en español. El dueño del producto pidió específicamente la opción de mayor alcance: en vez de tocar el límite global (afecta TODAS las vistas del proyecto) o construir un flujo de reintento por lotes, **rediseñar el POST de confirmación de biblioteca para mandar las resoluciones como un único campo JSON** en vez de N pares de campos — así el conteo de campos del POST queda constante sin importar cuántos ejercicios pendientes haya (el límite relevante pasa a ser `DATA_UPLOAD_MAX_MEMORY_SIZE`, default 2.5MB, que un JSON de miles de entradas cortas no se acerca a rozar).

**Alcance:** solo el flujo de **biblioteca** (`PreviewBibliotecaView`, `templates/importaciones/biblioteca_preview.html`, `ResolucionGrupoMuscularForm`/`FormSet` en `forms.py`). El flujo de plantillas (`ResolucionEjercicioFormSet`, `HojaMetadataFormSet`) NO se toca — ese invariante quedó aceptado tal cual está.

**Files:**
- Modify: `importaciones/forms.py`, `importaciones/views.py`, `templates/importaciones/biblioteca_preview.html`, `importaciones/tests.py`, `ISSUES.md`

**Interfaces:**
- `confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones)` (Task 8, `services.py`) **no cambia** — sigue esperando `decisiones = {"items": {nombre_normalizado: {"incluir": bool, "grupo_muscular": str|None}}}` para TODOS los items de `importacion.resultado["items"]`. Esta tarea solo cambia CÓMO `PreviewBibliotecaView.post()` arma ese dict a partir del POST (antes: parseando un formset; ahora: parseando un único campo JSON), no el contrato de `services.py`.
- `ResolucionGrupoMuscularForm`/`ResolucionGrupoMuscularFormSet` quedan sin usar en ningún otro lugar del repo (verificado: `grep -rn "ResolucionGrupoMuscular" --include="*.py" --include="*.html" .` solo los encuentra en `forms.py` y `views.py`, ningún test los ejercita directamente) — se **eliminan** de `forms.py`, no se dejan como código muerto.

- [ ] **Step 1: Escribir los tests que fallan**

En `importaciones/tests.py`, actualizar `ImportacionBibliotecaViewsTests` (el formato de POST cambia — estos dos tests existentes van a fallar hasta el Step 3):

```python
    def test_flujo_completo_subir_preview_confirmar(self):
        # ... (setUp y subida sin cambios) ...
        datos = {"resoluciones": "{}"}  # "Press de banca" se resolvió solo
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Press de banca").count(), 1)
        # ... (resto sin cambios) ...

    def test_flujo_con_resolucion_manual_de_grupo_muscular(self):
        # ... (setUp y subida sin cambios) ...
        datos = {"resoluciones": json.dumps({"hip thrust": "piernas"})}
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(nombre="Hip thrust").count(), 1)
        self.assertEqual(
            Ejercicio.objects.get(nombre="Hip thrust").grupo_muscular, "piernas"
        )
```

(Agregar `import json` al principio de `importaciones/tests.py` si no está ya.)

Agregar un test nuevo, de resolución faltante (reemplaza la validación "required" que antes daba gratis el formset):

```python
    def test_falta_resolver_un_pendiente_no_confirma(self):
        self.client.login(username="staff_a", password="clave12345")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Hip thrust"])
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()

        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "{}"},  # "hip thrust" queda sin resolver
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Ejercicio.objects.count(), 0)
```

Y el test de regresión de escalabilidad (el objetivo real de esta tarea — prueba que el invariante que la Tarea 12 tuvo que aceptar como falso para plantillas SÍ vale para biblioteca después de este fix):

```python
class RegresionCamposPostBibliotecaTests(TestCase):
    """El confirm POST de biblioteca manda las resoluciones como un único
    campo JSON -- a diferencia de plantillas (ver ISSUES.md [2026-07-28]),
    el conteo de campos del POST no escala con la cantidad de ejercicios
    pendientes de resolución manual, así que una biblioteca inicial de
    miles de ejercicios (escenario real, a diferencia de una plantilla)
    no puede romper contra DATA_UPLOAD_MAX_NUMBER_FIELDS."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gym", slug="gym")
        self.staff = User.objects.create_user(username="staff", password="clave12345")
        Perfil.objects.create(usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF)

    def test_600_ejercicios_pendientes_no_rompe_el_confirm_post(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])  # sin columna Grupo Muscular -> todos pendientes
        for i in range(600):
            ws.append([f"Ejercicio {i}"])

        self.client.login(username="staff", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"), {"archivo": _archivo_xlsx(wb)},
        )
        importacion = Importacion.objects.get()
        self.assertEqual(len(importacion.resultado["items"]), 600)

        resoluciones = {f"ejercicio {i}": "cuerpo_completo" for i in range(600)}
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": json.dumps(resoluciones)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.count(), 600)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ImportacionBibliotecaViewsTests importaciones.tests.RegresionCamposPostBibliotecaTests -v 2`
Expected: fallos — los dos tests actualizados porque `PreviewBibliotecaView` todavía espera un formset (`resoluciones` no es un campo que la vista actual entienda, va a crear ejercicios sin resolver o pedir el formset), el test nuevo de "falta resolver" porque no hay validación de pendientes-faltantes con el formato viejo, y el de 600 ejercicios porque con el formset actual ese POST directamente daría `TooManyFieldsSent` (400) en vez de 302.

- [ ] **Step 3: Implementar**

`importaciones/forms.py` — reemplazar `ResolucionGrupoMuscularForm`/`ResolucionGrupoMuscularFormSet` (eliminar ambos) por:

```python
import json


class ResolucionesJSONForm(forms.Form):
    """Reemplaza a `ResolucionGrupoMuscularFormSet` (un form por ejercicio
    pendiente) por un único campo con las resoluciones serializadas en
    JSON -- así el conteo de campos del POST de confirmación de biblioteca
    no escala con la cantidad de ejercicios sin match (una biblioteca real
    puede traer miles; ver ISSUES.md [2026-07-28] y su seguimiento)."""
    resoluciones = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_resoluciones(self):
        crudo = self.cleaned_data.get("resoluciones") or "{}"
        try:
            datos = json.loads(crudo)
        except (json.JSONDecodeError, TypeError):
            raise forms.ValidationError("Formato de resoluciones inválido.")
        if not isinstance(datos, dict):
            raise forms.ValidationError("Formato de resoluciones inválido.")
        for clave, valor in datos.items():
            if not isinstance(clave, str) or valor not in Ejercicio.GrupoMuscular.values:
                raise forms.ValidationError("Grupo muscular inválido.")
        return datos
```

(`Ejercicio` ya está importado en `forms.py` — sin cambios en ese import.)

`importaciones/views.py` — agregar `from ejercicios.models import Ejercicio` a los imports, reemplazar `ResolucionGrupoMuscularFormSet` por `ResolucionesJSONForm` en el import de `importaciones.forms`, y reescribir `PreviewBibliotecaView`:

```python
class PreviewBibliotecaView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/biblioteca_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.BIBLIOTECA,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _pendientes(self, importacion):
        return [
            item for item in importacion.resultado["items"]
            if item["match"]["tipo"] != "exacto" and not item["grupo_muscular_resuelto"]
        ]

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        return self._render(request, importacion, ResolucionesJSONForm())

    def _render(self, request, importacion, form):
        return render(request, self.template_name, {
            "importacion": importacion,
            "pendientes": self._pendientes(importacion),
            "grupo_muscular_choices": Ejercicio.GrupoMuscular.choices,
            "form": form,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        form = ResolucionesJSONForm(request.POST)
        if not form.is_valid():
            return self._render(request, importacion, form)

        resueltos_a_mano = form.cleaned_data["resoluciones"]
        faltantes = [
            item["nombre_original"] for item in self._pendientes(importacion)
            if item["nombre_normalizado"] not in resueltos_a_mano
        ]
        if faltantes:
            form.add_error(
                None, f"Falta resolver el grupo muscular de: {', '.join(faltantes)}.",
            )
            return self._render(request, importacion, form)

        decisiones = {"items": {
            item["nombre_normalizado"]: {
                "incluir": item["match"]["tipo"] != "exacto",
                "grupo_muscular": (
                    item["grupo_muscular_resuelto"]
                    or resueltos_a_mano.get(item["nombre_normalizado"])
                ),
            }
            for item in importacion.resultado["items"]
        }}

        try:
            creados = confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self._render(request, importacion, form)

        messages.success(request, f"Se crearon {len(creados)} ejercicio(s).")
        return redirect("ejercicios:listado")
```

`templates/importaciones/biblioteca_preview.html` — reemplazar el bloque `{{ formset.management_form }}` / `{% for f in formset %}` por selects planos (sin `name`, así el navegador NO los manda como parte del POST nativo) más un script mínimo que junta sus valores en el campo JSON oculto antes de enviar:

```html
    {% if pendientes %}
      <div class="tarjeta">
        <h2>Grupos musculares a resolver</h2>
        {% for item in pendientes %}
          <p>
            {{ item.nombre_original }}
            <select class="js-grupo-muscular" data-valor-original="{{ item.nombre_normalizado }}">
              <option value="">---------</option>
              {% for valor, etiqueta in grupo_muscular_choices %}
                <option value="{{ valor }}">{{ etiqueta }}</option>
              {% endfor %}
            </select>
          </p>
        {% endfor %}
      </div>
    {% endif %}
    {{ form.resoluciones }}
    {% for error in form.non_field_errors %}<p class="texto-error">{{ error }}</p>{% endfor %}
```

Y, antes de `</form>` (o en cualquier punto dentro del mismo `<form>`), agregar:

```html
    <script>
      document.currentScript.closest("form").addEventListener("submit", function () {
        var resoluciones = {};
        document.querySelectorAll(".js-grupo-muscular").forEach(function (select) {
          if (select.value) {
            resoluciones[select.dataset.valorOriginal] = select.value;
          }
        });
        document.getElementById("id_resoluciones").value = JSON.stringify(resoluciones);
      });
    </script>
```

(El resto del template -- tabla de items, filas inválidas, botón de confirmar, form de descartar -- no cambia.)

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones -v 2`
Expected: PASS (toda la suite).

- [ ] **Step 5: Agregar entrada a `ISSUES.md`**

Agregar una entrada nueva (no editar la de `[2026-07-28]`, que documenta el caso de plantillas que quedó aceptado tal cual) documentando que el caso de biblioteca, a diferencia de plantillas, sí se consideró un riesgo real (biblioteca inicial con miles de ejercicios es un escenario esperado, no patológico) y se resolvió con el campo JSON en vez de aceptarlo como riesgo o tocar `DATA_UPLOAD_MAX_NUMBER_FIELDS`.

- [ ] **Step 6: Commit**

```bash
git add importaciones/forms.py importaciones/views.py templates/importaciones/biblioteca_preview.html importaciones/tests.py ISSUES.md
git commit -m "fix(importaciones): resolución de grupo muscular de biblioteca vía campo JSON (evita TooManyFieldsSent con bibliotecas grandes)"
```

---

### Task 14: Biblioteca — resolución de matches ambiguos (usar existente vs crear nuevo)

**Contexto (no estaba en el plan original — surge de la revisión final de rama completa, después de la Tarea 13):** la revisión final encontró que el flujo de biblioteca calcula un match ambiguo por `rapidfuzz` (`resolver_nombre`, `matching.py`, ya construido en la Tarea 5) pero lo ignora por completo: `PreviewBibliotecaView.post()` trata cualquier item que no sea `tipo == "exacto"` como "crear nuevo", sin ofrecerle al staff la opción de "usar existente" que SÍ tiene el flujo de plantillas. Ejemplo reproducido: con "Sentadilla" ya en la biblioteca, importar "Sentadila" (typo, score ~94) crea un `Ejercicio` duplicado sin que el staff se entere. Esto contradice el Global Constraint ("Matches ambiguos... quedan pre-marcados en 'usar existente' — el staff elige activamente 'crear nuevo' si corresponde"), que el flujo de plantillas sí cumple. El dueño del producto pidió agregar la misma UI que plantillas (mostrar candidato + score, dejar elegir usar existente/crear nuevo) en vez de aceptarlo como riesgo documentado.

**Restricción de diseño (por qué esto no es un simple copy-paste de la UI de plantillas):** el flujo de plantillas resuelve esto con `ResolucionEjercicioFormSet` (un form de Django por ejercicio pendiente). La Tarea 13 reemplazó ese mismo patrón en biblioteca por un único campo JSON precisamente porque una biblioteca real puede traer miles de ejercicios y un form-por-pendiente rompe el límite de campos de Django (`DATA_UPLOAD_MAX_NUMBER_FIELDS`). Esta tarea NO puede reintroducir un formset — la resolución de "usar existente / crear nuevo" tiene que viajar en el MISMO campo JSON que ya lleva `grupo_muscular`, no en un mecanismo aparte.

**Files:**
- Modify: `importaciones/forms.py`, `importaciones/views.py`, `importaciones/services.py`, `templates/importaciones/biblioteca_preview.html`, `importaciones/tests.py`

**Interfaces:**
- `confirmar_importacion_biblioteca(*, importacion, gimnasio, decisiones)` (Task 8, `services.py`) **no cambia** — su guard existente (`if not decision["incluir"] or item["match"]["tipo"] == "exacto": continue`) ya hace lo correcto una vez que la vista calcula `incluir=False` también para un ambiguo resuelto como "usar_existente" (no solo para "exacto"). Confirmar esto releyendo la función antes de tocar nada — si no es así, hay que ajustarla, pero el diseño de referencia de abajo asume que no hace falta.
- `previsualizar_importacion_biblioteca` (`services.py`) gana un campo nuevo en el `match_json` de tipo "ambiguo": `candidato_nombre` (ya existe el patrón idéntico en `previsualizar_importacion_plantillas`, `services.py` línea ~74-76 — mismo criterio, biblioteca no lo tenía).
- El payload del campo `resoluciones` de `ResolucionesJSONForm` **cambia de forma**: de `{nombre_normalizado: grupo_muscular_str}` a `{nombre_normalizado: {"grupo_muscular": str|null, "accion": "usar_existente"|"crear_nuevo"|null}}`. Esto es una ruptura de compatibilidad con el payload de la Tarea 13 — hay que actualizar los tests existentes que postean el formato viejo (`ImportacionBibliotecaViewsTests.test_flujo_con_resolucion_manual_de_grupo_muscular`, `RegresionCamposPostBibliotecaTests.test_600_ejercicios_pendientes_no_rompe_el_confirm_post`), no solo agregar tests nuevos.

- [ ] **Step 1: Escribir los tests que fallan**

En `importaciones/tests.py`, actualizar los dos tests existentes que postean el formato viejo de `resoluciones`:

```python
    def test_flujo_con_resolucion_manual_de_grupo_muscular(self):
        # ... (setUp y subida sin cambios) ...
        datos = {"resoluciones": json.dumps({"hip thrust": {"grupo_muscular": "piernas"}})}
        # ... (resto sin cambios) ...
```

```python
    def test_600_ejercicios_pendientes_no_rompe_el_confirm_post(self):
        # ... (setUp y subida sin cambios) ...
        resoluciones = {
            f"ejercicio {i}": {"grupo_muscular": "cuerpo_completo"} for i in range(600)
        }
        # ... (resto sin cambios) ...
```

Agregar tests nuevos a `ImportacionBibliotecaViewsTests`:

```python
    def _archivo_con_ambiguo(self, gimnasio):
        Ejercicio.objects.create(
            gimnasio=gimnasio, nombre="Sentadilla", grupo_muscular="piernas",
        )
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Nombre"])
        ws.append(["Sentadila"])  # typo -> match ambiguo contra "Sentadilla"
        return _archivo_xlsx(wb)

    def test_preview_muestra_candidato_y_score_para_match_ambiguo(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        response = self.client.get(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk])
        )
        self.assertContains(response, "Sentadilla")  # nombre del candidato
        self.assertContains(response, "94")  # score (rapidfuzz determinístico para este par)

    def test_ambiguo_usar_existente_no_crea_ejercicio_nuevo(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        datos = {
            "resoluciones": json.dumps({"sentadila": {"accion": "usar_existente"}}),
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 1)

    def test_ambiguo_crear_nuevo_requiere_grupo_muscular_y_crea_ejercicio(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        datos = {
            "resoluciones": json.dumps(
                {"sentadila": {"accion": "crear_nuevo", "grupo_muscular": "piernas"}}
            ),
        }
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 2)
        self.assertTrue(
            Ejercicio.objects.filter(gimnasio=self.gimnasio_a, nombre="Sentadila").exists()
        )

    def test_ambiguo_sin_resolver_no_confirma(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.post(
            reverse("importaciones:biblioteca_subir"),
            {"archivo": self._archivo_con_ambiguo(self.gimnasio_a)},
        )
        importacion = Importacion.objects.get()
        response = self.client.post(
            reverse("importaciones:biblioteca_preview", args=[importacion.pk]),
            {"resoluciones": "{}"},
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza con error
        self.assertEqual(Ejercicio.objects.filter(gimnasio=self.gimnasio_a).count(), 1)
```

(El score exacto de rapidfuzz para "sentadila" vs "sentadilla" puede no ser 94 — correr el test una vez con un assert flojo o imprimir `match.score` para confirmar el valor real antes de fijar el assert; el punto del test es que ALGÚN número aparece, no un valor mágico exacto. Si el score no es determinístico entre corridas, usar `assertRegex` con un rango o directamente no fijar el número exacto en el assert.)

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test importaciones.tests.ImportacionBibliotecaViewsTests importaciones.tests.RegresionCamposPostBibliotecaTests -v 2`
Expected: fallos — los dos tests actualizados porque el payload viejo (`{"hip thrust": "piernas"}`) ya no matchea lo que la vista actual espera vs. lo que se necesita después del cambio (dependiendo del orden de implementación esto puede pasar en rojo por razones distintas; lo importante es que fallen ANTES del Step 3 y pasen DESPUÉS), y los 4 tests nuevos porque no existe manejo de "ambiguo" en absoluto todavía (sin `candidato_nombre` en el preview, sin decisión de `accion` posible, `usar_existente` para un ambiguo no evita la creación).

- [ ] **Step 3: Implementar**

`importaciones/services.py` — en `previsualizar_importacion_biblioteca`, agregar `candidato_nombre` al `match_json` de tipo "ambiguo" (mismo patrón que `previsualizar_importacion_plantillas`):

```python
        match_json = (
            {"tipo": "exacto", "ejercicio_id": match.ejercicio.pk}
            if match.tipo == "exacto"
            else {
                "tipo": "ambiguo",
                "candidato_id": match.candidato.pk,
                "candidato_nombre": match.candidato.nombre,
                "score": match.score,
            }
            if match.tipo == "ambiguo"
            else {"tipo": "nuevo"}
        )
```

`importaciones/forms.py` — reescribir `ResolucionesJSONForm.clean()` para el nuevo payload anidado:

```python
class ResolucionesJSONForm(forms.Form):
    """... (docstring existente, sin cambios de intención -- sigue siendo
    un único campo JSON por la misma razón de escalabilidad; el payload
    ahora es {nombre: {"grupo_muscular": str|None, "accion": str|None}}
    para poder cargar también la decisión usar_existente/crear_nuevo de un
    match ambiguo sin volver a un formset por ejercicio)."""
    resoluciones = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self):
        cleaned = super().clean()
        crudo = cleaned.get("resoluciones") or "{}"
        try:
            datos = json.loads(crudo)
        except (json.JSONDecodeError, TypeError):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        if not isinstance(datos, dict):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        for clave, valor in datos.items():
            if not isinstance(clave, str) or not isinstance(valor, dict):
                self.add_error(None, "Formato de resoluciones inválido.")
                return cleaned
            grupo_muscular = valor.get("grupo_muscular")
            if grupo_muscular is not None and grupo_muscular not in Ejercicio.GrupoMuscular.values:
                self.add_error(None, "Grupo muscular inválido.")
                return cleaned
            accion = valor.get("accion")
            if accion is not None and accion not in ("usar_existente", "crear_nuevo"):
                self.add_error(None, "Acción inválida.")
                return cleaned
        cleaned["resoluciones"] = datos
        return cleaned
```

`importaciones/views.py` — reescribir `PreviewBibliotecaView`:

```python
class PreviewBibliotecaView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/biblioteca_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.BIBLIOTECA,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _pendientes(self, importacion):
        # Un item pendiente puede necesitar UNA de las dos decisiones, o
        # ambas: `needs_accion` (match ambiguo -- usar existente o crear
        # nuevo) y/o `needs_grupo` (grupo_muscular sin auto-resolver). Se
        # devuelven juntas en una sola lista para que el template arme UNA
        # fila por ejercicio pendiente, no dos secciones separadas que
        # dupliquen filas para el caso ambiguo+sin-grupo.
        pendientes = []
        for item in importacion.resultado["items"]:
            tipo = item["match"]["tipo"]
            needs_accion = tipo == "ambiguo"
            needs_grupo = tipo != "exacto" and not item["grupo_muscular_resuelto"]
            if needs_accion or needs_grupo:
                pendientes.append({**item, "needs_accion": needs_accion, "needs_grupo": needs_grupo})
        return pendientes

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        return self._render(request, importacion, ResolucionesJSONForm())

    def _render(self, request, importacion, form):
        return render(request, self.template_name, {
            "importacion": importacion,
            "pendientes": self._pendientes(importacion),
            "grupo_muscular_choices": Ejercicio.GrupoMuscular.choices,
            "form": form,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        form = ResolucionesJSONForm(request.POST)
        if not form.is_valid():
            return self._render(request, importacion, form)

        resoluciones = form.cleaned_data["resoluciones"]
        faltantes = []
        for item in importacion.resultado["items"]:
            tipo = item["match"]["tipo"]
            if tipo == "exacto":
                continue
            entrada = resoluciones.get(item["nombre_normalizado"], {})
            if tipo == "ambiguo":
                accion = entrada.get("accion")
                if accion not in ("usar_existente", "crear_nuevo"):
                    faltantes.append(item["nombre_original"])
                    continue
                if accion == "usar_existente":
                    continue  # no crea nada -> no necesita grupo_muscular
            if not item["grupo_muscular_resuelto"] and not entrada.get("grupo_muscular"):
                faltantes.append(item["nombre_original"])
        if faltantes:
            form.add_error(None, f"Falta resolver: {', '.join(faltantes)}.")
            return self._render(request, importacion, form)

        decisiones = {"items": {
            item["nombre_normalizado"]: {
                "incluir": (
                    item["match"]["tipo"] != "exacto"
                    and not (
                        item["match"]["tipo"] == "ambiguo"
                        and resoluciones.get(item["nombre_normalizado"], {}).get("accion") == "usar_existente"
                    )
                ),
                "grupo_muscular": (
                    item["grupo_muscular_resuelto"]
                    or resoluciones.get(item["nombre_normalizado"], {}).get("grupo_muscular")
                ),
            }
            for item in importacion.resultado["items"]
        }}

        try:
            creados = confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self._render(request, importacion, form)

        messages.success(request, f"Se crearon {len(creados)} ejercicio(s).")
        return redirect("ejercicios:listado")
```

(Verificar antes de tocar `services.py::confirmar_importacion_biblioteca`: con `incluir=False` para un ambiguo resuelto como "usar_existente", su guard existente `if not decision["incluir"] or item["match"]["tipo"] == "exacto": continue` ya salta ese item sin crear nada -- no debería hacer falta ningún cambio ahí. Si al correr los tests del Step 4 algo no cierra, ese es el lugar a mirar.)

`templates/importaciones/biblioteca_preview.html` — reemplazar el bloque `{% if pendientes %}` (la sección "Grupos musculares a resolver") por:

```html
    {% if pendientes %}
      <div class="tarjeta">
        <h2>Ejercicios a resolver</h2>
        {% for item in pendientes %}
          <div>
            <p>{{ item.nombre_original }}</p>
            {% if item.needs_accion %}
              <select class="js-accion" data-valor-original="{{ item.nombre_normalizado }}">
                <option value="">---------</option>
                <option value="usar_existente">
                  Usar existente: {{ item.match.candidato_nombre }} ({{ item.match.score }}% de similitud)
                </option>
                <option value="crear_nuevo">Crear como nuevo ejercicio</option>
              </select>
            {% endif %}
            {% if item.needs_grupo %}
              <select class="js-grupo-muscular" data-valor-original="{{ item.nombre_normalizado }}">
                <option value="">---------</option>
                {% for valor, etiqueta in grupo_muscular_choices %}
                  <option value="{{ valor }}">{{ etiqueta }}</option>
                {% endfor %}
              </select>
            {% endif %}
          </div>
        {% endfor %}
      </div>
    {% endif %}
```

Y el `<script>` de más abajo (mismo `<form>`, reemplaza el bloque de script existente):

```html
    <script>
      document.currentScript.closest("form").addEventListener("submit", function () {
        var resoluciones = {};
        function entrada(nombre) {
          if (!resoluciones[nombre]) { resoluciones[nombre] = {}; }
          return resoluciones[nombre];
        }
        document.querySelectorAll(".js-grupo-muscular").forEach(function (select) {
          if (select.value) { entrada(select.dataset.valorOriginal).grupo_muscular = select.value; }
        });
        document.querySelectorAll(".js-accion").forEach(function (select) {
          if (select.value) { entrada(select.dataset.valorOriginal).accion = select.value; }
        });
        document.getElementById("id_resoluciones").value = JSON.stringify(resoluciones);
      });
    </script>
```

(El resto del template -- tabla de items, filas inválidas, advertencias de columnas, botón de confirmar, form de descartar -- no cambia.)

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test importaciones -v 2`
Expected: PASS (toda la suite).

- [ ] **Step 5: Agregar entrada a `ISSUES.md`**

Agregar una entrada nueva (no editar las de `[2026-07-28]`) documentando que el flujo de biblioteca ahora ofrece "usar existente / crear nuevo" para matches ambiguos, igual que plantillas, cerrando el gap encontrado en la revisión final de rama.

- [ ] **Step 6: Commit**

```bash
git add importaciones/forms.py importaciones/views.py importaciones/services.py templates/importaciones/biblioteca_preview.html importaciones/tests.py ISSUES.md
git commit -m "feat(importaciones): biblioteca ofrece usar existente/crear nuevo para matches ambiguos"
```
