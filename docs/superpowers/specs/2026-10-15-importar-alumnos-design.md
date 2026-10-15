# Importar alumnos desde Excel/CSV y acciones en lote — diseño

Aprobado por el dueño del producto el 2026-10-15. Plan de ejecución en
`docs/superpowers/plans/2026-10-15-importar-alumnos.md`.

## Para qué

Un gimnasio que llega con su padrón en una planilla (cientos de fichas) hoy lo
carga de a un alumno. Se agregan tres cosas:

1. **Importar alumnos** desde `.xlsx` o `.csv` en la sección Alumnos, calcado
   del importador de biblioteca (subir → previsualizar → confirmar), con una
   explicación del formato y un archivo de ejemplo generado al vuelo. **No se
   inventan datos ni se pierden en silencio.**
2. **La fecha de importación NO define el ciclo de cobro.** Un importado sin
   la columna «Inicio del ciclo» queda con `fecha_inicio_ciclo = None` («Sin
   fecha de inicio»): `pagos.generar_pagos_pendientes` lo saltea (filtra
   `fecha_inicio_ciclo__isnull=False`) y `pagos.acceso.bloqueo_de` nunca lo
   bloquea. El staff le fija la fecha después.
3. **Listado de alumnos** con buscador, selección múltiple y acciones en lote:
   fijar fecha de inicio, dar de baja, reactivar, generar códigos de ingreso.

## Decisiones del dueño del producto

- Fijar fecha en lote: los alumnos que ya tienen cuotas emitidas se saltean y
  se nombran en el mensaje (mover el ancla con cuotas solapa períodos).
- Rango de fecha de inicio (lote, ficha de un alumno sin cuotas y columna del
  archivo): **[hoy − 27, hoy + 60]**.
- Duplicados al importar: por email o teléfono; si a la fila o al alumno ya
  cargado le falta contacto, por nombre (ver regla exacta abajo).
- La importación de alumnos está **bloqueada en la cuenta demo** (`es_demo`):
  es compartida y un padrón real con datos de salud quedaría a la vista de
  otros prospectos hasta 6 h.
- Columnas opcionales **«Inicio del ciclo»** y **«Estado»** en el archivo.
- Se acepta **`.csv`** además de `.xlsx`.

## Hallazgos del código que el diseño corrige

- `alumnos/signals.py::_reanclar_ciclo_al_reactivar`: con ancla `None`,
  `ciclo_vigente` devuelve `None` y la función estampa hoy → reactivar a un
  importado empezaría a cobrarle. Con ancla futura pasa lo mismo (afecta
  también a alumnos anclados por `pagos/0006`). **Arreglo:** si el ancla es
  `None` o futura (`> hoy`), no hace nada.
- El `pre_save` de alta (`registrar_fecha_de_baja`) ancla en hoy si viene
  `None` → el import usa `bulk_create` (no dispara señales). Por eso el import
  completa a mano lo que haría el alta: sexo implícito si el gimnasio no es
  mixto; `fecha_baja = None`.
- «Altas del mes» y el gráfico de altas cuentan por `creado`
  (`tenants/analitica.py`) → importar el padrón los inflaría. **Arreglo:**
  `Alumno.importado` (`BooleanField(default=False, editable=False)`) y las
  ALTAS excluyen `importado=True`. Las bajas cuentan igual.
- Baja/reactivación en lote **nunca** con `QuerySet.update()`: `save(update_fields=
  ["estado","fecha_baja","fecha_inicio_ciclo","modificado"])` por alumno, como
  `AlumnoToggleEstadoView`, para que corran `registrar_fecha_de_baja` y
  `sincronizar_acceso_con_estado` (corta `User.is_active`).
- `anclar_ciclo_a_la_primera_rutina` ancla en la fecha de la primera rutina a
  un alumno sin cuotas: se acepta (comportamiento estándar), se documenta en el
  `help_text` y en la confirmación.

## Lectura del archivo

### Encabezados (`ALIAS_ALUMNOS`, `importaciones/parsing/alumnos.py`)

Ningún alias de una sola letra. El orden del dict importa
(`detectar_columnas` resuelve el prefijo en ese orden): `nombre_completo` antes
que `nombre`; cada `*_detalle` antes que su `tiene_*`; `frecuencia` antes que
`actividad`.

