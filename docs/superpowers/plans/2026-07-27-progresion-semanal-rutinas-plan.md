# Progresión semanal en rutinas (semana 1-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un campo `semana` (1-4) a los items de rutina para que una `RutinaPlantilla`/`RutinaAsignada` pueda tener ejercicios distintos por semana dentro de un ciclo de 4 semanas, y exponer eso en la UI de staff y el portal del alumno.

**Architecture:** Cambio aditivo sobre `rutinas/models.py` (campo nuevo + una property calculada), sin tocar el mecanismo existente de `dias_por_semana` (plantillas separadas por cantidad de días, sin cambios). Django templates + una vista existente (`tenants.views.HomeView._portal_alumno`) se ajustan para leer/mostrar el campo nuevo.

**Tech Stack:** Django 5.2, `django.test.TestCase` (sin mocking de tiempo — offsets relativos a `timezone.localdate()`, patrón ya usado en `turnos/tests.py`).

Spec: `docs/superpowers/specs/2026-07-27-progresion-semanal-rutinas-design.md`.

## Global Constraints

- El ciclo es **siempre 4 semanas**: constante de código `SEMANAS_POR_CICLO = 4` en `rutinas/models.py`, no un campo configurable.
- Los ejercicios **pueden diferir entre semanas** — `semana` vive en el item (`RutinaPlantillaItem`/`RutinaAsignadaItem`), no en la plantilla.
- `RutinaPlantilla.dias_por_semana` **no cambia** — la variabilidad de días por alumno sigue resolviéndose con plantillas separadas por cantidad de días.
- `semana_actual` se **calcula por fecha** (`fecha_inicio` + hoy), nunca la edita el staff a mano.
- **Sin loop automático**: al llegar a semana 4 se sostiene ahí indefinidamente hasta que el staff cierre la asignación y cree una nueva.
- **Fuera de alcance de este plan**: el importador de Excel/Google Sheets (Proyecto 2, spec posterior). No tocar nada relacionado a parsing de archivos.
- Este repo no tiene tests de `admin.py` en ningún app — los cambios ahí no llevan test dedicado (ver Tarea 1, paso final).

---

### Task 1: Modelo — campo `semana` en los items + migración + admin

**Files:**
- Modify: `rutinas/models.py:25-28` (imports), `rutinas/models.py:97-135` (`RutinaPlantillaItem`), `rutinas/models.py:215-246` (`RutinaAsignadaItem`)
- Modify: `rutinas/admin.py:44`, `rutinas/admin.py:51` (`list_display`)
- Create: `rutinas/migrations/0002_item_semana.py` (generada con `makemigrations`, no se escribe a mano)
- Test: `rutinas/tests.py`

**Interfaces:**
- Produces: constante `SEMANAS_POR_CICLO = 4` (`rutinas/models.py`, módulo-level, importable como `from rutinas.models import SEMANAS_POR_CICLO`). Campo `semana` (`PositiveSmallIntegerField`, `default=1`, rango 1-4 vía validators) en `RutinaPlantillaItem` y `RutinaAsignadaItem`. `Meta.ordering` de ambos pasa a `["semana", "dia", "orden"]`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar en `rutinas/tests.py`, después de la clase `ModeloBasicoTests` (línea 122):

