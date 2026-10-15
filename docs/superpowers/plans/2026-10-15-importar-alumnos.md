# Plan: importar alumnos (Excel/CSV) y acciones en lote

**Spec (autoridad):** `docs/superpowers/specs/2026-10-15-importar-alumnos-design.md`.
Toda regla de lectura, alias, duplicados, forma del JSON y lote está ahí: cada
tarea la cita por sección en vez de repetirla.

## Global Constraints

- Django 5.2, templates + CBV, sin DRF. Código, comentarios, mensajes y tests
  en castellano rioplatense (voseo), con la densidad de comentarios del código
  vecino.
- Suite: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test <app> --parallel 4`
  desde la raíz del worktree (hay un `.env` copiado; sin él 5 tests no
  herméticos dan rojo). Baseline: 2371 tests OK.
- TDD: test que falla → código → verde → commit. Un commit por tarea como
  mínimo; mensaje en castellano, terminado con
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Aislamiento por gimnasio: toda query de alumnos pasa por
  `Alumno.objects.for_gimnasio(gimnasio)`. Vistas de staff con
  `StaffRequiredMixin, TenantScopedMixin` (en ese orden).
- Fechas del dominio con `timezone.localdate()`, nunca `timezone.now().date()`;
  las funciones puras reciben `hoy` como parámetro.
- Nada de N+1: el costo en queries de previsualizar/confirmar/listar no crece
  con la cantidad de filas ni de alumnos (tests de escala comparan dos tamaños,
  no un número fijo).
- `QuerySet.update()` no dispara `auto_now` ni señales: `modificado` explícito.
  Baja/reactivación por `save(update_fields=["estado","fecha_baja",
  "fecha_inicio_ciclo","modificado"])` por alumno.
- `hx-boost="true"` es global: forms con archivo, links de descarga y links que
  dependan de `extra_style` llevan `hx-boost="false"`. Ningún error de usuario
  devuelve 4xx (bajo boost es un click mudo): redirect con `messages` o 200.
- Cada campo de form renderiza su error debajo del campo (`.errorlist` no
  tiene estilo; usar `partials/campo_form.html` o `.config-error`).
- `{# #}` es de una sola línea; para varias, `{% comment %}`.
- Si se toca `styles/input.css`, correr `npm run build:css` y commitear
  `static/css/app.css`.
- Largos de varchar validados en Python leyendo `Alumno._meta` (SQLite no los
  valida, Postgres sí).
- No se crea ningún `TenantOwnedModel` nuevo.

### Task 1: `clave_telefono` en `alumnos/identidad.py`

Agregar `clave_telefono(valor) -> str | None` según la spec, sección
«Duplicados». Django-free, testeado con `SimpleTestCase` en `alumnos/tests.py`
(o el archivo de tests de identidad que ya exista; buscá dónde se testea
`normalizar_telefono`).

Tests: `+54 9 11 2233-4455`, `11 2233-4455`, `011 15 2233-4455`,
`0054 11 2233 4455`, `1122334455` dan la misma clave; un número de EE.UU.
(`+1 415 555 0100`) se compara por sus dígitos; un número de menos de 6
dígitos o texto sin dígitos da `None`; `normalizar_telefono` no cambia (los
tests existentes siguen verdes).

### Task 2: lector `importaciones/parsing/alumnos.py` (+ CSV)

Módulo nuevo, Django-free (no importa `django` ni modelos), con
`ALIAS_ALUMNOS`, `leer_fecha`, `leer_inicio_ciclo`, `leer_estado`,
`leer_booleano`, `leer_sexo`/resolución de la convención de la columna,
`leer_frecuencia`, `partir_nombre_completo`, `MARCA_FILA_EJEMPLO`,
`MAX_FILAS_ALUMNOS`, `libro_desde_csv`, y `leer_hoja_alumnos(ws, *, hoy)`.
Todas las reglas: spec, secciones «Lectura del archivo» (Encabezados, Valores
—salvo email/teléfono/largos/`[demo]`, que son de la Task 5—, Topes, CSV).
Los valores de choices se escriben como strings literales (`"masculino"`,
`"varias_por_semana"`, `"activo"`), sin importar el modelo.

`leer_hoja_alumnos` devuelve un objeto/dataclass con: `fila_encabezado`,
`columnas_detectadas` (campo → texto del encabezado), `columnas_ignoradas`,
`advertencias_columnas`, `filas` (cada una con `fila_excel`, `datos` dict con
claves = nombres de campo de `Alumno` + `fecha_inicio_ciclo`, fechas como
`datetime.date`, y `avisos` lista de str, y `no_cargados` lista de str con los
«Campo «valor»» que van a observaciones), `filas_invalidas` (`FilaInvalida`) y
`total_filas_leidas`. La concatenación final a `observaciones` puede hacerla
el lector o la Task 5; decidilo y documentalo en el docstring.

Reutilizar de `importaciones/parsing/comun.py`: `normalizar_texto`,
`detectar_columnas`, `buscar_fila_encabezado`, `mejor_encabezado_parcial`,
`ColumnaRequeridaFaltante`, `FilaInvalida`, `_valor_celda`, `_mapa_merges`,
`_fila_vacia`. Molde de recorrido: `importaciones/parsing/tabular.py::
leer_hoja_biblioteca`. Molde de workbook en memoria: `importaciones/parsing/pdf.py`.

En `importaciones/parsing/__init__.py` (fachada sin lógica, salvo el despacho
existente) re-exportar lo público y agregar `parsear_archivo_alumnos(archivo,
*, hoy)`: `.csv` → `libro_desde_csv`, si no `openpyxl.load_workbook(archivo,
data_only=True)`; primera hoja → `leer_hoja_alumnos`. Errores de
codificación de CSV levantan una excepción del parsing (Django-free) que la
Task 5 traduce a `ImportacionInvalida`.

Tests (`SimpleTestCase`, en `importaciones/tests_alumnos_parsing.py`), con
workbooks armados en memoria (`openpyxl.Workbook()` + `ws.append`):
- fechas: celda `datetime`, número de serie, `"03/05/1990"`, `"3-5-90"`,
  `"31/02/1990"` (aviso), `1990` suelto (aviso), futura respecto de un `hoy`
  fijo (aviso), ISO con hora;
- inicio del ciclo dentro y fuera de [hoy−27, hoy+60] (fuera: vacío + aviso +
  no_cargados);
- estado: activo/baja/vacío/ilegible;
- booleanos: tabla completa; «Sí, asma» → detalle; detalle sin `tiene_*` lo
  infiere; «ninguna» no infiere; frecuencia con valor infiere actividad previa;
- sexo: convención F/M, H/M, M ambigua, «X»;
- frecuencia: etiquetas, valores, «3 veces por semana», ilegible;
- encabezados: título arriba de la tabla; «Nombre y apellido» no lo toma
  `nombre`; «Apellido y nombre» invierte; solo «Nombre» → se parte con aviso;
  `columnas_ignoradas`; sin columnas → `ColumnaRequeridaFaltante`;
  «Fecha de alta» queda ignorada (no es `inicio_ciclo`);
- partición con coma y sin coma; apellido vacío de una palabra → descarta;
- topes: > `MAX_FILAS_ALUMNOS` levanta; 200 vacías seguidas cortan con aviso;
- celdas combinadas; `1122334455.0` → `"1122334455"`;
- ningún alias de una letra (recorre `ALIAS_ALUMNOS`);
- sincronía: los valores devueltos ∈ `Alumno.Sexo.values`,
  `Alumno.FrecuenciaActividad.values`, `Alumno.Estado.values` (este test sí
  importa el modelo; va en `importaciones/tests.py` o en un `TestCase`);
- CSV: separador `;` y `,`, `utf-8-sig` con BOM, `cp1252` con tildes, mismo
  resultado que el `.xlsx` equivalente, UTF-16 levanta;
- la fachada `importaciones.parsing` exporta `parsear_archivo_alumnos` y
  `ALIAS_ALUMNOS`.

### Task 3: no re-anclar al reactivar un ancla `None` o futura

`alumnos/signals.py::_reanclar_ciclo_al_reactivar`: si
`instance.fecha_inicio_ciclo is None or instance.fecha_inicio_ciclo > hoy`,
return sin tocar. Actualizar su docstring con el porqué (spec, «Hallazgos»).

Tests en `pagos/tests.py`, junto a los de re-anclaje existentes (buscá
`test_reactivar` / `reancla`): reactivar con ancla `None` la deja `None`;
reactivar con ancla futura la deja intacta; los tests de re-anclaje existentes
siguen verdes. Usar el botón real (`alumnos:activar`) en al menos uno.

### Task 4: `Alumno.importado` y altas en la analítica

- `alumnos/models.py`: `importado = models.BooleanField(default=False,
  editable=False, help_text=...)` y actualizar el `help_text` de
  `fecha_inicio_ciclo`: «Día a partir del cual se cuentan los ciclos de cobro.
  Si el alumno vino de una importación y todavía no la tiene, no se le emite
  ninguna cuota hasta que se la fijes. Se completa sola con la primera rutina
  si el alumno todavía no tiene cuotas.» Migración `alumnos/000N_…`.
- `importaciones/models.py`: `Importacion.Tipo.ALUMNOS = "alumnos", "Alumnos"`
  + migración `importaciones/0003_…`.
- `tenants/analitica.py`: en `altas_y_bajas_por_mes` y `numeros_de_hoy` las
  ALTAS excluyen `importado=True`; las bajas no cambian.

Tests (`tenants/tests.py`, junto a los de esas funciones): un importado no
cuenta en altas del mes ni en el gráfico; su baja (por `save()` real) sí
cuenta. Verificar que `tenants/exportacion.py` no necesita cambios (lista
columnas explícitas) y que los tests de cobertura de demo/exportación siguen
verdes.

### Task 5: servicio de previsualización

Módulo nuevo `importaciones/alumnos.py` con
`previsualizar_importacion_alumnos(*, gimnasio, archivo, usuario)` y la clase
`_ClavesDeAlumnos`. Reutiliza `ImportacionInvalida`/`ERRORES_ARCHIVO_INVALIDO`
de `importaciones/services.py` y el manejo de `ColumnaRequeridaFaltante` de
`previsualizar_importacion_biblioteca` (molde). Usa
`parsear_archivo_alumnos(archivo, hoy=timezone.localdate())`.

Hace (spec: «Valores» —email, teléfono, largos, `[demo]`—, «Duplicados»,
«`Importacion.resultado`»):
- email (`alumnos.identidad.normalizar_email`), teléfono > 30, largos de
  opcionales desde `Alumno._meta` → regla general; nombre/apellido > 120 →
  descarta;
- `[demo]` reescrito; fila de ejemplo descartada;
- duplicados contra la base (UNA query de identidades del gimnasio) y dentro
  del archivo, con la regla exacta de la spec (contacto Y nombre; contacto
  igual con otro nombre se crea con aviso);
- `nacen_vencidas` según `gimnasio.dias_tolerancia_pago`;
- arma el JSON de la spec (fechas ISO; `CAMPOS_IMPORTABLES`) y crea
  `Importacion(tipo=ALUMNOS, estado=EN_REVISION, archivo=archivo,
  creado_por=usuario)`.
- En `gimnasio.es_demo` levanta `ImportacionInvalida` con un texto que explica
  que en la cuenta de prueba no se importan alumnos (es compartida).

Tests (`importaciones/tests_alumnos.py`, `TestCase`; helper de xlsx como
`importaciones/tests.py::_archivo_xlsx`): duplicado por email en mayúsculas,
teléfono con y sin 9, alumno inactivo, nombre sin contacto, dentro del
archivo; **hermano con el mismo teléfono y otro nombre se crea con aviso**;
nombre igual con otro contacto se crea con aviso; **aislamiento**: un alumno de
otro gimnasio con el mismo email no marca duplicado; nombre de 121 → descarta;
teléfono de 31 → vacío + aviso + observaciones; email inválido; `[demo]`;
fila de ejemplo; fechas ISO en el JSON; `nacen_vencidas` con y sin tolerancia;
demo rechazada. **Escala:** queries iguales con 20 y 200 filas, y con 5 y 50
alumnos existentes (`CaptureQueriesContext`, tolerancia ≤ 2; molde
`ImportacionBibliotecaEscalaTests`).

### Task 6: servicio de confirmación

En `importaciones/alumnos.py`: `confirmar_importacion_alumnos(*, importacion,
gimnasio) -> dict` (resumen). Molde: `confirmar_importacion_biblioteca`.

- `transaction.atomic()`; `select_for_update()` sobre la `Importacion`
  (verifica gimnasio y estado EN_REVISION, si no `ImportacionInvalida`) y sobre
  la fila del `Gimnasio` (serializa importaciones paralelas del mismo gimnasio).
- Recarga `_ClavesDeAlumnos` y saltea las filas que ahora son duplicado
  (`salteados_al_confirmar`).
- `Alumno.objects.bulk_create(..., batch_size=500)` con `gimnasio`,
  `importado=True`, `fecha_inicio_ciclo` del JSON o `None`, `estado` del JSON o
  ACTIVO, `fecha_baja=None`, sexo implícito (`gimnasio.sexo_implicito()`) si el
  gimnasio no es mixto (`gimnasio.tiene_publico_mixto`) y el sexo vino vacío.
- Marca CONFIRMADA, `confirmado_en`, achica `resultado` al resumen y en
  `transaction.on_commit` borra el archivo (`importacion.archivo.delete(
  save=False)` en try/except con `logger.warning`).
- Devuelve el resumen.

Tests: crea ACTIVOS con ancla `None`, `fecha_baja None`, `importado=True`; con
columnas, ancla y estado del archivo; un inactivo importado no cuenta en bajas
del mes; `pagos.models.generar_pagos_pendientes(hoy)` no emite nada para los
sin ancla y `pagos.acceso.bloqueo_de` da `None`; gimnasio no mixto completa el
sexo; segunda confirmación levanta; alumno creado entre preview y confirm se
saltea y se cuenta; importación de otro gimnasio rechazada; resultado achicado
y archivo borrado (`captureOnCommitCallbacks(execute=True)`); falla del storage
no propaga. **Escala:** confirmar 20 y 200 filas cuesta lo mismo (≤ chico + 5).

### Task 7: archivo de ejemplo

`importaciones/alumnos.py::construir_ejemplo_alumnos()` → `Workbook` al vuelo
(nunca un binario versionado), molde `services.construir_ejemplo_plantillas`:
- hoja «Alumnos»: encabezados legibles (Nombre, Apellido, Email, Teléfono,
  Fecha de nacimiento, Sexo, Estado, Inicio del ciclo, Actividad física previa,
  Frecuencia de actividad, Deportes que practica, Tiene discapacidad,
  Detalle discapacidad, Tiene enfermedad crónica, Detalle enfermedad crónica,
  Observaciones) y dos filas de ejemplo con `MARCA_FILA_EJEMPLO` en
  Observaciones;
- hoja «Cómo llenarlo»: obligatorias (nombre y apellido, o una columna con el
  nombre completo), valores aceptados por columna, fechas `dd/mm/aaaa`, que
  «Inicio del ciclo» acepta de 27 días atrás a 60 adelante y que sin ella el
  alumno no paga hasta que se le fije, que «Estado» vacío = activo, regla de
  duplicados, que se acepta `.csv`, y los alias derivados de `ALIAS_ALUMNOS`.

Vista `EjemploAlumnosView` (molde `EjemploPlantillasView`), ruta
`importaciones:alumnos_ejemplo` (`alumnos/ejemplo.xlsx`), filename
`ejemplo-alumnos.xlsx`.

Tests: la vista responde el xlsx; cada encabezado normalizado está en la lista
de alias de su campo; pasar el ejemplo por `previsualizar_importacion_alumnos`
detecta todas las columnas, crea 0 y descarta 2 filas como «fila de ejemplo».

### Task 8: vistas, URLs y templates de importación

- `importaciones/forms.py`: `SubirAlumnosForm` (`FileExtensionValidator(
  ["xlsx","csv"])`, `accept=".xlsx,.csv"`, tope `MAX_BYTES_ARCHIVO_ALUMNOS` con
  error de campo).
- `importaciones/views.py`: `SubirAlumnosView` (FormView, molde
  `SubirBibliotecaView`; en `es_demo` muestra la explicación y el POST da 200
  con el error), `PreviewAlumnosView` (GET muestra; POST confirma y redirige a
  `alumnos:listado` + `?sin_fecha=1` si quedaron alumnos sin fecha, con
  `messages.success` que explica el próximo paso; `get_importacion` filtra
  `for_gimnasio` + `tipo=ALUMNOS` + pk SIN filtrar estado: si ya está
  CONFIRMADA/DESCARTADA redirige al listado con `messages.info`; un
  `ImportacionInvalida` se muestra con `messages.error` y render 200;
  `never_cache`), rama ALUMNOS en `DescartarImportacionView` (redirige a
  `importaciones:alumnos_subir` y achica `resultado`).
- `importaciones/urls.py`: `alumnos/ejemplo.xlsx` (si no lo hizo la Task 7),
  `alumnos/subir/`, `alumnos/<int:pk>/preview/`, `alumnos/<int:pk>/descartar/`.
- Templates `templates/importaciones/alumnos_subir.html` (explicación del
  formato en castellano llano: columnas obligatorias y opcionales, que entran
  activos, sin fecha de inicio salvo la columna, sin cobro y sin acceso a la
  app, que se acepta .xlsx y .csv; link al ejemplo con `hx-boost="false"`;
  form con `hx-boost="false"`) y `alumnos_preview.html` (resumen; avisos de
  columnas; aviso ámbar de `nacen_vencidas`; tablas a crear —con la fecha de
  nacimiento e inicio de ciclo LEÍDAS y los avisos por fila—, ya existen —con
  link a la ficha `hx-boost="false"`— y descartadas; columnas ignoradas; botón
  confirmar que se deshabilita al enviar; botón descartar). Molde visual:
  `templates/importaciones/biblioteca_subir.html` y `biblioteca_preview.html`.
- Entrada: botón `.boton-secundario` «Importar desde Excel» en `.acciones-lista`
  de `templates/alumnos/alumno_list.html`. El nav no cambia.

Tests (`importaciones/tests_alumnos.py`): subir redirige al preview; archivo
inválido → 200 con error visible; `hx-boost="false"` en el form y en el link
al ejemplo; demo bloqueada; preview de otro gimnasio → 404; usuario alumno →
403; POST sobre una ya confirmada redirige con mensaje; descartar redirige a
subir; el listado muestra el botón.

### Task 9: listado con buscador, filtro y barra de selección

`alumnos/views.py::AlumnoListView` y `templates/alumnos/alumno_list.html`:
- `q`: partido en palabras; cada palabra tiene que matchear (AND) contra
  `nombre`, `apellido`, `email` o `telefono` (OR, `icontains`). Molde
  `ejercicios/views.py`.
- `sin_fecha=1` → `fecha_inicio_ciclo__isnull=True`, combinable con `estado`.
- UN solo form GET con `q`, `estado`, `sin_fecha`.
- Contexto: `activos_sin_inicio` (count sobre TODO el gimnasio, activos y sin
  fecha), `filtros_qs` (solo `q`/`estado`/`sin_fecha`, con `urlencode`), y los
  topes de lote.
- Columna «Inicio del ciclo» (`d/m/Y` o badge «Sin fecha de inicio»).
- Aviso arriba si `activos_sin_inicio`: «N alumnos activos sin fecha de inicio:
  no se les cobra. Ver cuáles» (link a `?sin_fecha=1&estado=activo`).
- Checkbox por fila `.js-lote` con `data-id`, SIN `name`; «Seleccionar todos
  (visibles)» `#lote-todos`; contador `#lote-conteo` con `aria-live`; barra
  `.lote-acciones` con `hidden` hasta que corre el JS; form POST a
  `alumnos:lote` con hidden `ids` y `filtros`; cuatro botones `name="accion"`
  (`fijar_fecha`, `dar_de_baja`, `reactivar`, `generar_codigos`). El JS llena
  `ids` al enviar, arranca el contador desde los `:checked` (el historial de
  htmx los restaura) y no deja enviar sin selección. Molde JS:
  `templates/importaciones/biblioteca_preview.html` (`.js-lote`, `#lote-todos`).
  Sin forms anidados. Si hace falta CSS nuevo, en `styles/input.css` + build.
- La URL `alumnos:lote` puede no existir todavía: en esta tarea agregá la ruta
  con una vista mínima que redirige al listado, o hacé esta tarea después de
  la 10; decidilo.

Tests: `q` con varias palabras, por email, por teléfono; `sin_fecha` con
`estado`; columna; aviso cuenta solo activos de todo el gimnasio; el form
mantiene `q`; markup de la barra (`hidden`, `.js-lote` sin `name`, `#lote-todos`,
hidden `ids`). **Escala:** 5 contra 50 alumnos, mismas queries.

### Task 10: acciones en lote

- `alumnos/lote.py`: `MAX_LOTE_FECHA = 1000`, `MAX_LOTE_ESTADO = 200`,
  `DIAS_MAX_INICIO_PASADO = 27`, `DIAS_MAX_INICIO_FUTURO = 60`;
  `fijar_fecha_inicio(gimnasio, ids, fecha) -> (cambiados, salteados)` (UN
  `UPDATE` con `~Exists(Cuota.objects.filter(alumno=OuterRef("pk")))` y
  `modificado=timezone.now()`; los salteados en una query); `cambiar_estado(
  gimnasio, ids, estado) -> int` (`atomic` + `select_for_update(of=("self",))`
  + `select_related("perfil__usuario")` + `.exclude(estado=estado)` + `save(
  update_fields=["estado","fecha_baja","fecha_inicio_ciclo","modificado"])`).
- `alumnos/forms.py::LoteForm`: `accion` (choices), `ids` (string, parseo a
  lista de enteros únicos; vacía o inválida → error), `fecha` (DateField,
  validada en [hoy−27, hoy+60]), `confirmado` (bool), `filtros`.
- `alumnos/views.py::AlumnoLoteView` (`StaffRequiredMixin, TenantScopedMixin`,
  GET → redirect al listado), ruta `alumnos/lote/` (antes de las `<int:pk>`),
  template `templates/alumnos/lote_confirmar.html`. Comportamiento y mensajes:
  spec, sección «Lote». La confirmación de fijar fecha lista quiénes cambian y
  quiénes se saltean (una query con `annotate(tiene_cuotas=Exists(...))`) y
  muestra los avisos de tolerancia y primera rutina; la de baja, el aviso del
  acceso y del padrón del lector. Mensajes con hasta 10 nombres + «y N más».
  Vuelve al listado con `filtros` re-codificado.
- `ingreso/services.py::generar_credenciales_en_bloque(gimnasio,
  alumno_ids=None)`: con ids, filtra además `pk__in`. La vista existente
  `ingreso:credenciales_en_bloque` sigue igual.

Tests (`alumnos/tests_lote.py`): fijar fecha: rango (`hoy+61` y `hoy-28` →
200 con error; `hoy+60` válido), salteados con cuotas nombrados, fecha futura
no emite cuotas con `generar_pagos_pendientes`, `modificado` cambia, ids de
otro gimnasio no se tocan, reloj congelado a las 23:30 en una fecha lejana
(`patch("django.utils.timezone.now")`); baja: pide confirmación sin cambiar
nada, después `fecha_baja` + `User.is_active=False`, doble POST no borra
`fecha_baja`; reactivar: limpia `fecha_baja`, importado sigue con ancla `None`,
ancla vieja re-anclada, acceso devuelto; códigos: solo seleccionados y activos,
el botón global sin cambios (test sin `alumno_ids`); tope excedido → redirect
con mensaje; GET → redirect; `filtros` con `//evil` o claves desconocidas se
re-codifican. **Escala de la baja:** 10 contra 40 alumnos con perfil, a lo
sumo 3 queries por alumno.

### Task 11: ficha, dashboard y `AlumnoForm`

- `templates/alumnos/alumno_detail.html`: línea «Inicio del ciclo de pago:
  d/m/Y» o «Sin fecha de inicio — no se le cobra hasta que se la fijes» con
  link a editar.
- `tenants/views.py::_metricas_dashboard`: `alumnos_activos_sin_inicio_count`
  (una query); `templates/tenants/home.html`: aviso con link a
  `alumnos:listado?sin_fecha=1&estado=activo` cuando es > 0.
- `alumnos/forms.py::AlumnoForm.clean_fecha_inicio_ciclo`: sigue obligatoria;
  permite fecha futura de hasta 60 días si el alumno es nuevo o no tiene
  cuotas (`self.instance.pagos.exists()`, una query); más de 60 o futura con
  cuotas → error como hoy. Un valor ya guardado sigue pasando siempre.

Tests: ficha con y sin fecha; dashboard muestra el aviso y su conteo (una
query más, no N); form sobre un importado sin fecha → error; futura +30 sin
cuotas válida; +61 inválida; futura con cuotas inválida;
`AnclaFuturaEnLaFichaTests` y los tests existentes del form verdes.

### Task 12: documentación

- `ARQUITECTURA.md`: subsección «Importar alumnos» dentro de «Importador de
  Excel (Proyecto 2)» (flujo, regla «no inventar ni perder», duplicados con la
  excepción de las familias, por qué `bulk_create` y qué completa a mano, demo
  bloqueada, CSV); en «Apps de dominio» → `pagos`, el estado «Sin fecha de
  inicio» y el arreglo de `_reanclar_ciclo_al_reactivar`; sección nueva o
  subsección «Acciones en lote del listado de alumnos» (ids en un campo, por
  qué `save()` por alumno, topes, salteados con cuotas, rango de fechas).
- `ISSUES.md`: entrada `[2026-10-15]` con los riesgos aceptados de la spec.
- `CLAUDE.md`: fila en la tabla «Si vas a tocar…» (importar alumnos / acciones
  en lote) apuntando a las secciones nuevas.