| Campo | Alias |
|---|---|
| `nombre_completo` | nombre y apellido, nombre completo, apellido y nombre, apellidos y nombres, nombres y apellidos, alumno, alumna |
| `nombre` | nombre, nombres, first name |
| `apellido` | apellido, apellidos, last name, surname |
| `email` | email, e-mail, e mail, mail, correo, correo electronico |
| `telefono` | telefono, tel, celular, cel, movil, whatsapp, wsp, numero de telefono |
| `fecha_nacimiento` | fecha de nacimiento, fecha nacimiento, fecha_nacimiento, nacimiento, fecha de nac, f nac, fec nac, cumpleanos |
| `inicio_ciclo` | inicio del ciclo, inicio ciclo, fecha_inicio_ciclo, inicio de ciclo, inicio del plan, inicio de plan, fecha de inicio, fecha inicio |
| `estado` | estado, situacion, activo |
| `sexo` | sexo, genero, sex, gender |
| `frecuencia_actividad_previa` | frecuencia_actividad_previa, frecuencia actividad previa, frecuencia de actividad, frecuencia |
| `actividad_fisica_previa` | actividad_fisica_previa, actividad fisica previa, hacia actividad fisica, actividad fisica |
| `deportes_practica` | deportes_practica, deportes que practica, deportes, deporte |
| `discapacidad_detalle` | discapacidad_detalle, detalle discapacidad, discapacidad detalle, cual discapacidad, tipo de discapacidad |
| `tiene_discapacidad` | tiene_discapacidad, tiene discapacidad, discapacidad |
| `enfermedad_cronica_detalle` | enfermedad_cronica_detalle, detalle enfermedad cronica, enfermedad cronica detalle, cual enfermedad |
| `tiene_enfermedad_cronica` | tiene_enfermedad_cronica, tiene enfermedad cronica, enfermedad cronica, enfermedades cronicas, patologia, patologias |
| `observaciones` | observaciones, observacion, obs, notas, nota, comentarios |

«Fecha de alta» **no** es alias de `inicio_ciclo` (es otra cosa: el día que se
anotó, quizá hace años). Si el implementador encuentra que un alias choca por
prefijo con otro campo (`detectar_columnas` hace dos pasadas: exacta y prefijo
con borde de palabra), corrige el orden o quita el alias conflictivo y lo
documenta con un test.

**Columnas obligatorias.** Primero `buscar_fila_encabezado(ws, ALIAS,
("nombre", "apellido"))`; si no, `("nombre_completo",)`; si no, `("nombre",)`
sola (se trata como nombre completo, ver partición). Si nada:
`ColumnaRequeridaFaltante("nombre y apellido", …)` con el eco de encabezados
que ya usa la biblioteca.

**Columnas no reconocidas**: los encabezados no vacíos sin campo van a
`columnas_ignoradas` y el preview los lista («Estas columnas no se importan»).

**Partición del nombre** (`partir_nombre_completo`): con coma, «Apellido,
Nombre». Sin coma, si el encabezado que matcheó empieza con «apellido» la
primera palabra es el apellido; si no, la última palabra es el apellido y el
resto el nombre. Cada fila partida lleva el aviso «Separé nombre y apellido:
revisalo». Una sola palabra → fila descartada, «Falta el apellido». Si la
columna `apellido` existe pero la celda está vacía y el nombre tiene ≥ 2
palabras, se aplica la misma partición con el mismo aviso.

### Valores

**Regla general («no inventar ni perder»).** Un dato opcional ilegible o que
no entra: el campo queda vacío (o en su default), la fila lleva un aviso, y el
texto original se agrega a `observaciones` con el formato
`Datos del archivo que no se pudieron cargar: Sexo «X»; Fecha de nacimiento «31/02/1990».`
(una sola línea con todos, al final de las observaciones existentes, separada
por un salto de línea).

Se descarta una fila SOLO si: falta nombre o apellido (y no se puede partir);
nombre o apellido superan `Alumno._meta` max_length (120); o es una fila del
ejemplo (`observaciones == MARCA_FILA_EJEMPLO`, chequeado ANTES de agregar nada
a observaciones).