```python
class SemanaItemTests(RutinasTestCase):
    """Campo `semana` (1-4) en los items de plantilla y de asignación."""

    def test_item_semana_default_es_1(self):
        _, item1, _ = self.crear_plantilla_con_items()
        self.assertEqual(item1.semana, 1)

    def test_item_semana_acepta_valor_explicito(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        item = RutinaPlantillaItem.objects.create(
            rutina=plantilla,
            ejercicio=self.press_banca,
            semana=3,
            dia=1,
            orden=3,
            series=3,
            repeticiones="10",
        )
        self.assertEqual(item.semana, 3)

    def test_item_semana_fuera_de_rango_falla_full_clean(self):
        _, item1, _ = self.crear_plantilla_con_items()
        item1.semana = 5
        with self.assertRaises(ValidationError):
            item1.full_clean()
        item1.semana = 0
        with self.assertRaises(ValidationError):
            item1.full_clean()

    def test_items_ordenados_por_semana_antes_que_dia_y_orden(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        # item1: dia=1, orden=1 · item2: dia=1, orden=2 (mismo día, ambos semana=1 por default)
        item1.semana = 2
        item1.save()
        items = list(plantilla.items.all())
        # item2 (semana=1) debe listar ANTES que item1 (semana=2), aunque
        # item1 tenga menor `orden` -- confirma que `semana` pesa más que `orden`.
        self.assertEqual(items[0], item2)
        self.assertEqual(items[1], item1)

    def test_rutina_asignada_item_semana_default_es_1_y_acepta_explicito(self):
        plantilla, _, _ = self.crear_plantilla_con_items()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        item = asignada.items.first()
        self.assertEqual(item.semana, 1)
        item.semana = 4
        item.full_clean()
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.semana, 4)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test rutinas.tests.SemanaItemTests -v 2`
Expected: ERROR/FAIL — `TypeError: 'semana' is an invalid keyword argument` (o `AttributeError`, según el test) porque el campo todavía no existe.

- [ ] **Step 3: Implementar el campo + la constante**

En `rutinas/models.py`, reemplazar el bloque de imports (líneas 25-28):

```python
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from core.models import TenantOwnedModel, TimeStampedModel

SEMANAS_POR_CICLO = 4
```

En `RutinaPlantillaItem` (línea 97 en adelante), agregar el campo antes de `dia` y actualizar `Meta.ordering`:

```python
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
```

```python
    class Meta:
        verbose_name = "item de plantilla"
        verbose_name_plural = "items de plantilla"
        ordering = ["semana", "dia", "orden"]
```

En `RutinaAsignadaItem` (línea 215 en adelante), el mismo campo antes de `dia`:

```python
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
```

```python
    class Meta:
        verbose_name = "item de rutina asignada"
        verbose_name_plural = "items de rutina asignada"
        ordering = ["semana", "dia", "orden"]
```

Generar la migración:

Run: `python manage.py makemigrations rutinas`
Expected: crea `rutinas/migrations/0002_<nombre_autogenerado>.py` agregando el campo `semana` a ambos modelos.

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test rutinas.tests.SemanaItemTests -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Admin — mostrar `semana` en el listado**

En `rutinas/admin.py`, actualizar los dos `list_display` (sin test dedicado — este repo no testea `admin.py`):

```python
# rutinas/admin.py:44 (RutinaPlantillaItemAdmin)
list_display = ("rutina", "ejercicio", "semana", "dia", "orden", "series", "repeticiones")
```

```python
# rutinas/admin.py:51 (RutinaAsignadaItemAdmin)
list_display = ("rutina_asignada", "ejercicio_nombre_snapshot", "semana", "dia", "orden", "series", "repeticiones")
```

- [ ] **Step 6: Commit**

```bash
git add rutinas/models.py rutinas/admin.py rutinas/migrations/ rutinas/tests.py
git commit -m "feat(rutinas): agregar campo semana (1-4) a los items de rutina"
```

---

### Task 2: `duplicar()` y `crear_desde_plantilla()` copian `semana`

**Files:**
- Modify: `rutinas/models.py:79-93` (`RutinaPlantilla.duplicar`), `rutinas/models.py:196-211` (`RutinaAsignada.crear_desde_plantilla`)
- Test: `rutinas/tests.py` (clases `DuplicarPlantillaTests` y `CrearDesdePlantillaTests`)

**Interfaces:**
- Consumes: campo `semana` de Task 1.
- Produces: nada nuevo — corrige que `duplicar()`/`crear_desde_plantilla()` preserven `semana` (hoy la ignoran, todo copia queda en semana=1 por el default del campo).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar en `DuplicarPlantillaTests` (después de la línea 297, dentro de la clase):