- **Fechas** (`leer_fecha(valor, *, hoy)`): celda `datetime`/`date` →
  `.date()`. Número: 1900–2100 → año suelto, vacío con aviso «Parece solo el
  año»; otro número entre 1 y 2958465 → serie de Excel
  (`openpyxl.utils.datetime.from_excel`). Texto: `d/m/aaaa` día primero con
  separador `/`, `-` o `.`; año de 2 dígitos: `> hoy.year % 100` → 1900, si no
  2000; ISO `aaaa-mm-dd` con hora opcional. Fecha inexistente («31/02») → vacío
  con aviso. `hoy` lo pasa quien llama (`timezone.localdate()`); el lector no
  lo calcula.
- **Fecha de nacimiento**: además se rechaza (vacío + aviso) < 1900-01-01 o
  > hoy.
- **Inicio del ciclo** (`leer_inicio_ciclo`): `leer_fecha` + rango
  [hoy − 27, hoy + 60]; fuera de rango → vacío + aviso + texto a observaciones.
- **Estado** (`leer_estado`): activo/activa/alta/vigente/si/sí → ACTIVO;
  inactivo/inactiva/baja/de baja/dado de baja/dada de baja/no → INACTIVO;
  vacío → ACTIVO sin aviso; ilegible → ACTIVO + aviso + observaciones.
- **Booleanos** (`actividad_fisica_previa`, `tiene_*`): verdadero = sí, si, s,
  x, 1, true, verdadero, yes, y, ✓; falso = no, n, 0, false, falso, vacío, «-».
  Texto que empieza con «si/sí» + resto («Sí, asma») → verdadero y el resto va
  al `*_detalle` si está vacío (aviso). **Negativos → falso, sin detalle y sin
  aviso**: «ninguna», «ninguno», «n/a», «na», «—», y todo texto que empiece con
  «no » o «ningun» («No tiene», «No posee», «Ninguna conocida»). Es la regla de
  «no inventar»: una columna «Discapacidad» llena de «Ninguna» es el formato más
  común de una ficha real, y volverla un sí con «Ninguna» de detalle inventaría
  un dato de salud. En `tiene_*`, un texto que no es ni negativo ni afirmativo
  («Sordera», «Motriz») → verdadero y el texto va al `*_detalle` (aviso): cubre
  una sola columna «Discapacidad» con texto libre. En `actividad_fisica_previa`, texto no
  entendido → falso + aviso + observaciones; «Sí, fútbol» → verdadero y el resto
  pasa a `deportes_practica` si está vacío (aviso).
- **Todo lo que va a observaciones lleva aviso.** Y si hay columnas de nombre
  completo y de nombre/apellido, gana el nombre completo cuando matcheó EXACTO y
  nombre/apellido solo por prefijo («Alumno | Nombre del tutor»); la columna
  que no se usa queda en `columnas_ignoradas`. La «M» de sexo se decide sin
  contar la fila del ejemplo.
- **Implícitos** (con aviso): `*_detalle` con texto que no sea
  no/ninguna/ninguno/-/n/a/na y `tiene_*` vacío o sin columna → `tiene_* =
  True`. `frecuencia_actividad_previa` con valor y `actividad_fisica_previa`
  vacío o sin columna → `actividad_fisica_previa = True`.
- **Sexo**: masculino/hombre/varón/varon/masc → masculino; femenino/mujer/fem
  → femenino; «prefiere no decir»/«no decir»/nd → no_decir; «F» → femenino;
  «H» → masculino. «M» se decide mirando la columna entera: si hay F y no H, M
  = masculino; si hay H y no F, M = femenino (mujer); con las dos o ninguna, M
  es ambigua → vacío + aviso. Otro valor → vacío + aviso + observaciones.
- **Frecuencia**: etiquetas y valores de `Alumno.FrecuenciaActividad`;
  diario/todos los dias → diaria; varias/«2/3 veces por semana» →
  varias_por_semana; semanal/«una vez por semana»/«1 vez» → una_por_semana; a
  veces/esporadica/ocasional → ocasional; número suelto o «N veces/x por
  semana»: 1 → una_por_semana, 2–6 → varias_por_semana, 7 → diaria. Otro →
  vacío + aviso + observaciones.
- **Textos**: `str().strip()` con espacios internos colapsados; un `float` sin
  decimales (`1122334455.0`) pasa a `int` antes de `str`. Celdas combinadas con
  `_valor_celda` + `_mapa_merges`.
- **Email** (en services): `alumnos.identidad.normalizar_email`; inválido o
  más de uno → vacío + aviso + observaciones.
- **Teléfono**: se guarda tal como vino (igual que la ficha). > 30 caracteres
  → vacío + aviso + observaciones.
- **Largos** de opcionales: se leen de `Alumno._meta`; si no entra → regla
  general.
- **`observaciones == "[demo]"`** (`tenants.demo.MARCA`) se reescribe como
  `"[demo] (importado)"` con aviso, para que `sembrar_demo --borrar` nunca
  borre a un alumno real.

### Topes

`MAX_FILAS_ALUMNOS = 1000` filas con datos: más → rechazo del archivo entero
(«dividilo en partes»), no se trunca. 200 filas vacías seguidas → se deja de
leer con aviso «Dejé de leer en la fila X» (cubre un `max_row` inflado).
Tamaño máximo de archivo `MAX_BYTES_ARCHIVO_ALUMNOS = 5 MB` (validado en el
form).

### CSV

`libro_desde_csv(archivo)` arma un `openpyxl.Workbook` en memoria (mismo
adaptador que `parsing/pdf.py`) y se pasa a `leer_hoja_alumnos`. Codificación
`utf-8-sig` y, si falla, `cp1252`. Separador: `csv.Sniffer().sniff(muestra,
delimiters=",;\t")`; si falla, `;` si aparece en la primera línea, si no `,`.
Todo llega como texto. Otra codificación (UTF-16) → `ImportacionInvalida`
«Guardalo como .xlsx o como CSV UTF-8». `parsear_archivo_alumnos(archivo, *,
hoy)` despacha por extensión del nombre.

## Duplicados

Claves (`_ClavesDeAlumnos`, una sola query sobre los alumnos del gimnasio,
activos e inactivos):

- email en minúsculas;
- `identidad.clave_telefono(valor)`: normaliza a `+54…`, saca el `9` de
  celular y se queda con los 10 dígitos nacionales; si eso falla, los dígitos
  si son ≥ 6; si no, `None`. Solo para comparar: `normalizar_telefono` (login)
  no se toca;
- nombre: tokens de `normalizar_texto(nombre + " " + apellido)` ordenados (no
  depende de dónde se cortó entre nombre y apellido).

**Regla:** una fila es duplicado si (coincide email o teléfono **y** el nombre)
o (el nombre coincide **y** a alguno de los dos lados le falta contacto: la fila
no tiene email ni teléfono, o el alumno ya cargado —o la fila anterior del
archivo— no tiene ninguno de los dos). El segundo lado es el gimnasio que cargó
a mano solo el nombre y después importa el padrón con contactos: sin esa regla
la persona quedaba duplicada y se le cobraba dos veces. Con contacto en los dos
lados, contacto igual con nombre distinto → se crea, con aviso «Comparte
teléfono/email con Pérez, Juan» (padres e hijos comparten celular), y nombre
igual con contacto distinto → se crea, con aviso «Hay un alumno con el mismo
nombre (…): revisá que no sea el mismo». Dentro del archivo, misma regla; gana
la primera aparición. Los
duplicados se listan («Ya existe», con link a la ficha si es de la base) y no
se crean ni se actualizan.

## `Importacion.resultado` (tipo ALUMNOS)

```json
{
  "version": 1,
  "fila_encabezado": 3,
  "columnas_detectadas": {"nombre": "Nombre", "telefono": "Celular"},
  "columnas_ignoradas": ["Plan", "Monto"],
  "advertencias_columnas": ["…"],
  "a_crear": [{"fila_excel": 4, "datos": {"nombre": "Juan", "apellido": "Pérez",
      "fecha_nacimiento": "1990-05-03", "fecha_inicio_ciclo": null,
      "estado": "activo", "…": "…"}, "avisos": ["…"]}],
  "ya_existen": [{"fila_excel": 9, "nombre": "Ana", "apellido": "Gómez",
      "origen": "base", "alumno_id": 12, "motivo": "…"}],
  "filas_invalidas": [{"fila_excel": 7, "motivo": "Falta el apellido"}],
  "nacen_vencidas": 0,
  "total_filas_leidas": 120
}
```