```python
    def test_duplicar_copia_la_semana_de_cada_item(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.semana = 2
        item1.save()
        copia = plantilla.duplicar()
        copiado1 = copia.items.get(orden=1)
        self.assertEqual(copiado1.semana, 2)
        copiado2 = copia.items.get(orden=2)
        self.assertEqual(copiado2.semana, 1)
```

Agregar en `CrearDesdePlantillaTests` (después de `test_copia_los_valores_correctos`, línea 161):

```python
    def test_crear_desde_plantilla_copia_la_semana_de_cada_item(self):
        plantilla, item1, item2 = self.crear_plantilla_con_items()
        item1.semana = 3
        item1.save()
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=date(2026, 1, 1),
        )
        copiado1 = asignada.items.get(orden=1)
        self.assertEqual(copiado1.semana, 3)
        copiado2 = asignada.items.get(orden=2)
        self.assertEqual(copiado2.semana, 1)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test rutinas.tests.DuplicarPlantillaTests.test_duplicar_copia_la_semana_de_cada_item rutinas.tests.CrearDesdePlantillaTests.test_crear_desde_plantilla_copia_la_semana_de_cada_item -v 2`
Expected: FAIL — `AssertionError: 1 != 2` (la copia quedó en semana=1, el default, porque `bulk_create` no pasa `semana` todavía).

- [ ] **Step 3: Implementar**

En `RutinaPlantilla.duplicar()` (`rutinas/models.py:79-93`), agregar `semana=item.semana,` al `RutinaPlantillaItem(...)` del `bulk_create`:

```python
            RutinaPlantillaItem.objects.bulk_create(
                [
                    RutinaPlantillaItem(
                        rutina=copia,
                        ejercicio=item.ejercicio,
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        descanso=item.descanso,
                        notas=item.notas,
                    )
                    for item in self.items.all()
                ]
            )
```

En `RutinaAsignada.crear_desde_plantilla()` (`rutinas/models.py:196-211`), agregar `semana=item.semana,` al `RutinaAsignadaItem(...)`:

```python
            RutinaAsignadaItem.objects.bulk_create(
                [
                    RutinaAsignadaItem(
                        rutina_asignada=asignada,
                        ejercicio_nombre_snapshot=item.ejercicio.nombre,
                        ejercicio_video_snapshot=item.ejercicio.url_video,
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        descanso=item.descanso,
                        notas=item.notas,
                    )
                    for item in plantilla.items.all()
                ]
            )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test rutinas.tests.DuplicarPlantillaTests rutinas.tests.CrearDesdePlantillaTests -v 2`
Expected: PASS (todas, incluidas las preexistentes — regresión).

- [ ] **Step 5: Commit**

```bash
git add rutinas/models.py rutinas/tests.py
git commit -m "fix(rutinas): duplicar() y crear_desde_plantilla() copian el campo semana"
```

---

### Task 3: `RutinaAsignada.semana_actual`

**Files:**
- Modify: `rutinas/models.py:1-28` (imports), `rutinas/models.py:138-213` (clase `RutinaAsignada`, agregar property después de `crear_desde_plantilla`)
- Test: `rutinas/tests.py`

**Interfaces:**
- Consumes: `SEMANAS_POR_CICLO` (Task 1), `self.fecha_inicio` (campo existente de `RutinaAsignada`).
- Produces: property `RutinaAsignada.semana_actual -> int`, valor en `[1, SEMANAS_POR_CICLO]`. La consumen Task 6 (template `asignada_detail.html`) y Task 7 (portal del alumno).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar en `rutinas/tests.py`, después de la clase `SemanaItemTests` (agregada en Task 1). Requiere agregar `timedelta` al import de `datetime` (línea 17) y `from django.utils import timezone` a los imports:

```python
from datetime import date, timedelta
...
from django.utils import timezone
```

```python
class SemanaActualTests(RutinasTestCase):
    """`RutinaAsignada.semana_actual`: calculada por fecha, clampeada en 4,
    sin loop automático."""

    def _asignada_con_inicio(self, fecha_inicio):
        plantilla, _, _ = self.crear_plantilla_con_items()
        return RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            plantilla=plantilla,
            fecha_inicio=fecha_inicio,
        )

    def test_semana_actual_es_1_el_dia_de_inicio(self):
        asignada = self._asignada_con_inicio(timezone.localdate())
        self.assertEqual(asignada.semana_actual, 1)

    def test_semana_actual_es_1_a_los_6_dias(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=6)
        )
        self.assertEqual(asignada.semana_actual, 1)

    def test_semana_actual_pasa_a_2_a_los_7_dias(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=7)
        )
        self.assertEqual(asignada.semana_actual, 2)

    def test_semana_actual_se_clampea_en_4(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() - timedelta(days=100)
        )
        self.assertEqual(asignada.semana_actual, 4)

    def test_semana_actual_es_1_si_fecha_inicio_es_futura(self):
        asignada = self._asignada_con_inicio(
            timezone.localdate() + timedelta(days=5)
        )
        self.assertEqual(asignada.semana_actual, 1)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test rutinas.tests.SemanaActualTests -v 2`
Expected: ERROR — `AttributeError: 'RutinaAsignada' object has no attribute 'semana_actual'`.

- [ ] **Step 3: Implementar**

Agregar `from django.utils import timezone` a los imports de `rutinas/models.py` (junto a `from django.db import models, transaction`).

Agregar la property dentro de la clase `RutinaAsignada`, inmediatamente después del método `crear_desde_plantilla` (después de la línea 212, antes de `class RutinaAsignadaItem`):

```python
    @property
    def semana_actual(self) -> int:
        """Semana del ciclo (1-4) que le toca a esta asignación hoy, según
        `fecha_inicio`. Se recalcula sola en cada acceso -- no es un campo
        persistido, así que nunca se desincroniza. Sin loop: una vez
        alcanzada la semana 4 se sostiene ahí hasta que el staff cierre esta
        asignación y cree una nueva."""
        dias_transcurridos = (timezone.localdate() - self.fecha_inicio).days
        if dias_transcurridos < 0:
            return 1
        return min(SEMANAS_POR_CICLO, (dias_transcurridos // 7) + 1)
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test rutinas.tests.SemanaActualTests -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add rutinas/models.py rutinas/tests.py
git commit -m "feat(rutinas): agregar RutinaAsignada.semana_actual calculada por fecha"
```

---

### Task 4: Form de item — agregar `semana`

**Files:**
- Modify: `rutinas/forms.py:33-41` (`RutinaPlantillaItemForm.Meta.fields`)
- Modify: `rutinas/tests.py` (test existente `test_item_crud_dentro_de_la_plantilla_correcta`, en `RutinasViewsTests`, línea 559)

**Interfaces:**
- Consumes: campo `semana` (Task 1).
- Produces: el form de alta/edición de item de plantilla ahora acepta y valida `semana`. Sin cambios de firma para nadie más.

- [ ] **Step 1: Modificar el test existente para que falle**

En `rutinas/tests.py`, dentro de `test_item_crud_dentro_de_la_plantilla_correcta` (línea 559), agregar `"semana"` al diccionario `datos` y las aserciones correspondientes:

```python
    def test_item_crud_dentro_de_la_plantilla_correcta(self):
        self.client.login(username="staff_a", password="clave12345")

        datos = {
            "ejercicio": self.ejercicio_a.pk,
            "semana": 2,
            "dia": 2,
            "orden": 1,
            "series": 3,
            "repeticiones": "10",
            "descanso": "60s",
            "notas": "",
        }
        response = self.client.post(
            reverse("rutinas:item_crear", args=[self.plantilla_a.pk]), datos
        )
        self.assertEqual(response.status_code, 302)
        nuevo_item = RutinaPlantillaItem.objects.get(rutina=self.plantilla_a, dia=2)
        self.assertEqual(nuevo_item.ejercicio, self.ejercicio_a)
        self.assertEqual(nuevo_item.semana, 2)

        response = self.client.get(
            reverse(
                "rutinas:item_editar", args=[self.plantilla_a.pk, nuevo_item.pk]
            )
        )
        self.assertEqual(response.status_code, 200)

        datos["series"] = 9
        datos["semana"] = 3
        response = self.client.post(
            reverse(
                "rutinas:item_editar", args=[self.plantilla_a.pk, nuevo_item.pk]
            ),
            datos,
        )
        self.assertEqual(response.status_code, 302)
        nuevo_item.refresh_from_db()
        self.assertEqual(nuevo_item.series, 9)
        self.assertEqual(nuevo_item.semana, 3)

        eliminar_url = reverse(
            "rutinas:item_eliminar", args=[self.plantilla_a.pk, nuevo_item.pk]
        )
        response = self.client.get(eliminar_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(eliminar_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            RutinaPlantillaItem.objects.filter(pk=nuevo_item.pk).exists()
        )
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python manage.py test rutinas.tests.RutinasViewsTests.test_item_crud_dentro_de_la_plantilla_correcta -v 2`
Expected: FAIL — `AssertionError: 1 != 2` (el form ignora la clave `"semana"` del POST porque todavía no está en `Meta.fields`, el item queda en el default).