Fechas como texto ISO (un `date` en `JSONField` da `TypeError`); al confirmar,
`date.fromisoformat`. Las claves de `datos` se filtran contra
`CAMPOS_IMPORTABLES` (los nombres de campo de `Alumno` que se importan) antes
de `Alumno(**datos)`. Tras confirmar o descartar, `resultado` queda
`{"version": 1, "resumen": {"creados", "ya_existian", "descartadas",
"salteados_al_confirmar"}}` y el archivo se borra en `on_commit` (try/except +
log; si el storage falla no da 500).

`nacen_vencidas`: cuántas filas a crear tienen `fecha_inicio_ciclo` anterior a
`hoy − gimnasio.dias_tolerancia_pago` (0 si la tolerancia está vacía): esas
cuotas nacen vencidas y el acceso se pausa esa noche. El preview lo avisa.

## Lote

Ids en UN campo oculto separado por comas (`LoteForm.ids`): con un checkbox
con `name` por fila, cientos de filas superan `DATA_UPLOAD_MAX_NUMBER_FIELDS` y
bajo `hx-boost` el 400 es un click mudo. Siempre se revalidan con
`Alumno.objects.for_gimnasio(g).filter(pk__in=ids)`.

- **Fijar fecha** (`MAX_LOTE_FECHA = 1000`): confirmación (fecha, default hoy,
  rango [hoy−27, hoy+60], error debajo del campo), quiénes cambian y quiénes se
  saltean por tener cuotas (una query con `Exists`), aviso de tolerancia («con
  una fecha anterior al {hoy − N} la primera cuota nace vencida y el acceso se
  pausa esa misma noche») y de primera rutina. Ejecuta UN `UPDATE` con
  `~Exists(Cuota…)` y `modificado` explícito. Mensaje «Fecha fijada a N; M se
  saltearon porque ya tienen cuotas: …» (hasta 10 nombres + «y N más»).
- **Dar de baja** (`MAX_LOTE_ESTADO = 200`): confirmación que avisa que pierden
  el acceso a la app y que su QR y su cara salen del padrón del lector (al
  reactivar hay que volver a enrolar la cara). `atomic` +
  `select_for_update(of=("self",))` + `.exclude(estado=destino)` + `save(
  update_fields=…)` por alumno.
- **Reactivar** (`MAX_LOTE_ESTADO`): directo, mismo mecanismo. Mensaje avisa
  cuántos siguen sin fecha de inicio.
- **Generar códigos**: directo, `ingreso.services.generar_credenciales_en_bloque(
  g, alumno_ids=ids)` (sin ids, igual que hoy).
- Ningún error de usuario devuelve 4xx: redirect con `messages` o render 200.
  `AlumnoLoteView` solo procesa POST; un GET redirige al listado.
- `filtros` (para volver al listado filtrado) solo con claves permitidas
  (`q`, `estado`, `sin_fecha`), re-codificado con `urlencode`.

## Riesgos aceptados (van a `ISSUES.md`)

- Fechas en texto siempre día primero; el preview muestra cómo quedó cada una.
- Una importación EN_REVISION abandonada guarda archivo y JSON con datos de
  salud (igual que los otros importadores; purga pendiente).
- `select_for_update(of=("self",))` no se prueba en SQLite: verificar contra
  Postgres antes del merge.
- La primera rutina ancla el ciclo de un importado sin fecha.
- Buscador y duplicados comparan el teléfono tal como se guardó.
- Datos opcionales ilegibles quedan en `observaciones`.
- Sin paginación del listado: deuda si un gimnasio pasa de ~1500 alumnos.
- Worker único: una baja de 200 alumnos tarda ~1,2 s.
- Un inactivo importado tiene `fecha_baja` vacía (la fecha real se desconoce):
  no aparece en el gráfico de bajas.
- CSV con otra codificación → error con instrucción.