- [ ] **Step 3: Implementar**

En `rutinas/forms.py:30-41`:

```python
class RutinaPlantillaItemForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantillaItem
        fields = [
            "ejercicio",
            "semana",
            "dia",
            "orden",
            "series",
            "repeticiones",
            "descanso",
            "notas",
        ]
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `python manage.py test rutinas.tests.RutinasViewsTests -v 2`
Expected: PASS (toda la clase — confirma que no rompimos ningún otro test de vistas).

- [ ] **Step 5: Commit**

```bash
git add rutinas/forms.py rutinas/tests.py
git commit -m "feat(rutinas): el form de item de plantilla acepta el campo semana"
```

---

### Task 5: Templates de staff — columna Semana + "semana actual"

**Files:**
- Modify: `templates/rutinas/plantilla_detail.html`
- Modify: `templates/rutinas/asignada_detail.html`
- Test: `rutinas/tests.py` (clase `RutinasViewsTests`)

**Interfaces:**
- Consumes: campo `semana` de los items (Task 1) y `RutinaAsignada.semana_actual` (Task 3). El context existente (`items`, `plantilla`, `asignada`) ya lo proveen las vistas (`RutinaPlantillaDetailView`/`RutinaAsignadaDetailView`), no requiere cambios de vista.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar en `RutinasViewsTests` (`rutinas/tests.py`), después de `test_item_crud_dentro_de_la_plantilla_correcta`:

```python
    def test_plantilla_detail_muestra_columna_semana(self):
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:plantilla_detalle", args=[self.plantilla_a.pk])
        )
        self.assertContains(response, "<th>Semana</th>", html=True)

    def test_asignada_detail_muestra_semana_actual(self):
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio_a,
            alumno=self.alumno_a,
            plantilla=self.plantilla_a,
            fecha_inicio=timezone.localdate() - timedelta(days=7),
        )
        self.client.login(username="staff_a", password="clave12345")
        response = self.client.get(
            reverse("rutinas:asignada_detalle", args=[asignada.pk])
        )
        self.assertContains(response, "Semana actual: 2 de 4")
```

Este archivo ya importa `timezone` (agregado en Task 3); si `timedelta` no quedó importado ahí, agregarlo también (`from datetime import date, timedelta`).

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python manage.py test rutinas.tests.RutinasViewsTests.test_plantilla_detail_muestra_columna_semana rutinas.tests.RutinasViewsTests.test_asignada_detail_muestra_semana_actual -v 2`
Expected: FAIL — no existe ningún `<th>Semana</th>` ni el texto "Semana actual: 2 de 4" en el HTML todavía.

- [ ] **Step 3: Implementar**

En `templates/rutinas/plantilla_detail.html`, en el `<thead>` de la tabla de ejercicios, agregar `<th>Semana</th>` antes de `<th>Día</th>`:

```html
      <thead>
        <tr>
          <th>Semana</th>
          <th>Día</th>
          <th>Orden</th>
          <th>Ejercicio</th>
          <th>Series</th>
          <th>Repeticiones</th>
          <th>Descanso</th>
          <th>Notas</th>
          <th></th>
        </tr>
      </thead>
```

Y en el `<tbody>`, agregar `<td>{{ item.semana }}</td>` antes de `<td>{{ item.dia }}</td>`, y subir el `colspan` del estado vacío de 8 a 9:

```html
          <tr>
            <td>{{ item.semana }}</td>
            <td>{{ item.dia }}</td>
            <td>{{ item.orden }}</td>
            ...
```

```html
            <td colspan="9" class="texto-suave">Esta plantilla todavía no tiene ejercicios.</td>
```

En `templates/rutinas/asignada_detail.html`, agregar la línea de semana actual en la tarjeta de datos (después de "Fecha de fin"):

```html
    <p>Fecha de fin: {{ asignada.fecha_fin|default:"—" }}</p>
    <p>Semana actual: {{ asignada.semana_actual }} de 4</p>
```

Y el mismo agregado de columna **Semana** en su tabla de ejercicios (mismo patrón que `plantilla_detail.html`: `<th>Semana</th>` antes de `<th>Día</th>`, `<td>{{ item.semana }}</td>` antes de `<td>{{ item.dia }}</td>`, `colspan="8"` → `colspan="9"` en el estado vacío).

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test rutinas.tests.RutinasViewsTests -v 2`
Expected: PASS (toda la clase).

- [ ] **Step 5: Commit**

```bash
git add templates/rutinas/plantilla_detail.html templates/rutinas/asignada_detail.html rutinas/tests.py
git commit -m "feat(rutinas): mostrar semana y semana actual en las vistas de staff"
```

---

### Task 6: Portal del alumno — filtrar por semana actual

**Files:**
- Modify: `tenants/views.py:90-131` (`HomeView._portal_alumno`)
- Modify: `templates/tenants/home.html:104-144`
- Test: `tenants/tests.py` (clase `HomeViewAlumnoTests`)

**Interfaces:**
- Consumes: `RutinaAsignada.semana_actual` (Task 3).
- Produces: nueva entrada de contexto `items_semana_actual` en `HomeView.get_context_data` (vía `_portal_alumno`), consumida solo por `templates/tenants/home.html`.

- [ ] **Step 1: Escribir el test que falla**

Agregar en `tenants/tests.py`, dentro de `HomeViewAlumnoTests`, después de `test_alumno_ve_rutina_pago_y_novedad` (línea 268). Si `timedelta` no está importado en este archivo, agregar `from datetime import timedelta` a los imports:

```python
    def test_alumno_ve_solo_los_ejercicios_de_su_semana_actual(self):
        _user, _perfil, alumno = self._crear_alumno_con_login(
            username="fede", nombre="Fede", apellido="Iglesias"
        )
        rutina = RutinaAsignada.objects.create(
            gimnasio=self.gimnasio,
            alumno=alumno,
            nombre_snapshot="Rutina Progresiva",
            objetivo_snapshot="Hipertrofia",
            fecha_inicio=self.hoy - timedelta(days=7),  # hoy cae en semana 2
            activa=True,
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Sentadilla semana 1",
            semana=1,
            dia=1,
            orden=1,
            series=4,
            repeticiones="10",
        )
        RutinaAsignadaItem.objects.create(
            rutina_asignada=rutina,
            ejercicio_nombre_snapshot="Peso muerto semana 2",
            semana=2,
            dia=1,
            orden=1,
            series=4,
            repeticiones="8",
        )

        self.client.login(username="fede", password="clave-123456")
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Peso muerto semana 2")
        self.assertNotContains(response, "Sentadilla semana 1")
        self.assertContains(response, "Semana 2 de 4")
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python manage.py test tenants.tests.HomeViewAlumnoTests.test_alumno_ve_solo_los_ejercicios_de_su_semana_actual -v 2`
Expected: FAIL — `assertNotContains` falla porque hoy el template muestra TODOS los items (`rutina_actual.items.all`), sin filtrar por semana.

- [ ] **Step 3: Implementar**

En `tenants/views.py`, reemplazar el bloque final de `_portal_alumno` (líneas 120-137: desde `hoy = timezone.now().date()` hasta el `}` de cierre) por:

```python
        hoy = timezone.now().date()
        rutina_actual = alumno.rutinas_asignadas.filter(activa=True).first()

        return {
            "alumno": alumno,
            "rutina_actual": rutina_actual,
            "items_semana_actual": (
                rutina_actual.items.filter(semana=rutina_actual.semana_actual)
                if rutina_actual
                else []
            ),
            "mensualidad_actual": alumno.pagos.filter(
                mes=hoy.month, anio=hoy.year
            ).first(),
            "ultimas_novedades": Novedad.objects.for_gimnasio(perfil.gimnasio)
            .visibles()
            .para_alumno(alumno)[:5],
            "ids_novedades_leidas": set(
                alumno.novedades_leidas.values_list("novedad_id", flat=True)
            ),
            "medios_cobro": MedioCobro.objects.for_gimnasio(
                perfil.gimnasio
            ).filter(activo=True),
        }
```

Único cambio real: se agrega `rutina_actual` como variable local (para no llamar dos veces al queryset) y la entrada nueva `"items_semana_actual"`. El resto del diccionario (`ultimas_novedades`, `ids_novedades_leidas`, `medios_cobro`) queda idéntico al original.

En `templates/tenants/home.html` (líneas 104-144), agregar el subtítulo de semana y cambiar el loop:

```html
    <div class="tarjeta">
      <h2>Tu rutina</h2>
      {% if rutina_actual %}
        <p><strong>{{ rutina_actual.nombre_snapshot }}</strong></p>
        <p class="texto-suave">Objetivo: {{ rutina_actual.objetivo_snapshot }}</p>
        <p class="texto-suave">Semana {{ rutina_actual.semana_actual }} de 4</p>
        <table class="tabla">
          <thead>
            <tr>
              <th>Día</th>
              <th>Ejercicio</th>
              <th>Series</th>
              <th>Repeticiones</th>
              <th>Descanso</th>
              <th>Notas</th>
              <th>Video</th>
            </tr>
          </thead>
          <tbody>
            {% for item in items_semana_actual %}
```

(el resto del `<tbody>` no cambia -- sigue iterando `item.dia`, `item.ejercicio_nombre_snapshot`, etc., solo cambió la fuente del loop).

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python manage.py test tenants.tests.HomeViewAlumnoTests -v 2`
Expected: PASS (toda la clase, incluidas `test_alumno_ve_rutina_pago_y_novedad`, `test_alumno_sin_rutina_ve_mensaje_no_tecnico`, etc. -- regresión).

- [ ] **Step 5: Commit**

```bash
git add tenants/views.py templates/tenants/home.html tenants/tests.py
git commit -m "feat(portal): el alumno ve solo los ejercicios de su semana actual"
```

---

### Task 7: Verificación final

**Files:** ninguno nuevo — solo comandos de verificación sobre todo lo anterior.

- [ ] **Step 1: Confirmar que no falta ninguna migración**

Run: `python manage.py makemigrations --check --dry-run`
Expected: `No changes detected` (la migración de Task 1 ya cubre todo el cambio de modelo).

- [ ] **Step 2: Correr la suite completa**

Run: `python manage.py test -v 2`
Expected: todos los tests en verde, incluidos los de `rutinas` y `tenants` tocados en este plan.

- [ ] **Step 3: Chequeo manual rápido**

Run: `python manage.py runserver`, loguearse como staff, entrar a una plantilla, agregar un ejercicio en semana 2, verificar que la columna "Semana" se ve en `plantilla_detail` y que el admin (`/admin/rutinas/rutinaplantillaitem/`) lista la columna `semana`. Loguearse como un alumno con rutina asignada y confirmar que el portal muestra "Semana N de 4" y solo esos ejercicios.

- [ ] **Step 4: Commit final (si quedó algo suelto)**

```bash
git status
# Si hay cambios sin commitear (no debería, cada tarea ya commiteó):
git add -A
git commit -m "chore(rutinas): verificación final de la progresión semanal"
```
