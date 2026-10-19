# ARQUITECTURA.md

Detalle de diseño de `app_gim`, sección por sección: qué hace cada app, por qué
está armada así y las trampas que ya mordieron. **Salió de `CLAUDE.md` el
2026-10-15** para que ese archivo (que se carga en cada sesión) quede corto;
el texto se movió tal cual, sin reescribir. Las referencias viejas del tipo
«ver `CLAUDE.md` § X» en código, `ISSUES.md`, `ROADMAP.md` o specs apuntan
ahora a la sección homónima de ESTE archivo.

**Leé la sección del área que vas a tocar antes de escribir código.**

## Apps de dominio

- **`alumnos`** — `Alumno(TenantOwnedModel)`. Además de los datos de contacto
  de Fase 1, tiene una ficha de inscripción ampliada (agregada después de
  Fase 6, fuera del scope original) que el staff carga el día del alta:
  `sexo` y `frecuencia_actividad_previa` son catálogos cerrados (`TextChoices`,
  mismo criterio que `grupo_muscular` de `Ejercicio`); `deportes_practica`,
  `discapacidad_detalle` y `enfermedad_cronica_detalle` son texto libre a
  propósito (no amerita un catálogo cerrado). Todos `blank=True`/opcionales:
  no todo alumno cuenta todo el detalle en el momento, y los alumnos ya
  existentes antes de esta feature no lo tienen cargado. La edad sigue
  siendo `fecha_nacimiento` (ya existía) — no se agregó un campo `edad`
  aparte para no duplicar el dato y arriesgar que se desincronice.
- **`ejercicios`** — `Ejercicio(TenantOwnedModel)`, biblioteca por gimnasio
  (no global; ver docstring del módulo), y `CategoriaEjercicio(TenantOwnedModel)`,
  el catálogo con el que cada gimnasio agrupa esos ejercicios. Ver
  "Categorías de ejercicio por gimnasio" más abajo.
- **`rutinas`** — `RutinaPlantilla`/`RutinaPlantillaItem` (editable) y
  `RutinaAsignada`/`RutinaAsignadaItem` (snapshot congelado). La copia se
  hace con `RutinaAsignada.crear_desde_plantilla(...)` y
  `RutinaPlantilla.duplicar()` — ambas transaccionales. Los modelos "Item" NO
  son `TenantOwnedModel` (se acceden vía su padre, que ya está scopeado).
  **RPE** (agregado después de Fase 6): `RutinaAsignadaItem.rpe` es un
  `TextChoices` de 4 niveles ("Podría hacer más intenso" ... "Debería bajar
  la intensidad") que el propio alumno carga desde su portal (`home.html`,
  un `<select>` que se auto-envía a `rutinas:item_calificar`,
  `RutinaAsignadaItemCalificarView` con `AlumnoRequiredMixin`, mismo patrón
  que `NovedadMarcarLeidaView`) — solo puede calificar items de su rutina
  **activa** (una vieja/cerrada da 404, igual que un item de otro alumno). El
  staff lo ve de solo lectura en `rutinas:asignada_detalle`. **Riesgo
  aceptado a propósito**: como `RutinaAsignadaItem` no tiene FK viva a
  `Ejercicio` (es un snapshot, ver arriba), agregar el RPE por ejercicio para
  el dashboard del dueño va a tener que agrupar por `ejercicio_nombre_snapshot`
  (texto) — si un ejercicio se renombra en la biblioteca, el historial viejo
  de RPE no se fusiona con el nombre nuevo. Es consecuencia directa de que el
  RPE es una calificación por sesión/semana (lo que pidió el dueño del
  producto), no una opinión general y estable del ejercicio.
  **`bloque` y `dia_nombre`** (2026-08-31, los trae el importador desde la
  planilla del entrenador): `bloque` es el código de superserie ("A1", "B2" —
  los ejercicios del mismo bloque se hacen uno atrás del otro) y `dia_nombre`
  el título del día ("Tren superior · Core"). Están en los DOS modelos Item
  (plantilla y snapshot) y `dia_nombre` va **denormalizado por item, no como
  modelo `Dia`**: mismo patrón que `categoria_snapshot`, que ya repite un texto
  en todos los items y se resuelve al leer con la regla "gana la semana más
  baja" de `agrupacion.py`. Un modelo propio pedía migración de datos, FK en
  `crear_desde_plantilla` y cambio de forma en `dias_disponibles`
  (`tenants/views.py`) y en el agrupado del PDF, todo para una etiqueta. Costo
  aceptado: en el alta manual el texto se retipea por ejercicio.
  **`Meta.ordering` NO incluye `bloque`** — el importador ya asigna `orden` en
  el orden del archivo, así que A1, A2, B1 salen agrupados solos, y meterlo
  mandaría los items manuales (bloque vacío) al principio. Son cuatro los
  caminos de escritura que hay que tocar juntos si se agrega otro campo así:
  `crear_desde_plantilla`, `duplicar()`, `importaciones/services.py` (que lee
  con `.get(..., "")`, porque una `Importacion` EN_REVISION creada antes del
  deploy no tiene la clave en su JSON) y `RutinaPlantillaItemForm`.
  **Lista de ejercicios del día y PDF** (agregado después de Fase 6, y
  simplificado el 2026-08-24 a pedido de un cliente real): `rutinas/
  agrupacion.py::listar_ejercicios_del_dia()` es el único lugar que arma
  la lista de ejercicios de un día de una `RutinaAsignada` — la usan
  tanto el portal del alumno (`RutinaMiDiaDetailView` →
  `mi_dia_detalle.html`, un día por vez, con las 4 semanas lado a lado en
  columnas separadas por Series/Reps/Kilos/Descanso/Calificación desde el
  rediseño "tabla ancha por columna"; ver "El día del alumno en el celular"
  más abajo, que agrega una SEGUNDA forma para esa misma pantalla) como
  `rutinas/pdf.py::
  generar_pdf_rutina_asignada()` (fpdf2, Django-free a propósito, recorre
  todos los días). Hasta esa fecha la función dividía el resultado en
  secciones por `categoria_snapshot`; un cliente real la encontró
  confusa y se sacó esa subdivisión — ahora devuelve una lista PLANA
  (ordenada por `RutinaAsignadaItem.orden`), y cada ejercicio sigue
  trayendo su propio `categoria_display` (se calcula igual que
  antes) como subtítulo bajo el nombre, ya no como encabezado de
  sección. `RutinaAsignadaPdfView` (staff-only, botón "Descargar PDF" en
  `asignada_detail.html`) es el fallback en papel para cuando un alumno
  se queda sin acceso al portal — pensado para imprimir, no como
  documento de marketing. **Mantené el desglose de campos del PDF
  sincronizado con el de la tabla del portal**: el PDF original (commit
  `51239e5`) empaquetaba todo en una celda compacta tipo "3x12 · 20kg
  (hecho)", y quedó desactualizado cuando `d0de225` separó esas columnas
  en pantalla — se corrigió después para que `_celda_semana` liste
  Series/Repeticiones/Kilos/Descanso/Calificación (con
  `item.get_rpe_display()`, no un genérico "(hecho)") en líneas
  separadas, y cada fila lleve el grupo muscular como subtítulo bajo el
  nombre del ejercicio, igual que la tabla en pantalla — esa regla de
  sincronía se mantiene, aunque ya no haya secciones que sincronizar.
- **`pagos`** — `Cuota(TenantOwnedModel)` y `MedioCobro(TenantOwnedModel)`
  (alias/CBU/lo que el gimnasio muestra al alumno para pagar, editable por
  staff). **La cuota NO es mensual**: se llamaba `PagoMensual` hasta la
  migración a ciclos y hoy es un período con fechas (`periodo_inicio` /
  `periodo_fin`, este último INCLUSIVO) de `DIAS_CICLO = 28` días, anclado a
  `Alumno.fecha_inicio_ciclo` — el día en que ESE alumno empieza a entrenar.
  `related_name` sigue siendo `pagos`.
  **Consecuencia comercial asumida, no un efecto colateral:** 365/28 = 13,04
  cobros al año contra 12 meses, o sea ~8,6% más de facturación anual por
  alumno con el mismo monto. El panel y la analítica se siguen leyendo en
  clave mensual (`TruncMonth("periodo_inicio")`), así que una vez al año un
  mes muestra dos ciclos del mismo alumno.
  **`mes`/`anio` siguen en la base, `null=True` y sin uso.** No se borran: el
  `RemoveField` sería irreversible (no tienen `default`, así que el
  `database_backwards` emite `ADD COLUMN NOT NULL` sin default y falla), y dos
  columnas muertas cuestan infinitamente menos que un restore. `Cuota.save()`
  las mantiene llenas derivadas de `periodo_inicio`; los caminos en lote las
  setean a mano porque `bulk_create` no pasa por `save()`.
  `pagos/models.py` expone `ciclo_vigente(ancla, hoy)` (función pura, sin ORM),
  `generar_pagos_pendientes(hoy)` y `marcar_vencidos(hoy)`;
  `python manage.py generar_pagos` corre las dos — lo programa
  `.github/workflows/generar-pagos.yml`, que ahora pingea Healthchecks porque
  pasó a ser el único emisor de facturación Y el insumo del bloqueo.
  **`Estado.ANULADO`** es la salida del staff para condonar una cuota: no
  bloquea y no suma a `ingresos_por_mes`. Sin ese estado, destrabar a un
  becado o a alguien de licencia obligaba a marcar PAGADO, o sea a falsear la
  facturación del gimnasio. Se escribe desde `pagos:anular`
  (`CuotaAnularView`, con pantalla de confirmación; una PAGADA da 404, y una
  anulada vuelve a PENDIENTE desde el mismo botón) — durante un release el
  estado existió sin ninguna vista que lo setease, y el filtro «Anulada» del
  listado no podía matchear nada.
  **La fecha límite de pago vive en un solo lugar**: `limite_de_pago()` (pura)
  y su gemela en SQL `filtro_por_limite_de_pago()`, de las que dependen
  `marcar_vencidos`, el aviso «por vencer» del cron y el texto del push
  (`Cuota.fecha_limite_pago()`). `tolerancia_efectiva()` devuelve `None` para
  las cuotas anteriores a `fecha_activacion_bloqueo`, que siguen venciendo al
  terminar el ciclo. Hay un test que recorre 70 días × 5 tolerancias comparando
  las dos versiones: **si tocás una, la otra tiene que seguirla.**


  **De dónde sale `Alumno.fecha_inicio_ciclo`** (lo más delicado de todo esto):
  - **Alumnos que ya existían**: la migración `pagos/0006` los ancla en el
    **primer día no cubierto por su última cuota mensual** (`max(periodo_fin)
    + 1 día`). La versión intuitiva —anclar en la primera rutina— estaba mal
    para TODOS, no en un caso de borde: el ciclo vigente cae siempre en
    `[hoy-27, hoy]`, así que se solapaba siempre con la cuota calendario del
    mes en curso que el gimnasio ya cobró, nacía vencida en la misma corrida
    del cron y disparaba un push masivo de «tu cuota está vencida».
  - **Alumnos nuevos**: `pre_save` al alta, corregido por el `post_save` de
    la primera `RutinaAsignada` (solo si todavía no tiene ninguna cuota: mover
    el ancla con cuotas emitidas solapa períodos y vuelve a cobrar días ya
    cobrados, y el `unique_together` no lo impide porque solo prohíbe dos
    cuotas con el MISMO arranque).
  - **Al reactivar** (INACTIVO→ACTIVO), se re-ancla a hoy **solo si el ciclo
    vigente arrancó antes**: durante la baja no se emiten cuotas pero el ancla
    avanza sola, así que sin esto el que vuelve de tres meses recibe esa noche
    una cuota retroactiva ya vencida. La condición evita regalarle 27 días al
    que se dio de baja por error y volvió el mismo día. **Va en el `pre_save`,
    así que todo `save(update_fields=...)` sobre `estado` tiene que listar
    también `fecha_baja` y `fecha_inicio_ciclo`**: con `["estado"]` a secas
    Django calcula los dos valores y los tira, y así estuvo el botón de
    baja/alta (el único camino de UI) un release entero.

  - **Un ancla `None` o futura no se re-ancla al reactivar** (2026-10-15).
    `_reanclar_ciclo_al_reactivar` corta antes de llamar a `ciclo_vigente` si
    `fecha_inicio_ciclo is None or > hoy`. Con `None` (el alumno importado sin
    «Inicio del ciclo») `ciclo_vigente` devuelve `None` y la rama de abajo
    estampaba hoy: reactivar a un importado lo ponía a recibir cuotas esa misma
    noche. Con ancla futura pasaba lo mismo y además adelantaba el cobro (afecta
    también a los alumnos que la señal de la primera rutina dejó anclados a
    futuro). Lo fijan `pagos.tests` `test_reactivar_con_ancla_none_no_la_inventa`
    y `test_reactivar_con_ancla_futura_no_la_adelanta`.

  - El campo es **obligatorio en `AlumnoForm`** aunque sea `blank=True` en el
    modelo: vaciarlo desde la ficha dejaría al alumno sin recibir ninguna
    cuota, en silencio y para siempre. Y no acepta que el staff la MUEVA a una
    fecha futura, que apaga la facturación igual (`ciclo_vigente` no emite
    nada hasta que el ancla llega — un ancla futura además daba índice
    NEGATIVO, `(-6)//28 == -1`, y emitía una cuota por un período anterior al
    alta). El valor YA guardado pasa siempre aunque sea futuro: la señal de la
    primera rutina lo escribe legítimamente cuando el plan se carga con
    anticipación, y rechazarlo dejaba la ficha entera inguardable hasta ese
    día.



**«Sin fecha de inicio» es un estado legítimo, no un dato roto** (2026-10-15).
El importador de alumnos crea las fichas con `fecha_inicio_ciclo = None` salvo
que el archivo traiga la columna «Inicio del ciclo»: la fecha en que se importa
NO es el día en que el alumno empezó a entrenar, y anclar ahí le facturaría a
todo el padrón de golpe. Sin ancla, `generar_pagos_pendientes` lo saltea (filtra
`fecha_inicio_ciclo__isnull=False`) y `bloqueo_de` nunca lo bloquea: **un alumno
importado sin ancla no se cobra ni se bloquea hasta que el staff le fija la
fecha** (ficha, lote o la primera rutina, si todavía no tiene cuotas). Las
pantallas lo dicen: la ficha muestra el badge «Sin fecha de inicio» con un link
a «Fijar fecha», el listado tiene el filtro `sin_fecha` y el dashboard una
tarjeta `.aviso-urgente` con el conteo de activos sin fecha
(`alumnos_activos_sin_inicio_count`, una query). El rango válido,
`[hoy − 27, hoy + 60]`, vive en un solo lugar, `alumnos/ciclo.py::rango_inicio_ciclo`
(Django-free). **El lector del archivo y el lote lo aplican siempre**; el lote
además no le toca el ancla a quien ya tiene cuotas. **`AlumnoForm` lo aplica
solo al alumno SIN cuotas** (alta nueva o importado al que nunca se le cobró),
con un error que explica que una fecha más vieja haría nacer la primera cuota
vencida. **Con cuotas, la ficha conserva su regla de siempre**: hacia atrás se
puede corregir sin piso (arreglar un ancla mal puesta), a futuro no (solapa o
deja huecos de cobro), y el tope de 60 días vale igual. En los dos casos el
valor YA guardado pasa siempre (la señal de la primera rutina escribe anclas
futuras, y un alumno de hace meses tiene una vieja), y `pagos.exists()` solo se
consulta si la fecha sale de `[hoy − 27, hoy]`. Al importado sin ancla la ficha
le muestra arriba del campo, y como error de obligatorio, que vino de una
importación y que no ponga hoy si no corresponde (`forms.AVISO_SIN_INICIO`).
Si la fecha elegida (alta o edición, alumno activo, fecha cambiada) hace que la
cuota de esta noche ya pase la tolerancia (`pagos.acceso.nace_bloqueada`), la
vista lo avisa con un `messages.warning` y guarda igual: la fecha es válida.

### Bloqueo de acceso por falta de pago (`pagos/acceso.py`)

**Hay DOS bloqueos en el producto y NO tienen las mismas reglas.** Éste es el
del GIMNASIO contra su alumno que no pagó la cuota: busca cobrarle, así que
deja siempre abiertas las dos puertas que necesita para ponerse al día —
**cancelar una reserva y subir el comprobante NUNCA se bloquean**. El otro es
el de la PLATAFORMA contra el gimnasio que no paga la app
(`Gimnasio.estado_cuenta`, ver «Panel de plataforma» más abajo): ése SÍ corta
esas dos cosas, a propósito, porque es la palanca del dueño del producto y
tiene que apretar. Si tocás uno, no arrastres su criterio al otro.

`Gimnasio.dias_tolerancia_pago` (vacío = **no bloquear a nadie**, y es el
estado de todos los gimnasios existentes) define a los cuántos días de
arrancar un ciclo impago el alumno deja de ver su rutina y de poder reservar
turnos. `pagos/acceso.py` es el ÚNICO lugar donde vive esa regla —
`bloqueo_de(alumno)`, `cuotas_impagas_de(alumno)` y `contar_bloqueados(gimnasio)`.

- **`Gimnasio.fecha_activacion_bloqueo`**, estampada por `tenants/signals.py`
  en la transición apagado→prendido: `bloqueo_de` ignora las cuotas anteriores
  a esa fecha. Sin esto, el día que el dueño prende la función quedan
  bloqueados de golpe todos los que arrastren cualquier impaga histórica (el
  que estuvo de licencia, el que pagó en efectivo y nadie confirmó, el
  becado). Es literalmente «un alumno que pagó y se queda sin acceso»,
  multiplicado por el gimnasio entero.
- **La tolerancia se resuelve en Python, nunca como `periodo_inicio + columna`
  en el queryset**: esa aritmética anda en Postgres y da resultados
  silenciosamente distintos en SQLite, donde corre toda la suite. Como el
  gimnasio siempre se conoce en el punto de uso, es un escalar.
- **Vencer y bloquear usan el mismo umbral**, para que no haya un día en que
  el alumno esté bloqueado y la app le diga «Pendiente»: `acceso.py` bloquea
  con `periodo_inicio <= hoy - tolerancia`, y `marcar_vencidos` vence con
  `fecha_limite_pago < hoy`, que es la misma desigualdad (un `<` en vez de
  `<=` en el vencimiento producía exactamente ese día). Con la tolerancia
  vacía —o para las cuotas anteriores a `fecha_activacion_bloqueo`, a las que
  el bloqueo no alcanza— vence recién cuando el ciclo TERMINÓ (`hoy >
  periodo_fin`): es la traducción honesta del «vence cuando el mes cerró» de
  antes, y evita el incidente de pasar todo el padrón a VENCIDO el día del
  deploy, o el día que un dueño prende la tolerancia.
- **El aviso «por vencer» se mide contra la fecha límite, no contra el
  arranque del ciclo**: sale cuando `fecha_limite_pago` cae entre hoy y
  `DIAS_AVISO_PAGO` días, y el cuerpo dice cuántos días QUEDAN. Medirlo desde
  el arranque mandaba el push el día 1 («vence el 28») y, por el dedup por
  cuota, nada cerca del vencimiento real.

- **`nace_bloqueada(gimnasio, fecha_inicio_ciclo, hoy)`** (2026-10-15) responde
  «¿la cuota que el cron emitiría HOY con esta ancla nace bloqueando el
  acceso?». Es pura y está construida sobre las mismas piezas que `bloqueo_de`
  (`_umbral`, `tolerancia_efectiva`, `ciclo_vigente`): no es una copia de la
  regla. Se evalúa sobre el `periodo_inicio` REAL que emite `ciclo_vigente` (con
  un ancla de hace más de un ciclo, la cuota vigente arranca en el ciclo
  corriente) y con `<=`, igual que el bloqueo. La usan el preview del importador
  (`nacen_vencidas`) y la confirmación de «fijar fecha» en lote. Su primera
  versión comparaba con `<` y un test fijaba el error; el borde exacto lo cubre
  `NacenVencidasTests.test_borde_exacto_cuenta`.

- **Qué se bloquea**: el portal reemplaza la tarjeta de rutina por la del
  bloqueo, `RutinaMiDiaDetailView` renderiza `rutinas/bloqueado.html` **con
  200 y no 404** (esa URL queda en favoritos; un 404 diría que la app se
  rompió), y calificar RPE / marcar día entrenado / reservar turno dan 403.
  **Cancelar una reserva y subir el comprobante NUNCA se bloquean** — el
  alumno tiene que poder liberar el cupo y pagar.
- **Un 403 solo no alcanza**: bajo el `hx-boost` global es un click muerto sin
  ningún mensaje, así que los botones directamente no se renderizan con el
  acceso pausado. El 403 es la defensa del servidor, no la UX.
- **El portal lista TODAS las cuotas impagas** (`partials/cuotas_impagas.html`),
  no solo la del ciclo en curso: si bloquea una anterior y en pantalla solo
  aparece la actual, el alumno le sube el comprobante a la equivocada y sigue
  bloqueado sin entender por qué.
- **Subir el comprobante NO desbloquea** — decisión de producto: solo el staff
  marcando PAGADO. `ConfirmarPagoView` avisa si el alumno sigue bloqueado por
  otra cuota; «Pago confirmado» a secas sobre alguien que sigue sin acceso es
  una mentira que el staff descubre por el reclamo.
- **El contador del panel es una query agregada**, nunca `bloqueo_de` por fila:
  es exactamente el N+1 que este proyecto ya pagó con un 502 en producción.
- **`marcar_vencidos` escribe `modificado` explícitamente**: `QuerySet.update()`
  no dispara `auto_now` y `enviar_recordatorios` filtra por
  `modificado__date=hoy`, así que hasta ahora el push de «cuota vencida» no
  salía nunca. Arreglarlo implica que **la primera corrida post-deploy manda
  una ráfaga**: contar antes cuántas filas caen y hacer esa pasada con el push
  silenciado.

- **`novedades`** — `Novedad(TenantOwnedModel)` con `NovedadQuerySet.visibles()`
  (activa + publicada + no vencida), y `NovedadLeida` (read-receipt por
  alumno; no es `TenantOwnedModel`, se scopea vía su FK a `Novedad`/`Alumno`
  que ya está acotada) para el badge "Nueva" del portal y el conteo de
  lecturas que ve el staff.
- **`turnos`** y **`calendario`** — agenda de reservas con cupos y su
  integración opcional con Google Calendar; ver sección propia abajo.
- **`importaciones`** — importador de planes de entrenamiento y biblioteca de
  ejercicios desde Excel; ver "Importador de Excel (Proyecto 2)" abajo.
- **`ingreso`** — la puerta del gimnasio: lectores, credenciales (QR o id
  facial) y el registro de cada intento de pasar; ver "Control de ingreso"
  abajo.
- **`solicitudes`** — el embudo público de captación: un interesado deja una
  solicitud en `/solicitar/` y el dueño del producto la aprueba desde el
  panel. **Ninguno de sus modelos es `TenantOwnedModel`** (son datos DE la
  plataforma, no DE un gimnasio); ver "Sitio público y embudo de solicitudes"
  abajo.
- **`plataforma`** — no es una app de dominio del gimnasio y va última en
  `INSTALLED_APPS`: es el panel del dueño del producto, el único lugar donde
  se ven TODOS los gimnasios. Ver "Panel de plataforma" abajo.

## Editar la rutina asignada y el panel «Cómo viene el alumno»

Agregado el 2026-08-31 a pedido del primer cliente pago: el entrenador importa
una planilla base y después **la personaliza alumno por alumno** (kilos según
el nivel de cada uno, variantes de un ejercicio, sumar o sacar ejercicios). El
proyecto tenía CRUD completo de items para `RutinaPlantilla` y **nada** para
`RutinaAsignada`, que era de solo lectura para el staff.

- **`rutinas/services.py`** (nuevo) es el ÚNICO lugar que escribe sobre los
  items de una rutina asignada: `editar_ejercicio_asignado`,
  `agregar_ejercicio_asignado`, `quitar_ejercicio_asignado`. Las vistas no
  escriben (ninguna llama a `form.save()`).
- **Regla de propagación entre semanas, lo más importante de todo esto.** Un
  ejercicio existe hasta 4 veces (una por `semana`). `ejercicio_nombre_snapshot`
  y `ejercicio_video_snapshot` se aplican a **las 4 semanas**; `series`,
  `repeticiones`, `kilos`, `descanso`, `notas` y `bloque`, **solo a la semana
  editada** (la progresión semana a semana es justamente el punto). Agregar y
  quitar actúan sobre las 4.
  **El nombre propaga por integridad, no por comodidad**: `agrupacion.py`
  identifica "el mismo ejercicio entre semanas" agrupando por
  `ejercicio_nombre_snapshot`, así que renombrar una sola semana parte el
  ejercicio en dos filas distintas en el portal del alumno y en el PDF. Los
  "hermanos" se resuelven por `(rutina_asignada, dia,
  ejercicio_nombre_snapshot)` — **los tres campos importan** y hay un test por
  cada uno (sin `dia` se renombra el mismo ejercicio en los otros días; sin
  `rutina_asignada`, en las otras rutinas del alumno).
- **Dos trampas reales, cada una con test de regresión.** (1) El nombre viejo
  se lee de la BASE, no de la instancia: con un `UpdateView`,
  `ModelForm._post_clean` ya le escribió el nombre NUEVO encima, así que usarla
  hace que el UPDATE de los hermanos no matchee ninguna fila y el renombre
  quede aplicado a una sola semana. (2) `QuerySet.update()` **no dispara
  `auto_now`**, así que `modificado` (de `TimeStampedModel`) va explícito en
  los dos `update()`.
- **Consecuencia aceptada sobre la analítica del dueño**: `tenants/analitica.py`
  agrupa por `ejercicio_nombre_snapshot`. Hasta acá ese texto era inmutable;
  ahora es editable, así que renombrar mueve las calificaciones viejas de
  bucket y cambia el ranking del dashboard retroactivamente. No tiene arreglo
  limpio (una FK viva rompería el snapshot).
- **`rutinas/progreso.py`** (nuevo) es el diferenciador: pone frente al
  entrenador el feedback que el alumno ya venía dando y que **no se leía en
  ninguna vista de staff**. `RutinaAsignadaDiaCompletado` se escribía y se
  tiraba; el `rpe` solo aparecía entre ~128 filas planas o promediado a nivel
  gimnasio. Ahora `adherencia_de_rutina()` cruza días entrenados contra
  sesiones previstas (**acotado a `semana_actual`**: en la semana 2 de 4 la
  adherencia del ciclo completo no puede pasar del 50%, y leerla así sería
  acusar al alumno de algo que todavía no pasó), y `SENALES_POR_RPE` traduce
  los 4 valores del `TextChoices` a ↑ / = / ↓. **Es un dict de 4 entradas, no
  IA**: el ROADMAP veta "IA de rutinas" y esto no lo es. `anotar_senales` vive
  acá y **no** en `agrupacion.py` a propósito — esa función también alimenta el
  portal del alumno y el PDF, y al alumno se le muestra la etiqueta que él
  eligió, nunca una instrucción sobre su propio entrenamiento.
- **`asignada_detail.html` se reagrupó por día** (un ejercicio por fila, las 4
  semanas en columnas, reusando `listar_ejercicios_del_dia`). Antes era una
  tabla plana de ~128 filas; con "quitar" actuando sobre las 4 semanas, ese
  botón habría aparecido repetido en las 4 filas prometiendo borrar solo la
  suya. `agrupacion.py` expone `item_referencia` (el item de la semana más
  baja, que ya definía `orden` y `categoria_display`) para darle a la fila un
  pk estable.
- **Qué rutina ve el alumno lo decide `RutinaAsignada.vigente_de(alumno=...)`,
  por FECHA — nunca el flag `activa`.** Regla de producto: un plan dura 4
  semanas y el alumno lo ve completas aunque el profesor ya haya cargado el
  siguiente. Eso lo resuelve **"la más reciente cuya `fecha_inicio` ya
  llegó"**, sin comparar contra el fin del ciclo: mientras el viejo corre el
  nuevo no arrancó; al llegar su fecha toma el relevo; sin siguiente se queda
  el último; y un plan futuro no se adelanta. Los seis lugares que resolvían
  "la rutina del alumno" llaman a este método, así que no pueden divergir.
- **`vigente_de` no tiene fallback y `proxima_de` es una función aparte.** Es
  deliberado: si una sola función devolviera el plan programado cuando no hay
  vigente, todos los consumidores heredarían ese modo — incluidas las TRES
  escrituras del alumno, que lo dejarían marcar como entrenado y calificar un
  plan que no arrancó, ensuciando la adherencia y `tenants/analitica.py`.
  `proxima_de` solo alimenta carteles informativos.
- **`activa` significa "archivada a mano"**, no "la que ve el alumno". Su único
  camino de UI es el botón `asignada_archivar`. `crear_desde_plantilla` NO
  archiva la anterior (se probó lo contrario durante unas horas y le sacaba al
  alumno el plan en curso, ver `ISSUES.md`) ni escribe `fecha_fin`: el fin del
  ciclo se deriva con `fecha_fin_prevista` (**exclusiva**: `fecha_inicio + 28
  días`, el primer día NO cubierto; `ultimo_dia` resta uno para mostrar).
  Persistirlo sería un campo derivado que se desincroniza en cuanto se inserta
  un plan entre dos existentes — mismo criterio que `semana_actual`.
- **`Meta.ordering` lleva `-id` además de `-fecha_inicio`** para desempatar dos
  planes que arrancan el mismo día (un `ORDER BY` con empate no garantiza orden
  en Postgres). `vigente_de` igual **repite el orden explícito**: depender del
  Meta es frágil, un `.distinct()` o un `prefetch_related` de un caller lo
  anula sin ruido. Hay un `Meta.indexes` sobre `(alumno, -fecha_inicio, -id)`
  porque la consulta corre en cada request del portal y de la ficha, y el
  historial ahora crece sin archivarse.
- **Migraciones de datos encadenadas, y la lección que dejan:** `rutinas/0011`
  archivó duplicados con el criterio viejo *sin mirar si la fecha había
  llegado*, y `rutinas/0013` deshace eso porque con el criterio nuevo habría
  dejado alumnos sin ninguna rutina. Si tocás la vigencia otra vez, revisá qué
  hicieron las dos antes de escribir la tercera.
- **Un plan programado no notifica al cargarse**: el signal lo saltea y lo
  levanta el cron `enviar_recordatorios` el día que arranca (dedup por
  `RecordatorioEnviado.Tipo.RUTINA_INICIADA`), mismo patrón que las novedades
  con publicación programada. Sin eso el aviso llegaba hasta 4 semanas antes y
  el día del relevo no llegaba nada.

## Indicadores temporales del panel (2026-09-02)

El dashboard era todo agregado histórico (grilla de calor, género, RPE): no
había forma de ver si el gimnasio crece o se vacía. `tenants/analitica.py`
suma cinco funciones que responden "¿cómo venimos?".

- **`altas_y_bajas_por_mes`** depende de **`Alumno.fecha_baja`**, campo nuevo
  que estampa `alumnos/signals.py::registrar_fecha_de_baja` en la TRANSICIÓN
  a INACTIVO. Antes esto no era calculable: `modificado` cambia con cualquier
  edición, así que contar bajas por ahí daba un número inventado. La señal
  actúa solo en la transición (si reestampara siempre, corregirle el teléfono
  a alguien dado de baja hace un año lo movería al mes actual) y limpia la
  fecha al reactivar. Va en `pre_save` para que el valor viaje en el mismo
  UPDATE.
- **`ingresos_por_mes`** agrupa por el mes de la CUOTA (`anio`/`mes`), no por
  `fecha_pago`: al dueño le importa cuánto facturó cada mes, no cuándo entró
  el dinero de una cuota atrasada. Solo `PAGADO` — pendiente y vencido son
  expectativa, no ingreso.
- **Todas devuelven una fila por período aunque esté vacío.** Un gráfico que
  se saltea los meses sin movimiento hace que dos meses separados por un
  hueco se vean contiguos: miente sobre la tendencia.
- **`cobranza_porcentaje` es `None`, no 0, cuando no hay cuotas emitidas.**
  "0% cobrado" en un mes sin cuotas es alarmante y falso.
- **Cada indicador es UNA consulta agregada** (`TruncMonth`/`TruncWeek`),
  nunca un bucle por período; hay un test que compara dos tamaños de conjunto.
  Este archivo es justo donde un N+1 se paga dos veces (panel + listado).
- El gráfico de altas dibuja las **bajas en negativo**: comparadas contra las
  altas desde el cero se leen sin hacer cuentas. El tooltip devuelve el valor
  absoluto, o mostraría "-3", que es un artefacto del dibujo.
- **La última semana del gráfico semanal está en curso** y siempre se ve más
  baja; el copy lo aclara para que no se lea como una caída.

Las ventas de la tienda viven en `tienda/analitica.py`, con el mismo molde; sin el add-on no corren las queries de ventas (el contador de alertas del panel cuesta 1 query agregada para todos los gimnasios).

## Público del gimnasio y composición del padrón (2026-09-04)

`Gimnasio.tipo_publico` (`TextChoices`: `MIXTO` —default—, `MUJERES`,
`HOMBRES`) existe porque en un gimnasio de un solo género las dos tarjetas de
género no informan nada: «Alumnos por género» es una sola barra y «Ejercicios
más asignados por género» es una copia exacta del gráfico general, apilada en
un color. El default preserva exactamente el panel y la ficha de todos los
gimnasios que ya existían.

- **Es un intercambio, no un apagado.** La tarjeta de composición del padrón es
  siempre UNA, y la llena lo que de verdad distingue a los alumnos de ese
  gimnasio: género si es mixto, `distribucion_por_edad` si no. La segunda
  tarjeta (ejercicios por género) simplemente no se renderiza. **Lo decide la
  vista, no el template**: `ejercicios_mas_asignados_por_genero` vuelve a
  correr la query de ranking, así que saltearla ahorra trabajo real.
- **`Gimnasio.tiene_publico_mixto` y `sexo_implicito()`** son los dos únicos
  lugares donde vive la regla — la consultan el dashboard y `AlumnoForm`.
  `sexo_implicito()` resuelve contra `Alumno.Sexo` con import tardío
  (`Alumno` ya importa `tenants`; a nivel de módulo sería circular).
- **Los tramos de edad cubren TODO el dominio.** El primero es «Hasta 25» y no
  «18 a 25» a propósito: un gimnasio puede tener menores, y con piso en 18 un
  alumno de 16 no entraba en ningún tramo y desaparecía del gráfico en
  silencio. Mismo criterio que `orden_con_bucket_vacio`, que se niega a
  descartar el bucket vacío. `fecha_nacimiento` sin cargar → «Sin dato».
- **La edad se calcula en Python, nunca como aritmética de fecha contra una
  columna.** Es una query (`values_list`) y el agrupado va sobre las filas ya
  traídas — eso no viola "una consulta agregada por indicador", que es sobre no
  hacer N queries. Y `hoy` es parámetro, o un test de bordes (25/26) se rompe
  solo el día del cumpleaños del fixture.
- **El campo «Sexo» sale de la ficha y lo completa `AlumnoForm.clean()`**, pero
  **solo si está vacío**. Sin esa condición, editarle el teléfono a un alumno
  cargado como masculino cuando el gimnasio era mixto le reescribía el género
  al guardar. Se completa (en vez de dejarlo vacío) para que el día que el
  gimnasio pase a mixto el histórico ya sirva. No va como señal `pre_save` por
  la misma razón: estamparía en cada guardado.
- **Gotcha del JS del panel, y el motivo de `datosDe()`.** Todos los gráficos
  viven en un solo IIFE que leía cada dataset con
  `JSON.parse(document.getElementById(...).textContent)`. Con un `json_script`
  ausente eso lanza y **se lleva puestos todos los gráficos que vienen
  después**, sin ningún rastro del lado del servidor. Las 7 lecturas pasan
  ahora por `datosDe(id)`, que devuelve `[]` si el nodo no está. **Si agregás
  un gráfico condicional, leé el dataset con ese helper.** Tampoco alcanza con
  dejar el `json_script` sin condición: con la clave ausente renderiza `""` y
  `"".map(...)` rompe igual — hay un test que verifica que el nodo no quede.
- «Alumnos por género» y «Alumnos por edad» devuelven la MISMA forma
  (`{etiqueta, total}`) para compartir `barraSimple()` sin ramas.

## Datos de demostración (`manage.py sembrar_demo`)


Agregado el 2026-09-02: una cuenta vacía no muestra NADA de la app (los
gráficos del panel, la tarjeta de planes por vencer y los botones de eliminar
necesitan datos para existir en pantalla), así que una captura para promocionar
el producto se veía como un formulario en blanco.

    python manage.py sembrar_demo --gimnasio <slug> [--alumnos 24] [--meses 6]
    python manage.py sembrar_demo --gimnasio <slug> --borrar

- **Cada alumno sembrado nace con acceso** (`User`+`Perfil`) y contraseña
  compartida `demo.PASSWORD_DEMO`. Sin eso, la ficha decía «Sin acceso
  todavía» y no había botón «Entrar como»: no se podía mostrar el portal del
  alumno sin crear los accesos de a uno. La contraseña fija es aceptable
  SOLO porque el comando se niega a correr sobre un gimnasio con alumnos
  reales; se pasa vía el parámetro `password=` de
  `alumnos.services.crear_acceso`, que existe **únicamente para esto** —
  desde una vista la contraseña la sigue eligiendo siempre la app.
- **El email de demo va namespaceado por slug** (`nombre.apellido@<slug>.
  ejemplo.com`, `demo._email_demo`). `User.username` es único GLOBAL y
  `semilla` es fija, así que con un dominio compartido el segundo gimnasio de
  prueba choca contra el primero en el alumno #1. Por la misma razón el par
  (nombre, apellido) lleva sufijo a partir del alumno 25: las dos listas
  tienen 24 entradas y `24*7 % 24 == 0`, así que se repiten juntas.
- **`--borrar` tiene que sacar tres cosas más que los alumnos**: los `User`
  (anotados ANTES del `delete()`, porque `Alumno.perfil` es `SET_NULL` y
  después no hay forma de saber cuáles eran; si quedan, son logins huérfanos
  que funcionan y no aparecen en ningún panel), sus `Perfil` (van en cascada)
  y los `RegistroSuplantacion`, cuyo FK al alumno es `PROTECT` — sin eso
  `--borrar` revienta con `ProtectedError` justo para quien usó «Entrar
  como», que es para lo que se siembran los accesos.
- **`--gimnasio` es obligatorio y NO tiene default.** Además, el comando se
  niega si el gimnasio ya tiene alumnos sin la marca de demo, salvo
  `--confirmar`: es lo único que separa "lleno la cuenta de prueba" de "le meto
  24 alumnos falsos al gimnasio de un cliente que paga".
- Todo lo que crea queda marcado en `Alumno.observaciones` (`demo.MARCA`), y
  `--borrar` saca exactamente eso. Hay un test de que un alumno REAL del mismo
  gimnasio sobrevive al borrado.
- **Silencia el push mientras siembra** (`notificaciones.services.silenciado()`,
  un flag de proceso, NO `signal.disconnect()`). Cada `Reserva` notifica al
  staff: sin esto, sembrar manda **cientos** de notificaciones al celular de
  quien corre el comando. Medido: 71 con solo 4 alumnos.
- **Lección de testing que costó cuatro intentos**, y que aplica a cualquier
  test sobre push de este proyecto:
  1. `TestCase` envuelve el test en una transacción que **nunca commitea**, así
     que los `transaction.on_commit` de los signals no corren jamás → hace
     falta `TransactionTestCase`.
  2. Parchear `_enviar` reemplaza justo el código que se quiere probar (ahí
     vive el chequeo del silenciado) → hay que parchear `webpush`, el límite
     real de red.
  3. `PUSH_ENABLED` está apagado en la suite por la bandera `TESTING` → hace
     falta `override_settings(PUSH_ENABLED=True)` o `_enviar` corta antes.
  Con cualquiera de las tres cosas mal, el test pasa igual **sin el fix**.
- Las reservas se siembran recorriendo los días **de a uno** y filtrando por
  día de semana. Una primera versión avanzaba 5 días desde hoy dentro de cada
  semana: si hoy era miércoles, la grilla de calor mostraba jueves y viernes en
  CERO, que en una captura se lee como que la app está rota.
- La clave de `get_or_create` sobre `CategoriaEjercicio` usa
  `normalizar_texto`, la misma que aplica el `save()` del modelo. Con un
  `.lower()` a mano, "Tracción" se guardaba como "traccion" y la segunda
  corrida reventaba contra la `UniqueConstraint`.

La tienda se siembra **sólo si `gimnasio.es_demo`** (`tenants/demo.py::_sembrar_tienda`, dentro del `atomic` de `sembrar_demo`): `--confirmar` también siembra sobre gimnasios con alumnos reales y ahí publicaría productos inventados. Va con `get_or_create`, sin fotos (el workflow de restaurar no tiene R2) y los pedidos sembrados descuentan del disponible lo que retienen. `vaciar_gimnasio` borra los `Pedido` (PROTECT hacia `Alumno`; los ítems caen en cascada) antes que los productos, y `borrar_demo` los limpia por la marca de los alumnos.

## Cuenta de demostración compartida (`Gimnasio.es_demo`)

Agregado el 2026-09-08: se le pasa UNA misma cuenta de staff (slug `demo`) a
varios dueños de gimnasio para que prueben la app. `Gimnasio.es_demo` es el
único flag y hace **dos** cosas, a propósito:

- **Bloquea el cambio de contraseña** (`tenants/mixins.py::
  BloqueadoEnCuentaDemoMixin`, aplicado a `StaffPasswordChangeView` y a su
  pantalla `Done`). Sin esto, el primero que la cambia deja afuera a todos los
  demás: `update_session_auth_hash` salva **sólo** la sesión de quien la
  cambió. El link se oculta en `gimnasio_form.html`, pero **la defensa es el
  403** — bajo el `hx-boost` global un botón ausente no impide tipear la URL.
  **Son DOS puertas, no una**: `ResetPasswordStaffForm.get_users()` también
  excluye la cuenta demo (`perfil__gimnasio__es_demo=False`), porque su
  username ES el email y cualquiera que tenga las credenciales puede pedir el
  reset. Lo encontró un `/code-review`, y el argumento decisivo es que **la
  contraseña es lo ÚNICO del estado de la demo que `restaurar_demo` no sana**:
  todo lo demás se vacía y se resiembra cada 6 h, esa no — se revierte sólo por
  Shell o por `/admin/`.
- **Autoriza el vaciado.** `tenants/demo.py::vaciar_gimnasio` levanta
  `ValueError` si el gimnasio no lo tiene: es lo único que separa "vacío la
  cuenta de prueba" de "le borro el gimnasio entero a un cliente que paga", y
  por eso el guard vive en la FUNCIÓN y no en el comando.

**`sembrar_demo --borrar` no sirve para restaurar.** Saca sólo los alumnos con
`observaciones == "[demo]"` y lo que cuelga de ellos; deja intactos ejercicios,
categorías, plantillas, novedades, config de turnos, medios de cobro,
importaciones y las `SuscripcionPush` del staff — nada de eso lleva la marca —
y no revierte los dos campos del `Gimnasio` que la propia siembra muta
(`dias_tolerancia_pago` y `fecha_activacion_bloqueo`). De ahí `vaciar_gimnasio`.

- **El orden de borrado lo imponen los `PROTECT` y no es reordenable**:
  `RegistroSuplantacion`/`RutinaAsignada`/`Cuota` antes que `Alumno`,
  `RutinaPlantilla` antes que `Ejercicio`, `Ejercicio` antes que
  `CategoriaEjercicio`. Los `User` de los alumnos se anotan **antes** del
  borrado (`Alumno.perfil` es `SET_NULL`), filtrando por `rol=ALUMNO`: **el
  `User` del staff no se toca nunca**, es la cuenta compartida cuya contraseña
  ya circula.
- **Las credenciales de Google van PRIMERO.** `calendario/signals.py::
  sync_reserva_borrada` es un `pre_delete` sobre `Reserva` que llama a la API de
  Google **una vez por reserva**; sin credencial el receiver corta en la primera
  línea. Segundo candado: el workflow no le pasa las `GOOGLE_*`.
- **Borrar las `SuscripcionPush` del staff resuelve de paso el push cruzado**:
  cuelgan del `User`, así que si dos dueños activan notificaciones en la cuenta
  compartida, cada uno recibe en su celular lo que dispara el otro.
- `restaurar_demo` = vaciar + reaplicar `_gimnasio_canonico()` + `sembrar_demo`,
  con el `silenciado()` del push **por fuera** del `atomic` (los `on_commit`
  corren al cerrarse el más externo). `slug` y `es_demo` nunca están en el
  estado canónico: el slug es la URL pública, y pisar `es_demo` desarmaría el
  candado.
- **La cobertura son DOS tests y sólo sirven juntos**, lección de un
  `/code-review`. `test_deja_en_cero_todos_los_modelos_tenant_owned` recorre
  `apps.get_models()` filtrando `TenantOwnedModel`, pero enumerar para sólo
  CONTAR hace que la aserción parezca exhaustiva sin serlo: un modelo nuevo que
  nadie agregue ni al barrido ni al fixture cuenta 0 antes y 0 después, y pasa.
  Por eso `test_ensuciar_cubre_todos_los_modelos_tenant_owned` exige que
  `_ensuciar` deje al menos una fila de CADA modelo tenant-owned: un modelo
  nuevo rompe ése primero, obliga a sumarlo al fixture, y recién ahí el otro
  puede ver que falta barrerlo. **Si agregás un `TenantOwnedModel`, va a los
  dos lugares.**
- **El fixture tiene que crear `Cuota`, `RutinaAsignada` y `Reserva`**, y hay un
  test que restaura DOS veces seguidas (`test_restaurar_dos_veces_seguidas_
  funciona`). Es el estado estacionario —todo lo que hace el cron desde la
  segunda corrida— y el único camino donde el orden de borrado importa de
  verdad: sin eso, mover `alumnos.delete()` unas líneas más arriba no rompía
  NINGÚN test (se verificó: 317 en verde) y el cron reventaba en producción con
  `ProtectedError`, dejando la demo sin restaurar hasta que saltara Healthchecks
  horas después. Con los dos tests, ese mismo cambio rompe ocho.
- Lo corre `.github/workflows/restaurar-demo.yml` cada 6 h, con el **mismo
  `concurrency.group` que `sembrar-demo.yml`** para que una siembra manual no se
  solape con la automática. Alta de la cuenta:
  `manage.py crear_gimnasio ... --demo`.

En la demo la tienda está siempre disponible (`puede_usar_tienda` es verdadero con `es_demo`) y lleva el chip «Adicional» (barra y título), porque es un adicional que se cobra aparte (la portada dice «todas las funciones de gestión incluidas» y lista la tienda como «+ Tienda online (opcional)»; en la demo `tienda_habilitada` sigue en False y `facturacion_aplica` la excluye, así que no se le factura nada); tampoco se conecta Mercado Pago (las compras de un prospecto le caerían a la cuenta de otro). **Después del deploy:** correr `restaurar_demo` (o `sembrar_demo`) a mano para que la demo tenga la tienda sembrada, sin esperar a la próxima corrida del cron.

## Exportador de datos (`tenants/exportacion.py`)

Agregado el 2026-09-21: cuando un gimnasio deja de pagar la app o migra a otro
programa, sus datos son suyos. «Mi gimnasio» tiene una tarjeta «Tus datos» con
el botón **«Exportar mis datos (.zip)»**, que baja un CSV por entidad.

- **El botón existe siempre y sale deshabilitado** hasta que el dueño del
  producto tilda `Gimnasio.exportacion_habilitada` en `/admin/` (queda prendida
  hasta que se destilde; el campo está fuera de `GimnasioForm.Meta.fields`,
  igual que `es_demo`). **La cuenta demo exporta siempre.** La regla vive en un
  solo lugar: `Gimnasio.puede_exportar`. El `disabled` es UX; **la defensa es
  el 403 de `ExportarDatosView`**, que es POST-only y cuyo form lleva
  `hx-boost="false"` (htmx se traga la descarga).
- **Dos carpetas con los mismos datos**, porque no existe un CSV que sirva a
  los dos lectores: `para-excel/` (`;`, coma decimal, BOM — abre con doble
  click en un Excel argentino) y `para-importar/` (`,`, punto decimal, sin BOM).
  Salen de **una sola lectura**; el saneado anti-fórmula se aplica solo a la de
  Excel (en la otra, un apóstrofe agregado es corrupción de datos) y no toca
  teléfonos `+54…`, negativos ni «- Tren superior».
- **Se pagina por pk, NO con `.iterator()`.** Con `disable_server_side_cursors`
  (obligatorio por PgBouncer) el driver trae el resultado entero a memoria
  antes de iterar, y `RutinaAsignadaItem` crece sin techo. SQLite no lo muestra.
- **`EXCLUIDOS` es explícito y hay un test que lo exige**: todo
  `TenantOwnedModel` está en `HOJAS` o en `EXCLUIDOS` con su motivo (que además
  se imprime en el `LEEME.txt`). **Un modelo tenant-owned nuevo ahora va a TRES
  lugares**: `vaciar_gimnasio`, `_ensuciar`, y `HOJAS`/`EXCLUIDOS`.
  `GoogleCalendarCredential` no entra nunca — se verificó mutando el código que
  `values_list` sobre un `EncryptedTextField` **saca el token descifrado**.
- **El par de tests de cobertura solo sirve junto** (misma lección que
  `vaciar_gimnasio`): uno exige que cada modelo esté decidido, el otro que cada
  CSV tenga filas. Para el segundo, `_ensuciar_para_exportar` envuelve
  `_ensuciar` y agrega lo que ese fixture no crea (`NovedadLeida`,
  `RutinaAsignadaDiaCompletado`, una credencial de Google con token
  reconocible): sin eso, el test de secretos pasaba sin que hubiera ningún
  secreto. Y los conteos del test de aislamiento son **números fijos**, no un
  `count()` con el mismo filtro del código, que sería comparar el exportador
  consigo mismo.
- **Tres frenos en la vista, los tres por el único worker de gunicorn**: un
  techo (`MAX_FILAS_WEB`, medido: ver `ISSUES.md [2026-09-21]`) por encima del
  cual no se genera nada y se avisa al dueño del producto; 60 s entre descargas
  por gimnasio, tomados con un `UPDATE` condicional sobre
  `exportacion_ultima_descarga` (atómico ante el doble click, y `update()` para
  no tocar `modificado`, que versiona logo e ícono PWA); y el mail a
  `EXPORTACION_AVISO_EMAIL` en cada exportación de una cuenta real.
- **`manage.py exportar_gimnasio --gimnasio <slug> --salida x.zip`** genera el
  mismo archivo desde la Shell, sin techo ni timeout y sin mirar la casilla.
- `SOPORTE_CONTACTO` (env var opcional) es el texto que ve el gimnasio sin la
  exportación habilitada; `gimnasio.contacto` NO sirve, es el del gimnasio.
  **Desde el panel de plataforma ya no se inyecta en cada vista**: lo expone
  `plataforma/context_processors.py` para TODO el sitio (ver abajo).

## Panel de plataforma (`plataforma/`, 2026-09-22)

La app del DUEÑO DEL PRODUCTO, no de los gimnasios: cómo viene la plataforma,
a quién hay que cobrarle, quién la usa y a quién hay que cortarle el acceso.
Hasta acá todo eso vivía en `/admin/` y en la Shell de Render. Es la primera
app cuyo público es una sola persona, así que la regla de aislamiento que rige
al resto del proyecto (un gimnasio no ve lo del otro) acá es al revés: se ven
TODOS. El candado es `plataforma/mixins.py::SuperadminRequiredMixin`
(`is_superuser`, 403 para cualquier otro) y no hay ninguna vista sin él.

Spec en `docs/superpowers/specs/2026-09-22-panel-plataforma-superadmin-design.md`.

### Los campos nuevos de `Gimnasio` son gestión de plataforma

`estado_cuenta` + `estado_cuenta_desde`, `facturacion_inicio` y
`facturacion_exenta`. Los cuatro quedan **fuera de `GimnasioForm.Meta.fields`**,
mismo criterio que `es_demo` y `exportacion_habilitada`: un gimnasio no puede
desbloquearse solo ni decidir desde cuándo se le factura.

- **`activo` NO bloquea nada, y ése fue el hallazgo que motivó todo esto.**
  Destildarlo solo saca al gimnasio de la landing pública y del login por slug
  (`gimnasio_activo_o_404`) y lo saca de la facturación; el staff y los alumnos
  que ya tienen usuario **siguen entrando igual** por `/accounts/login/`. Léelo
  como «oculto/retirado», nunca como «cortado». Para cortar el acceso está
  `estado_cuenta`, que es una decisión distinta: una cuenta congelada por falta
  de pago sigue siendo un cliente al que se le quiere cobrar.
- **Los dos escalones de `estado_cuenta` son manuales, no un cron.** Cobrarle a
  un gimnasio es una conversación; un corte automático el día equivocado le
  rompe el día de trabajo a alguien que quizás ya transfirió.
  `ALUMNOS_BLOQUEADOS` existe como escalón del medio porque el dueño tiene que
  seguir entrando: es el único que puede pagar y la suya es la única pantalla
  donde ve qué debe. `SUSPENDIDA` deja a todos afuera.
- **La regla de quién queda afuera se escribe UNA vez**:
  `ESTADOS_SIN_ACCESO_ALUMNO` y `ESTADOS_SIN_ACCESO_STAFF` (`frozenset` en
  `tenants/models.py`), que comparten el middleware y
  `notificaciones/services.py`. Separadas, el alumno recibiría el aviso de algo
  que después no puede abrir.
- **`facturacion_inicio` vacío significa «desde la fecha de alta»**, que es lo
  correcto para un gimnasio nuevo. A los que YA existían se lo estampó la
  migración `tenants/0013` con la fecha del deploy: prender la facturación **no
  es retroactivo**, mismo criterio que `fecha_activacion_bloqueo`. Sin eso, la
  primera carga del panel muestra a todos los clientes como vencidos con meses
  de atraso por períodos que nunca se les cobró. En el FORM es obligatorio
  aunque en el modelo sea `blank=True` (mismo caso que
  `AlumnoForm.fecha_inicio_ciclo`): vaciarlo deshace esa migración en silencio.

### `PagoPlataforma` y `ActividadDiaria` NO son `TenantOwnedModel`

Tienen FK a `Gimnasio` y **aun así no van a los tres lugares** donde sí va todo
modelo tenant-owned nuevo (`tenants/demo.py::vaciar_gimnasio`, el fixture
`_ensuciar` de `tenants/tests.py`, y `HOJAS`/`EXCLUIDOS` de
`tenants/exportacion.py`). La regla completa, para el próximo modelo con FK a
`Gimnasio`:

> Un `TenantOwnedModel` es un dato **del** gimnasio: su staff lo ve y lo edita,
> le pertenece, se lo lleva al exportar y se borra al vaciar la demo. Un dato
> **de la plataforma sobre** el gimnasio lo escribe únicamente el superadmin, el
> gimnasio no lo ve en ninguna pantalla, y restaurar la cuenta de demostración
> no tiene por qué borrar el historial de lo que facturó el producto. Si es lo
> segundo, hereda de `TimeStampedModel`/`models.Model` a secas y queda fuera de
> los tres lugares.

- **`ActividadDiaria.usuario` va CASCADE y `gimnasio` PROTECT**, y la asimetría
  es deliberada: `vaciar_gimnasio` borra los `User` de los alumnos de la demo
  cada 6 h, y con PROTECT ahí la restauración automática se caería con
  `ProtectedError`. Así las filas de la demo igual desaparecen, sin que el
  modelo entre en el barrido.
- **La clave única es `(usuario, fecha)`** y el alta usa `ignore_conflicts`.
  Consecuencia asumida: si un `Perfil` se muda de gimnasio o de rol EL MISMO
  día en que ya entró, ese día le queda acreditado al gimnasio (y al rol)
  viejo. La alternativa —`(usuario, gimnasio, fecha)`— haría que una mudanza
  contara como dos personas.
- **`rol` va copiado, no se lee del `Perfil` al mirar**: un alumno al que
  después se le da acceso de staff no puede reescribir retroactivamente a quién
  se le atribuyó el uso de marzo.
- **Crecimiento**: una fila por usuario activo por día. Un gimnasio de 300
  alumnos que entran todos los días son **~110.000 filas al año**. No es un
  problema hoy —el índice `(gimnasio, fecha)` cubre las dos consultas del panel
  (los 30 días de la ficha y el `Max(fecha)` del monitor, que corre para TODOS
  los gimnasios en cada carga) y la ventana es de 30 días—, pero si alguna vez
  lo es, la salida es una purga de lo anterior a N meses, **no** cambiar el
  modelo.

### `PlataformaMiddleware`: una clase, dos trabajos

Registra el día de uso y corta el acceso de una cuenta congelada. Va en un
middleware y no en un mixin por el mismo motivo que
`ExpirarSuplantacionMiddleware`: tiene que correr en CADA request, y un mixin
deja afuera la vista que alguien agregue sin acordarse.

- **Usa `process_view` y no `__call__`**: ahí ya existe `request.resolver_match`,
  que es lo que permite decidir por NOMBRE de ruta en vez de por prefijo de URL
  escrito a mano.
- **La allowlist se evalúa PRIMERO, antes de tocar `request.user`.** No es
  cosmético y ya mordió: leer el usuario resuelve la sesión y el registro del
  día la **escribe**, o sea un `Set-Cookie`. `logo_gimnasio`/`fondo_gimnasio`
  (`tenants.views.ArchivoDeGimnasioView`) existen justamente para servir esos
  dos archivos con `Cache-Control: public, max-age=31536000, immutable` — es lo
  que bajó una navegación de 251 KB a 0 — y colgarles una cookie tira ese
  trabajo a la basura. **Lección general: el registro de actividad no puede
  tocar rutas cacheables ni rutas que pide la máquina.** La allowlist son dos
  piezas: `APPS_SIN_BLOQUEO` (`admin`, `plataforma` —las dos superficies desde
  donde se descongela: bloquearlas es tirar la llave adentro de la casa
  cerrada— y `notificaciones`) y `URLS_SIN_BLOQUEO` con los nombres PELADOS de
  `tenants` (`tenants/urls.py` no define `app_name`). Cada entrada tapa una
  forma distinta de dejar a alguien encerrado: sin `logout` no puede ni salir,
  sin `suplantacion_volver` el staff queda atrapado en la cuenta del alumno.
  Desde el 2026-09-29 hay un **tercer nivel**, `APPS_SIN_BLOQUEO_CON_REGISTRO`
  (`suscripcion`), que se evalúa DESPUÉS del registro del día y sólo para el
  staff: ver "Cobro online de la suscripción".
- **El tráfico de la PWA no cuenta como uso**: el service worker, el manifest y
  los íconos los pide el navegador solo, en segundo plano, sin que nadie haya
  abierto la app — le anotarían un día de uso a un gimnasio que nadie tocó.
- **Costo: 0 queries netas** en el camino feliz (`base.html` ya resuelve
  `user.perfil.gimnasio` y el ORM lo cachea en la instancia; `perfil_de` lee lo
  mismo un poco antes, memoizado en el request) **más UN INSERT por usuario y
  por día**. El dedupe es una marca en la sesión (`CLAVE_SESION_ACTIVIDAD`), que
  se chequea ANTES de resolver nada: el segundo request del día no cuesta ni un
  `getattr`. La marca es un caché de «ya anoté el de hoy», no la fuente de
  verdad — la fuente es la clave única del modelo, de ahí el `ignore_conflicts`
  para dos pestañas que cruzan el primer request. `CostoDelMiddlewareTests`
  compara con y sin el middleware y fija las dos cosas por separado.
- **El cartel responde 200, nunca 403 ni 404.** Bajo el `hx-boost="true"` global
  un 4xx es un click que no hace nada y no deja ningún mensaje: el usuario
  concluye que la app se rompió. Los POST se redirigen a `home`, cuyo GET
  renderiza el cartel, así que no hay loop posible. **Y la respuesta lleva
  `no-store`** (`add_never_cache_headers`): un 200 sin `Cache-Control` el
  navegador lo guarda por heurística y el bfcache lo devuelve al volver con
  «atrás», así que el cartel sobreviviría al momento en que la cuenta se
  restaura.
- **El registro corre ANTES del bloqueo**, a propósito: que alguien intente
  entrar y se encuentre el cartel también es información — es la señal de que el
  gimnasio sigue vivo del otro lado de la deuda.
- **Esto es telemetría y no puede tumbar una página**: si el INSERT falla se
  loguea y se sigue, y la marca de la sesión se escribe igual (una falla
  persistente deja una línea de log por usuario y por día, no una por request).
- **El superadmin nunca se bloquea y nunca se registra**: su `Perfil` puede
  colgar de un gimnasio congelado, y contar sus visitas haría que un gimnasio
  dormido figure usándose todos los días, que es justo lo que se busca detectar.

### Push filtrado, y qué se pierde

El filtro va en los **dos embudos** de `notificaciones/services.py`
(`notificar_a_usuario` y `notificar_a_gimnasio`), no en los siete eventos:
alcanzaba con que uno se olvidara. Se filtra por `SuscripcionPush.gimnasio`
(que sí es `TenantOwnedModel`) y no por el `Perfil`, que sería un join más por
el mismo dato.

**Consecuencia aceptada:** `_ya_notificado` marca el `RecordatorioEnviado`
ANTES de mandar, así que **un aviso silenciado no se reenvía al restaurar**. Es
lo correcto acá: son todos avisos de un momento («tu turno empieza en una
hora»), y lo que el alumno necesita al volver —su rutina, su cuota impaga— lo
ve en el portal apenas entra.

### Precios, ciclo y prueba (`plataforma/precios.py`)

Módulo **Django-free a propósito** (sin ORM, sin `timezone`, sin settings),
mismo criterio que `pagos.models.ciclo_vigente`: el «hoy» lo pasa siempre quien
llama. Precio en USD por escalones de alumnos **activos** (hasta 100 → 10, 101
a 300 → 15, más de 300 → 20; bordes inclusivos), `DIAS_GRATIS = 30` de prueba
desde `facturacion_inicio`, `DIAS_CICLO = 30` y `DIAS_AVISO_COBRO = 7`.

**Precio de lista, sólo para mostrar** (2026-10-11): `PRECIOS_DE_LISTA_USD`
(20 / 30 / 40, una clave por tope de `ESCALONES`, un test lo exige) es el
número que la portada tacha al lado de la oferta. **No entra a ningún cálculo
de cobro** y no se tacha en «Mi suscripción», donde se cobra. El anual (10 de
12 meses, `DESCUENTO_ANUAL_MESES = 2`, confirmado ese día) se muestra tachando
`precio_usd_anual_sin_descuento` (12 × mensual). La portada dice que el tramo
«se ajusta solo» y aclara que el anual lo congela 12 meses: las dos frases van
juntas o el texto miente. Ver `ISSUES.md [2026-10-11]`.

**La tienda (Hito 3, 2026-10-18):** `PRECIO_TIENDA_USD = 20` por mes,
`precio_tienda_usd(tienda)` (20 o 0) y `precio_tienda_usd_anual(tienda)` (10
meses: 200 o 0). `precio_usd`, `precio_usd_anual` y
`precio_usd_anual_sin_descuento` ganan `tienda=False` **sólo por keyword**
(`precio_usd(n, True)` no compila a propósito); sin el argumento devuelven lo de
siempre. **El anual congela tramo Y tienda por 12 meses.** Sin prueba gratis
propia: el primer mes gratis del gimnasio nuevo ya cubre todo. La portada lee
estos números de `_contexto_precios` (`precios_tienda_usd`,
`precios_tienda_anual_usd`, `precios_tienda_ars`), nunca literales: la línea
«+ Tienda online (opcional)» va debajo de los tramos y los textos dicen «todas las
funciones **de gestión** incluidas». «Con cobro por transferencia» es verdad
mientras la tienda no tenga MP: **el Hito 2 tiene que actualizar esa frase**.
Test: `tenants.tests` (`test_portada_ofrece_la_tienda_como_adicional...`).

**Ojo con la simetría**: acá `DIAS_CICLO` es 30 (lo que la PLATAFORMA le cobra
al gimnasio) y en `pagos` es 28 (lo que el gimnasio le cobra a sus alumnos).
Son dos negocios distintos y las dos constantes tienen que poder moverse por
separado. `PagoPlataforma.periodo_hasta` es **inclusivo**, igual que
`pagos.Cuota.periodo_fin`.

`plataforma/facturacion.py` es lo único que toca el ORM, y el monitor lista
TODOS los gimnasios: es justo donde un N+1 se paga en cada carga.
`gimnasios_anotados()` es **una** query y el precio y el estado se calculan en
Python sobre las filas ya traídas. Las tres relaciones multivaluadas
(`alumnos`, `actividad`, `pagos_plataforma`) van como **`Subquery`
correlacionada y nunca como `Count`/`Max` sobre el join**: anotadas como join
el producto cartesiano las multiplica entre sí (con dos pagos, un gimnasio de 3
alumnos cuenta 6). **Si agregás una cuarta anotación multivaluada, va como
`Subquery`.**

Dos guards sobre el registro de un pago: la `UniqueConstraint(gimnasio,
periodo_desde)` —el caso real es el doble submit del form boosteado y el «¿lo
habré cargado?»— traducida a un error de campo por el `clean()` del form, más
un `try/except IntegrityError` en `form_valid` (adentro de un `atomic()`, o el
error deja la transacción marcada para rollback y los `messages`, que viven en
la base, revientan después) para la race que el chequeo check-then-insert no
cubre. El template frena el doble click deshabilitando el botón, mismo patrón
que `gimnasio_form.html`.

**Las tres escrituras del panel dejan `modificado` como estaba**:
`EstadoCuentaView` y `ExportacionToggleView` con `QuerySet.update()`, y
`FacturacionUpdateView` con `save(update_fields=[...])` (ahí hay un `ModelForm`
que validar, y con `auto_now` un campo fuera de `update_fields` no se
persiste). `Gimnasio.version_media` deriva de `modificado` y versiona la URL
del logo, la del fondo y la del ícono de la PWA: tocarlo por un cambio de
facturación le hace volver a bajar todo a cada celular del gimnasio.

### La tienda en el monitor y en el pago manual (Hito 3)

- `facturacion.fila_de_gimnasio` toma `tienda = gimnasio.tienda_habilitada`;
  `FilaMonitor.precio_usd` pasa a ser el **total** (tramo + tienda) y gana
  `tienda_usd: int = 0` al final. `kpis`, `para_cobrar` y `avisos` no cambiaron
  de código: ya suman el total. Sin tienda, todo idéntico a antes (test «sin
  tienda = igual que hoy»). La demo con `tienda_habilitada=True` igual no
  factura (`facturacion_aplica`).
- **Aceptado:** el monitor muestra el precio del PRÓXIMO cobro, así que un
  gimnasio con un anual pago al que se le prende la tienda ya suma +20 en `kpis`
  aunque no pague hasta renovar (mismo comportamiento de siempre con un cambio
  de tramo). La etiqueta «con tienda» de las columnas «Precio (USD)» y «Para
  cobrar» lo explica.
- `PagoPlataforma.monto_tienda_usd` (default 0: las filas viejas valen 0, que es
  la verdad) es la parte del monto que es la tienda. El pago manual
  (`PagoPlataformaCreateView.get_initial`) precarga `monto_usd` con la tienda y
  `monto_tienda_usd` con `precio_tienda_usd(tienda_habilitada and facturacion_aplica)`.
  El campo es opcional en el form (vacío = 0, para no romper los POST que no lo
  mandan), no negativo y no puede superar `monto_usd`. **Aceptado:** después de
  un «Monto a revisar» el precargado sale del toggle de hoy, no del intento; el
  superadmin lo corrige.
- `TiendaToggleView` sigue con `update()` (no toca `modificado`); sólo cambia el
  mensaje: con cobro dice que se suma (o deja de sumarse) desde el próximo pago,
  y sin cobro (demo, exento, retirado) «(no se le cobra)».

### Cotización del dólar (`plataforma/cambio.py`)

El precio se pacta en USD y los gimnasios pagan en pesos. Dólar **MEP**
(`dolarapi.com/v1/dolares/bolsa`), con `urllib` de la biblioteca estándar y no
`requests` (no es dependencia del proyecto y es un solo GET).

**Regla que ordena el módulo: el panel no puede caerse ni colgarse por una API
de terceros.** `cotizacion_dolar()` no levanta nunca y devuelve `None` ante
cualquier problema; las pantallas leen ese `None` como «mostrar solo USD».
Cache de **24 h** para el éxito y de **10 min para el fallo** (si no, cada carga
del panel se come el timeout entero mientras la API esté muerta; el timeout es
de 3 s porque esto corre dentro del request y gunicorn tiene 30). Bajo
`TESTING` no sale a la red, mismo criterio que R2.

**Y manda un `User-Agent` propio** (`cambio.USER_AGENT`): con el de `urllib`
por defecto («Python-urllib/3.x») dolarapi responde **403**, y como el módulo
se traga todo, el panel mostraba sólo USD sin un solo error a la vista —
exactamente el modo de falla que la regla de arriba tolera a propósito. Medido
el 2026-09-28 desde Render y desde local. Si la cotización desaparece del
panel, el primer sospechoso es éste, no el cache.

**`fecha` vuelve como datetime *aware* en UTC**, que es como la publica la API:
se muestra SIEMPRE con el filtro `date` o con `timezone.localtime(...)`, nunca
con `.date()` ni `.hour` a secas — con UTC-3 una cotización de las 21:30 de
Buenos Aires está fechada al día siguiente en UTC. El pago guarda `monto_usd`,
`monto_ars` y `tipo_cambio`: no es redundancia, con la inflación argentina
recalcular después con la cotización de hoy da un número que nunca existió.
`monto_ars`/`tipo_cambio` son opcionales porque la API puede estar caída justo
cuando se registra el pago, y eso no puede impedir anotarlo. El POST **no** pide
cotización: los montos los manda el formulario.

### Alta de un gimnasio: la contraseña se ve UNA vez

`plataforma:gimnasio_crear` no es un camino nuevo, llama al MISMO
`tenants.services.crear_gimnasio` que el comando (el registro público sigue
cerrado). **El POST exitoso NO redirige**: renderiza la credencial en un 200.
No es un descuido de PRG — es el patrón de `alumnos/views.py::
_render_credenciales`: la contraseña no puede viajar por `messages`, que se
serializa en la sesión, que acá vive en la BASE. `never_cache` va sobre el
`dispatch` entero y no alrededor de un `render` suelto, para que no haya forma
de agregar mañana otra salida que se olvide del header. `sin_password` (la
cuenta entra con Google) no muestra ninguna contraseña: inventar una para
llenar el hueco sería mostrar un dato que no sirve para entrar.

### Acoplamiento nuevo: `tenants` lee `SOPORTE_CONTACTO` del panel

`plataforma/context_processors.py::estado_cuenta` expone
`cuenta_alumnos_bloqueados`, `cuenta_suspendida`, `SOPORTE_CONTACTO` y
`SENTRY_PANEL_URL` para TODO el sitio. Va como context processor y no como
mixin porque el banner vive en `base.html`, o sea en toda página: una vista que
se olvide de agregarlo dejaría al dueño sin enterarse de que sus alumnos están
bloqueados. **Consecuencia: `tenants` ya no inyecta `SOPORTE_CONTACTO` en su
propio contexto** (ver `tenants/views.py`) — si algún día se saca la app
`plataforma` de `INSTALLED_APPS`, ese texto desaparece de «Mi gimnasio». Corta
temprano en `app_name == "admin"`, mismo criterio que
`tour_onboarding_disponible`: una query de `Perfil` menos contra Neon en cada
pantalla del admin.

`SENTRY_DSN` es una variable SUELTA y no un grupo todo-o-nada (mismo criterio
que `SOPORTE_CONTACTO`): sin ella el SDK ni se inicializa y la app funciona
igual. `SENTRY_PANEL_URL` es puramente cosmética (el link del panel) y se lee
en cada request para que `override_settings` sirva en un test.

`URLS_SIN_BLOQUEO` (`plataforma/middleware.py`) ya no son sólo nombres de `tenants`: entra `tienda_foto_producto`, que se compara por `url_name` **sin namespace** (de ahí el prefijo `tienda_` en el nombre de la ruta). Es la foto `immutable` del producto: colgarle la cookie de sesión rompería su caché.

## Cobro online de la suscripción (`suscripcion/`, 2026-09-29)

Hasta acá el dueño del producto le cobraba a cada gimnasio por transferencia y
cargaba a mano la fila de `PagoPlataforma`. El modo de falla caro: **el
gimnasio que paga un domingo a la noche seguía bloqueado hasta que alguien
mirara el panel.** Ahora el staff paga desde la app con Mercado Pago (Checkout
Pro, un link por ciclo — no débito automático, así el monto se calcula fresco
cada vez y no se lo come la inflación) y el pago se acredita solo. **El registro
manual no se eliminó**: queda para el que transfiere o paga en efectivo, y
`PagoPlataforma.origen` (`MANUAL`/`ONLINE`) distingue los dos caminos.

**Alcance: SÓLO plataforma → gimnasio.** El cobro del gimnasio a sus alumnos
(`pagos`) no se tocó; ver el matiz en «Principios no negociables» #3. Spec en
`docs/superpowers/specs/2026-09-29-cobro-online-suscripcion-design.md` (su
sección final «Decisiones de ejecución» manda sobre el resto).

### Por qué una app aparte

La pantalla de pago la usa el **dueño del gimnasio**, y toda vista de
`plataforma` lleva `SuperadminRequiredMixin` — es la invariante de esa app. En
`tenants` tampoco: está al principio del orden de dependencias y tendría que
importar `plataforma.precios`/`facturacion`. `suscripcion` tiene **dos
públicos** (el staff del gimnasio y, en `cobros/`, el superadmin) más Mercado
Pago, que no tiene sesión: cada vista declara el suyo con exactamente uno de
los dos mixins o es el webhook, y lo fija
`tests_vistas.test_toda_vista_declara_su_publico`, el sustituto de «toda vista
de `plataforma` es superadmin». Va **última** en `INSTALLED_APPS` (lee
`plataforma`): el comentario que decía que `plataforma` era la última porque
nadie la leía dejó de ser cierto y se corrigió.

`IntentoDeCobro` (el link generado) y `AvisoDeCobro` (el dedup de los mails de
vencimiento) **no son `TenantOwnedModel`**: son datos de la plataforma SOBRE el
gimnasio, igual que `PagoPlataforma`, así que quedan fuera de los tres lugares
(`vaciar_gimnasio`, `_ensuciar`, `HOJAS`/`EXCLUIDOS`). Por eso `AvisoDeCobro`
no reusa `notificaciones.RecordatorioEnviado`, que sí es del gimnasio. **Ninguno
tiene FK a `User`**, y no es opcional: `vaciar_gimnasio` borra los `User` de la
demo cada 6 h; con PROTECT ese cron se cae con `ProtectedError`, con CASCADE se
borra un registro de plata. Quién apretó «Pagar» queda como texto
(`email_de_quien_pago`). Por lo mismo `PagoPlataforma.registrado_por` pasó a
nullable (un pago online no lo tipeó nadie), con la `CheckConstraint`
`pago_plataforma_manual_con_autor` sosteniendo lo que importa: una fila
`MANUAL` siempre tiene autor. Es unidireccional a propósito: prohibir el autor
en una online bloquearía que el superadmin la repare.

### El precio se congela en el link

El intento guarda monto USD, monto ARS, tipo de cambio, alumnos activos y
período en el momento de generar el link: **lo que se le cobra es lo que se le
mostró**, la misma invariante por la que `PagoPlataforma` guarda los tres
montos. Sin cotización (`cambio.cotizacion_dolar()` en `None`) **no hay link**
(`CobroNoDisponible("sin_cotizacion")` + un `WARNING` con el slug) y el botón de
la pantalla sale deshabilitado: antes la pantalla prometía un monto que después
no podía producir. Nunca se persiste la última cotización buena: en Argentina
una cotización vieja siempre subfactura.

- **`UniqueConstraint(gimnasio, periodo_desde)` parcial sobre `estado="creado"`**
  (calcado del de `ingreso`): un solo link vivo por período. Es lo que hace que
  el segundo click **reuse el link** en vez de crear un segundo cobro, y que dos
  pantallas no muestren dos tipos de cambio del mismo mes — el anti-doble-click
  deja de ser un problema de JS. Mensual y anual comparten `periodo_desde`, así
  que cambiar de plan pasa el link anterior a `CANCELADO` y libera el índice.
  **Sólo se reusa si le quedan más de `MINUTOS_MINIMOS_PARA_REUSAR` (30)**:
  con menos, MP lo rechazaría a mitad del pago, así que se pasa a `VENCIDO` y
  se genera otro.
- **Un link vivo sólo se ofrece («Continuar al pago») si es del período que el
  gimnasio debe HOY** (`views._intento_abierto_de`). Pagar online un período
  además pasa a `CANCELADO` los otros `CREADO` del gimnasio que se solapan con
  él; el pago MANUAL no puede hacerlo (`plataforma` no importa `suscripcion`),
  y por eso el filtro vive en la pantalla. Sin las dos cosas, un gimnasio que
  generó el link y después transfirió veía «al día» y «Continuar al pago» a la
  vez hasta 48 h, y pagarlo era un `DUPLICADO` con devolución manual.
- **El link vence a las 48 h** (`HORAS_VALIDEZ_LINK`), en MP (`expires` +
  `expiration_date_to`) y en `IntentoDeCobro.expira`. 48 y no 24: el aviso del
  viernes a la noche se tiene que poder pagar el domingo; no una semana: el MEP
  se mueve.
- **Vencer es perezoso**: `crear_intento` marca `VENCIDO` los `CREADO` vencidos
  del gimnasio en la misma transacción, antes de insertar. Con el índice
  parcial un `CREADO` eterno bloquearía el link nuevo, y el cron llega 2-4 h
  tarde.
- **`crear_intento` es estricto, `acreditar` es permisivo.** El primero se
  niega (con un 200 que explica, nunca un 4xx: bajo el `hx-boost` global es un
  click muerto) si `Gimnasio.facturacion_aplica` es falso — la regla vive ahí y
  en ningún otro lado —; en la demo la pantalla muestra precio y plan con el
  botón deshabilitado. `acreditar` **no se niega por nada de eso**: si la plata
  entró, se acredita y se avisa. Negarse al acreditar es quedarse con plata sin
  registro.
- Si Mercado Pago falla al crear la preferencia, `crear_intento` loguea un
  `WARNING` con el slug y el error de MP (método, ruta y código HTTP, sin
  secretos por diseño: un 401 ahí es el token mal cargado) y levanta **adentro
  del `atomic`**, así el rollback se lleva el intento recién creado y no queda
  uno huérfano ocupando el índice. Antes de ese log el operador no tenía ningún
  rastro: el gimnasio veía el aviso y el log de Render, un 200.
- **`DIAS_ANIO = 360`** (12 × `DIAS_CICLO`), no 365: con un año calendario los
  períodos mensual y anual dejan de encajar en la grilla de 30 días y un
  gimnasio que pasa de anual a mensual arrastra el corrimiento para siempre.
  `precio_usd_anual` es el mensual × (12 − `DESCUENTO_ANUAL_MESES`), y el anual
  **congela el escalón de precio 12 meses** (decisión comercial asumida, ver
  `ISSUES.md [2026-09-29]`).
- **Todo acceso desde `suscripcion` a un gimnasio para facturar pasa por
  `facturacion.gimnasio_anotado(pk)`, y `crear_intento` además llama a
  `facturacion.exigir_anotado`.** `periodo_a_cobrar` lee `cubierto_hasta` con
  `getattr`, así que con un `Gimnasio` pelado —que es exactamente
  `request.user.perfil.gimnasio`, la forma natural de escribir la vista—
  devuelve `None` en silencio y cobra siempre el primer período después de la
  prueba, aunque el gimnasio ya haya pagado tres. Un comentario no alcanzaba:
  el guard levanta `ValueError`. `gimnasios_anotados()` sigue siendo UNA query;
  la lista «Cobros con problema» del superadmin es una query aparte.

### La tienda en el cobro online (Hito 3)

- `services.tienda_usd_para(gimnasio, plan)` es la única regla: el add-on
  (mensual o anual) de `gimnasio.tienda_habilitada`. Lo usan `crear_intento` y
  `views._intento_abierto_de`. `IntentoDeCobro.monto_tienda_usd` lo guarda al
  generar el link y `_acreditar_aprobado` lo **copia** al `PagoPlataforma`, nunca
  lo recalcula (lo cobrado es lo mostrado).
- **El link vivo se reusa sólo si coincide plan Y tienda**: si no, se cancela
  como un cambio de plan. Sin eso, un link generado antes de prender la tienda
  se reusaría 48 h con el monto viejo. `plataforma` no importa `suscripcion`, así
  que el toggle no puede cancelar links: el filtro vive en
  `_intento_abierto_de` (el banner «Continuar al pago» de Mi suscripción, que va
  directo a MP con el `monto_ars` viejo) y `crear_intento` cancela el viejo al
  generar el nuevo.
- Mails al superadmin (`cobro_acreditado`, `cobro_con_problema`) y título de la
  preferencia de MP dicen «tienda» si la incluye. El aviso de vencimiento
  (`avisos.avisar`) pasa `tienda_usd` como default del lambda (si no, todos los
  mails de la corrida llevan el valor de la última fila).
- **Casos aceptados:** (1) un período atrasado pagado después de prender la
  tienda la incluye: el monto sale del toggle del día en que se paga, no del
  período («desde el próximo pago» = el próximo cobro); (2) un link viejo sin
  tienda (CANCELADO/VENCIDO) que igual se paga se acredita con su monto
  (`monto_tienda_usd = 0`): se cobra lo que se mostró (hay test); (3) el mail de
  vencimiento sale una vez por período, así que si la tienda cambia después su
  precio queda viejo (Mi suscripción siempre muestra el actual).

### `mercadopago.py` levanta, al revés de `cambio.py`

Mismo molde (stdlib `urllib` —el SDK oficial arrastra `requests`, que el
proyecto ya rechazó para `cambio.py`—, un único seam de red `_pedir`) y **la
regla de errores invertida**. `cambio.py` traga todo porque el panel no puede
caerse por una API de terceros y no hay plata en juego; acá la hay:
`crear_preferencia`, `consultar_pago` y `buscar_pagos` **levantan
`ErrorDeMercadoPago`** y quien llama decide cómo degradar. `_pedir` **levanta
`RuntimeError` bajo `TESTING`**: un patch olvidado en el camino de la plata
tiene que romper el test, no pasar en verde con el pago sin acreditar. Los
cortes al LEER la respuesta (`ConnectionResetError`, `IncompleteRead`) urllib no
los envuelve, y se mapean a `ErrorDeMercadoPago` porque los llamadores sólo
atrapan ésa. `TIMEOUT = 5` y cero reintentos internos: el reintento es MP a los
15 min, o el cron.

- **El header `Authorization` se arma en `_headers()` y nunca se ata a una
  variable local de `_pedir`.** Sentry (`SENTRY_DSN`) captura las variables
  locales de cada frame, y el scrubber por defecto de `sentry-sdk` **no es
  recursivo**: filtra un local llamado `token`, no un dict `headers` con el
  Bearer adentro. Lo fija un test que recorre los frames de la traza — **con un
  `try/except` a mano, porque `assertRaises` limpia los frames**
  (`traceback.clear_frames`) y el test pasaría sin mirar nada.
  **Lo mismo vale para los datos del PAGADOR**: `GET /v1/payments` trae su
  email, documento y tarjeta, así que `acreditar` recorta la respuesta con
  `resumen_de_pago` en la misma línea que la consulta (nunca queda entera en
  un local), y `reconciliar_pendientes` sólo ve los ids de la búsqueda
  (`services._ids_de_pagos`). Lo fija
  `tests_services.DatosDelPagadorFueraDeLosFramesTests`.
- `ErrorDeMercadoPago.codigo` es el status HTTP (`None` sin respuesta): el
  webhook trata un **404** como «ese pago no existe» y contesta 200.
- `init_point` vs `sandbox_init_point` se elige por el prefijo `TEST-` del
  token, en el módulo: el riesgo concreto es desplegarle un link de sandbox a un
  cliente real.
- **Una sola credencial, la de la plataforma**; no hay OAuth por gimnasio. El
  día que `pagos` (gimnasio → alumno) tenga MP, necesita credenciales POR
  gimnasio y cifradas con `EncryptedTextField`: este módulo no sirve tal cual.
- `binary_mode` + `ticket`/`atm` excluidos: un pago no puede quedar «en
  revisión» ni pagarse en un Rapipago a los 3 días con el tipo de cambio de
  hace 3 días. Costo asumido: se pierde al que paga en efectivo (le queda el
  registro manual).
- `firma_valida` es puro y no levanta. Los segmentos ausentes del manifest se
  **omiten** (no van vacíos), el `data.id` va en minúsculas, y **el `ts` no se
  valida como ventana de frescura**: la defensa contra un replay es la
  idempotencia, y un reloj más es uno que puede rechazar un reintento legítimo.

### `acreditar`: el único punto de entrada, llamado por tres caminos que compiten

El webhook, el retorno del navegador y la reconciliación llaman a
`services.acreditar(payment_id)`, en cualquier orden y cantidad de veces. **El
webhook llegando antes que el navegador es el caso normal, no el borde.** Es
idempotente con `select_for_update()` + re-chequeo de estado (molde
`solicitudes/services.py::_decidir`), no recibe `request` sino `url_panel_para`
(un callable; el cron no tiene request), y **el estado del pago sale SIEMPRE de
`GET /v1/payments/{id}`**, consultado antes de abrir la transacción (no se
retiene un lock esperando a la red) — nunca del cuerpo del webhook, que viaja
sin firmar (la firma cubre el `data.id`, no el `action`).

- **La idempotencia es por `(payment_id, estado_mp)`, no por `payment_id`.** MP
  manda otra notificación con el MISMO id cuando el pago pasa a
  `refunded`/`charged_back`; cortar por id solo se tragaba la devolución.
- **`estado_mp` no retrocede para el mismo pago.** Como la consulta corre fuera
  del lock, dos llamadas pueden commitear en otro orden que el de sus lecturas:
  una foto vieja de «approved» acreditaría (y descongelaría) un pago que otra
  llamada ya registró como devuelto. Un devuelto no vuelve a nada; un aprobado
  sólo avanza a devuelto.
- **Un pago DISTINTO sobre un intento** (un link se puede pagar más de una
  vez): si el intento ya movió plata (`PAGADO`/`DUPLICADO`/`REVISAR`/`DEVUELTO`)
  no se pisa nada, y si el nuevo está aprobado se anota en
  `respuesta_mp["pagos_adicionales"]` (deduplicado por `payment_id`), va un
  `WARNING` al log y sale el mail «pago adicional» para devolverlo — el log y
  el mail sólo la primera vez, y «Cobros con problema» lista esos intentos
  aunque sigan `PAGADO` (antes la única traza era un mail best-effort); si no
  la movió (`CREADO`/`VENCIDO`/`CANCELADO`/`RECHAZADO`),
  el `payment_id` se reemplaza y se sigue — es la tarjeta rechazada seguida de
  un reintento aprobado en la misma pantalla de MP.
- **Acreditables son `CREADO`, `VENCIDO`, `CANCELADO` y `RECHAZADO`.** La plata
  en la cuenta le gana a nuestro reloj: MP puede aprobar un pago iniciado un
  minuto antes del vencimiento, o el gimnasio pagar el link que canceló al
  cambiar de plan. **Un `filter(estado=CREADO)` acá se come un pago real.**
- **El monto de MP se VALIDA, nunca se escribe.** `transaction_amount` contra
  `intento.monto_ars` (±1 centavo) y `currency_id == "ARS"`; si no coincide,
  `REVISAR` + mail, sin `PagoPlataforma`. Es `transaction_amount` y no
  `total_paid_amount` (con cuotas incluye el interés del comprador: no es un
  pago de más) ni `net_received_amount` (resta la comisión: bajaría el KPI de
  ingresos ~2% en silencio contra las filas manuales). Escribir lo que reporta
  MP dejaría que un checkout manipulado compre un mes por ARS 1.
- **Doble cobro por SOLAPAMIENTO, no sólo por igualdad.** Antes de crear el
  `PagoPlataforma` se busca cualquier pago del gimnasio cuyo rango se solape (el
  superadmin pudo cargar a mano el período con otra fecha de inicio, que la
  `UniqueConstraint(gimnasio, periodo_desde)` no ve). Solapa → `DUPLICADO` +
  mail. La constraint queda como red para la carrera, y su `IntegrityError`
  también va a `DUPLICADO` + mail, dentro de su propio `atomic()`. **Nunca
  copiar el `except IntegrityError: return None` de `solicitudes`**: ahí existe
  para no filtrar información; acá sería quedarse con la plata sin decírselo a
  nadie.
- **Devolución y contracargo**: el intento `PAGADO` pasa a `DEVUELTO` y sale
  mail al superadmin. **No se borra el `PagoPlataforma` ni se recongela la
  cuenta**: cortar es siempre una decisión humana.

### Descongelar: «restauración automática, corte nunca»

La invariante de `Gimnasio.EstadoCuenta` («los escalones son MANUALES, nunca un
cron») **no se viola, se extiende**, y conviene leerla bien antes de «arreglar»
esto: todos sus argumentos son sobre el peligro de **cortar** automático (el día
equivocado, a alguien que quizás ya transfirió). Restaurar tiene el riesgo
inverso: dejar afuera un fin de semana a un gimnasio que YA pagó. Se descongela
si y sólo si las cuatro: (a) el `approved` vino de consultar la API; (b) el
monto coincidió; (c) el `PagoPlataforma` se creó en esta transacción; (d)
recalculado desde la base, `Max(periodo_hasta) >= timezone.localdate()`. **La
(d) es la que importa**: quien debe tres meses y paga uno no se descongela, y el
mail al superadmin dice desde cuándo sigue debiendo (es
`pagos/views.py::_avisar_si_sigue_bloqueado` un piso más arriba). Se escribe con
`update(estado_cuenta=NORMAL, estado_cuenta_desde=None)`: `update()` para no
tocar `modificado` (versiona logo, fondo e ícono PWA), y los dos campos juntos
porque una fecha vieja sobre una cuenta normal se lee como congelada. Riesgo
aceptado: sin campo `motivo`, un gimnasio suspendido por otra razón se
descongela con un pago que lo deja al día.

**Por lo mismo, `RetornoView` sólo dice «¡Listo!» si el gimnasio quedó al día
de verdad.** Pagar uno de varios períodos adeudados muestra que todavía se
deben períodos (situación `pagado_con_deuda`, leída del estado de cobro recién
recalculado y no del intento): «¡Listo!» con un «Ir al inicio» que termina en el
cartel de cuenta congelada es la lección de «"Pago confirmado" a secas sobre
alguien que sigue sin acceso». El `status` del querystring de retorno se
ignora, la referencia de la URL se valida contra el gimnasio del usuario (404),
y un `ErrorDeMercadoPago` ahí muestra «estamos confirmando tu pago»: un lookup
fallido no puede parecerse a un pago fallido. Pero si volvió **sin** pago (sin
`payment_id` o con el literal `"null"`) y el intento no tiene ninguno anotado,
dice «No se registró ningún pago» (situación `sin_pago`): «si ya pagaste no
vuelvas a pagar» ahí es falso.

### El webhook: no es «200 siempre»

`/suscripcion/v1/webhook/mercadopago/` (la versión va en la ruta, mismo
argumento que `/ingreso/v1/`: la URL queda cargada en el panel de MP y en cada
preferencia ya generada). `csrf_exempt` y **nunca mira `request.user`**, que es
lo que lo hace seguro (lo fija `test_ignora_request_user_con_una_firma_valida`).

En este orden:

| Caso | Respuesta | Por qué |
|---|---|---|
| Integración apagada | 503 | MP reintenta hasta que se cargue la configuración; con 200 se perderían. |
| Tipo distinto de `payment`, firmado o no | 200, **antes de la firma** | No toca la base. Las IPN viejas y las de `merchant_order` pueden llegar sin firmar: con 401 se reintentaban para siempre. |
| `payment` con firma inválida | 401, sin tocar la base | Un reintento no arregla una firma; 401 en serie en el panel de MP = secret mal cargado. |
| Sin `data.id`, cuerpo inválido, pago ajeno o ya acreditado | 200 | Nada que hacer; el cortocircuito de `acreditar` lo hace barato. |
| MP contesta 404 al consultar el pago | 200 | El pago no existe (el «simular notificación» del panel de MP): con 500 se reintentaba para siempre. |
| `ErrorDeMercadoPago` (MP no contestó) | **500 a propósito** | MP reintenta cada 15 min, muchísimo mejor que el cron (2-4 h tarde). Con 200, un gimnasio que pagó queda sin acreditar hasta mañana. |

Una notificación con `data.id` pero sin `type` se contesta 200 y se ignora:
tratarla como pago haría 500 infinitos sobre notificaciones que no son de pagos
(pendiente de confirmar en el sandbox que las de pago siempre traen `type`).

### El gimnasio suspendido tiene que poder pagar: tercer nivel de allowlist

Con `SUSPENDIDA`, `PlataformaMiddleware` le muestra el cartel al staff en toda
ruta: sin arreglarlo, **la feature era inalcanzable justo para el cliente que
más la necesita**. `APPS_SIN_BLOQUEO` era el arreglo equivocado: corre ANTES de
tocar `request.user` (existe para rutas cacheables y de máquina) y perdía
`registrar_dia_activo`, que acá es justo la señal que interesa — el gimnasio
sigue vivo del otro lado de la deuda. Va **`APPS_SIN_BLOQUEO_CON_REGISTRO =
{"suscripcion"}`**, evaluado DESPUÉS del registro del día y **sólo para el
staff**: aplicado a cualquiera, el alumno de un gimnasio bloqueado que tipea
`/suscripcion/` recibiría el 403 de `StaffRequiredMixin` en vez del cartel. El
webhook no depende de esto (es anónimo y el middleware corta antes). Las
entradas cuestan cero queries: el botón «Pagar la suscripción» de
`cuenta_bloqueada.html`, el «Pagar ahora» del banner de atraso de `base.html`
(los dos condicionados a `COBRO_ONLINE_DISPONIBLE`, un context processor que es
una bandera de settings) y la tarjeta «Suscripción a TuGimApp» de «Mi
gimnasio». **El nav no ganó ningún ítem.**

**Todo form o link que termina en mercadopago.com.ar lleva `hx-boost="false"`**
(«Pagar con Mercado Pago», «Continuar al pago»): es el redirect cross-origin de
siempre, que htmx hace por XHR y no puede seguir. Y los radios del plan
destaparon un defecto latente: heredaban el `input { w-full … }` global y
quedaban como un punto flotando en un casillero vacío;
`.selector-fondo-tipo__opcion input` ahora lleva `w-auto m-0 p-0 border-0
shrink-0`, lo que de paso arregló el selector de fondo de «Mi gimnasio», que
tenía el mismo problema.

### Crons y avisos

- **`reconciliar_cobros`** (`reconciliar-cobros.yml`, cada 6 h) es la red para
  «el webhook se perdió y el usuario cerró la pestaña», y por eso usa
  `buscar_pagos` por `external_reference` (ahí nunca hubo un `payment_id` que
  consultar). **Reconcilia todo intento reciente en estado acreditable, tenga o
  no `payment_id`**: el plan original miraba sólo los de `payment_id` vacío, y
  así «tarjeta rechazada + reintento aprobado + webhook perdido» no se volvía a
  mirar nunca y el gimnasio que pagó quedaba congelado. Pasa TODOS los
  resultados a `acreditar`, para que un segundo aprobado dispare el aviso de
  pago adicional. Recorre **del más nuevo al más viejo** (tope 50): un backlog
  de links muertos no puede dejar afuera al recién generado, que es el que más
  probablemente tiene un aprobado con el webhook perdido.
- **`avisar_cobros`** (`avisar-cobros.yml`, diario a las 12:00 UTC) manda al
  staff del gimnasio el aviso «por vencer» (`DIAS_AVISO_COBRO`), «vence hoy» y
  «vencido», deduplicado por `AvisoDeCobro(gimnasio, periodo_desde, tipo)`.
  **Sin destinatarios NO se crea la fila de dedup** (se loguea un `WARNING`): si
  se creara, el «vencido» —uno por período— se perdería para siempre aunque
  después se cargue un email.
- **Los dos comandos levantan `CommandError` sin la integración.** Un cron que
  no hace nada en silencio porque faltan los secrets es indistinguible del sano:
  así el workflow queda en rojo y no pinguea a Healthchecks.
- Los mails (`suscripcion/emails.py`) siguen el molde de `solicitudes`: texto
  plano, `fail_silently=True`, `EMAIL_REPLY_TO`. Los del superadmin van a
  `COBROS_AVISO_EMAIL`, suelta y opcional (vacía cae a `DEFAULT_FROM_EMAIL`).
  Salen sincrónicos en el `on_commit` del webhook y del retorno, así que
  **`EMAIL_TIMEOUT = 10`** (constante en `settings.py`, no variable de
  entorno): sin él `smtplib` espera para siempre y un SMTP colgado retiene el
  único worker hasta el kill de gunicorn a los 30 s — un 502 para el gimnasio
  que acaba de pagar.

### Qué sólo se prueba en el sandbox

La suite no sale a la red: todo el contrato con MP está parcheado sobre
`suscripcion.mercadopago`. Queda por confirmar contra el sandbox (usuarios de
prueba, credenciales `TEST-…`, el nombre del titular `APRO`/`OTHE` decide el
resultado): que el manifest de `firma_valida` coincide con el real, que las
notificaciones de la `notification_url` llegan firmadas (si no, darían 401 y
las levantaría la reconciliación, tarde), y que el email del pagador que se
manda en la preferencia no rompe el sandbox. Pasos manuales en
`ISSUES.md [2026-09-29]`. **En local**, el Python de python.org de esta Mac no
trae certificados raíz (`CERTIFICATE_VERIFY_FAILED` contra dolarapi y MP):
correr `runserver` con `SSL_CERT_FILE` apuntando al bundle de `certifi`.
Render no tiene el problema.

## Control de ingreso (`ingreso/`, 2026-09-28)

Que un alumno entre por un molinete con un QR o con un lector facial, y que el
gimnasio vea quién entró de verdad. Varios establecimientos se lo pidieron al
dueño del producto, y por eso salió de «Qué NO construir todavía». **No hay hardware todavía**: lo
construido es un contrato HTTP que cualquier equipo comercial (Hikvision,
ZKTeco, Dahua, Suprema) o un puente en la PC del gimnasio puede consumir, más
las pantallas de staff y del alumno. **No se construye modo kiosco** (tablet o
celular con cámara en la puerta), por decisión del dueño del producto: sin
hardware, la puerta se ejercita con `curl` (`scripts/probar_ingreso_local.sh`).

Spec en `docs/superpowers/specs/2026-09-27-control-de-ingreso-design.md` (con
los riesgos aceptados numerados y un glosario), plan en
`docs/superpowers/plans/2026-09-28-control-de-ingreso.md`, y el contrato para
el instalador en `docs/integracion-control-de-ingreso.md`. **Vocabulario:** en
la UI la llave del lector es la «clave», y en el código y el contrato es el
`token` (`Authorization: Bearer`, `Dispositivo.token_hash`); el QR del alumno
es su «código», y en el modelo es `Credencial.valor`. **Ningún modelo lleva
«Acceso» en el nombre**: la palabra ya significa usuario+contraseña
(`alumnos:accesos`) y el bloqueo por deuda (`pagos/acceso.py`).

### Dónde vive cada pieza

- **`models.py`** — cuatro `TenantOwnedModel`: `ConfiguracionIngreso` (fila
  única, siempre vía `obtener_configuracion`, que hace `get_or_create`; **ojo:
  `turnos.models` tiene una función con el mismo nombre**, importalas
  calificadas), `Dispositivo` (el lector: `token_hash`, `activo`,
  `ultimo_contacto` y el contador `eventos_hoy`/`eventos_hoy_fecha`),
  `Credencial` (QR o id facial, una por alumno y tipo, así que regenerar es un
  `UPDATE` en la misma fila; el `valor` es único por gimnasio y tipo **solo
  entre las activas**, para que el id facial de un ex-alumno se pueda
  reasignar, y por eso `contexto_de` prefiere la activa cuando hay dos filas
  con el mismo valor) y `EventoIngreso` (cada intento, permitido o no:
  `dispositivo` PROTECT, `alumno` CASCADE nullable, `credencial` SET_NULL, y el
  primer `UniqueConstraint` parcial del proyecto, sobre `(dispositivo,
  id_evento_equipo)`). El valor leído NO se guarda en el evento: de una
  credencial desconocida quedan los últimos caracteres en `detalle`.
- **`tokens.py`** — `generar()` y `hash_de()`, Django-free.
- **`decision.py`** — `Lectura`, `Contexto`, `contexto_de()` (lo único que
  consulta la base), `decidir()` y los textos de la pantalla (`MENSAJES`,
  `SALUDO`, `mensaje_de`), compartidos por la puerta online y el padrón.
- **`services.py`** — todas las escrituras: `crear_dispositivo`, `rotar_token`,
  las credenciales (`generar_credencial_qr`, `generar_credenciales_en_bloque`
  con un solo `bulk_create(ignore_conflicts=True)` contra la carrera de dos
  POST, y el número de creadas contado en la base porque así Django no lo
  informa, `guardar_credencial_facial`,
  `dar_de_baja_credencial`), `registrar_lote` y `padron_de`.
- **`auth.py`** — `dispositivo_autenticado()`: 401 sin token o con uno que no
  matchea (sin distinguir cuál), 403 solo con un token válido de un lector
  desactivado.
- **`limites.py`** — el freno por IP y el tope diario (ver abajo).
- **`qr.py`** (`svg_de`, `png_de`, `modulos_de`) y **`tarjetas.py`**
  (`generar_pdf_credenciales(nombre_gimnasio, tarjetas)`), los dos
  Django-free. Dependencia nueva: `segno` (Python puro). `qr.py` usa
  `segno.make_qr` y **no `segno.make`**, que para valores cortos elige Micro
  QR y la cámara del celular no lo lee.
- **`presentacion.py`** — lo que comparten las pantallas: `codigo_de` (el
  único lugar que decide si un QR se dibuja: solo si abre la puerta),
  `tarjeta_del_portal`, `resumen_para_el_panel` (la tarjeta del dashboard, dos
  queries fijas; qué es un «rechazo» lo define `models.RESULTADOS_RECHAZO`, la
  misma tupla que usa el filtro `rechazados` del listado, y la doble lectura
  `DUPLICADO` no lo es), `inicio_del_dia` (la medianoche LOCAL, compartida por el
  listado, el panel y la purga) y `fuera_del_boost`.
- **`views.py`** — la API (`EventosApiView` y `PadronApiView`, en
  `/ingreso/v1/eventos/` y `/ingreso/v1/padron/`: la versión va en la ruta
  porque el instalador la deja cableada en un equipo que no podemos
  actualizar), las pantallas de staff (`ingreso:listado`, `dispositivos`,
  `configuracion`, las de credenciales y `tarjetas`), `ingreso:instructivo`
  («Cómo conectar tu puerta»: el documento de integración en pantalla, para que
  el dueño no tenga que mandarle un `.md` del repo a su instalador) y
  `ingreso:mi_codigo` del portal. Se entra desde la tarjeta «Control de
  ingreso» del dashboard y desde «Configurar la puerta» en «Mi gimnasio»: **el
  nav se queda en 8 ítems**.
- **`analitica.py`** — «Reservas contra ingresos reales» y «Vinieron sin
  reserva» del panel del staff. La grilla de calor de `tenants/analitica.py`
  cuenta lo que el alumno DIJO que iba a hacer; con un lector en la puerta
  existe además lo que hizo, y esto pone las dos cosas lado a lado. La ventana
  es de `SEMANAS_COMPARACION` (8) para las DOS grillas —comparar todo el
  historial de reservas contra las pocas semanas que lleva conectada la puerta
  haría que las reservas ganen en todas las celdas— y de `DIAS_SIN_RESERVA`
  (7) para la lista, que es para actuar esta semana y no un ranking histórico.
  Costo fijo, porque corre en cada carga del panel: dos agregados y tres
  queries, nada por alumno.
- **`management/commands/purgar_ingresos.py`**, el puente de ejemplo
  `scripts/puente_ingreso_ejemplo.py` (**solo stdlib**: tiene que correr en la
  PC de un gimnasio sin `pip install`) y `scripts/probar_ingreso_local.sh`.
  `ingreso.tests.DocumentoDeIntegracionTests` compara el documento de
  integración contra lo que la API devuelve (rutas, campos de los JSON,
  resultados, motivos, la tabla de constantes), y `PuenteDeEjemploTests` exige
  que el puente no importe nada fuera de la stdlib: **un cambio de contrato
  arrastra el documento en el mismo commit**.

### Decisiones que no hay que deshacer

- **La app nunca guarda ni procesa biometría.** La cara se enrola EN el equipo,
  que guarda la plantilla y reporta «entró el usuario 42»; la app guarda solo
  ese número (`Credencial.valor` de tipo FACIAL). La plantilla facial es dato
  sensible (Ley 25.326), y correr un modelo de reconocimiento en el único
  worker de gunicorn no es viable. Ningún campo del contrato lleva una foto ni
  un vector.
- **`decidir` no toca la base.** `contexto_de` resuelve el lote entero con un
  número FIJO de queries (credenciales, configuración, deudores con
  `pagos.acceso.bloqueados_entre`, reservas con
  `turnos.services.reservas_vigentes_en_momentos`, ingresos previos para el
  antipassback), traiga una lectura o cien, y `contexto` no tiene default: un
  `contexto=None` «que se lo arma solo» es la puerta por la que volvería la
  query por evento. Las ventanas de reservas e ingresos se arman alrededor de
  CADA lectura, nunca como un rango `[min, max]` del lote: una lectura del año
  2000 de un lector con el reloj reseteado traía el historial entero.
- **Orden de las ramas**: sin credencial, credencial inactiva, alumno
  inactivo, deuda, sin reserva, antipassback. El antipassback va último («podés
  entrar pero ya entraste»: el deudor que insiste tiene que leer «cuota sin
  pagar», no «ya pasaste») y solo compara eventos del mismo `sentido`. Las
  credenciales se indexan por `(tipo, valor)`: un QR y un id facial pueden
  compartir el texto.
- **`estado_cuenta` no cierra la puerta, pero sí las pantallas.** Si el
  gimnasio no le paga la app al dueño del producto, se le corta el panel y el
  torniquete sigue abriendo: dejar gente afuera de un edificio no es una
  palanca de cobro. `decision.py` no conoce `estado_cuenta`, y la API queda
  afuera de `PlataformaMiddleware` porque no tiene sesión (el middleware corta
  en anónimo) — **`ingreso` NO está en `APPS_SIN_BLOQUEO`**, así que las
  pantallas de staff muestran el cartel como el resto del panel. Lo fijan dos
  tests que solo sirven juntos: `tests_api.PuertaConLaCuentaCongeladaTests`
  (la puerta sigue abriendo; el cliente del test **no se loguea**, porque un
  lector real nunca tiene cookie y logueado el test fallaría por el motivo
  equivocado) y `tests_vistas.PantallasConLaCuentaSuspendidaTests` (el
  cartel). Ninguno de los dos frenos de `limites.py` mira `estado_cuenta`.
- **Con la cuenta SUSPENDIDA, un lector se revoca desde `/admin/`**
  destildando `activo` del `Dispositivo` (el dueño del gimnasio no puede: sus
  pantallas muestran el cartel). No se abre una excepción en la allowlist. La
  API responde 403 desde el request siguiente porque `auth.py` mira `activo`
  en la base, y `DispositivoAdmin.save_model` invalida el set cacheado del
  freno. `ISSUES.md [2026-09-28]`, `tests_vistas.RevocarDesdeElAdminTests`.
- **El token se guarda como `sha256`, sin pimienta.** Una query indexada, y un
  volcado de la base no contiene ninguna llave usable. Ni en claro (como
  `solicitudes.SolicitudAccesoToken`, que es de un solo uso) ni con
  `make_password` (del orden de 100 ms de CPU bloqueante por request en el
  único worker, estimado, no medido en la rama;
  con 256 bits de `secrets` el key-stretching no compra nada). **Sin pimienta
  con `SECRET_KEY`**: rotarla dejaría sin puerta, en silencio y de golpe, a
  todos los gimnasios. Si algún día se pasa a un hasher lento, el camino de
  «token no encontrado» necesita un hash dummy o el tiempo delata qué
  lectores existen.
- **Online manda el reloj del servidor; en diferido `momento` e
  `id_evento_equipo` son obligatorios.** El modo lo define
  `abierto_por_el_equipo` (booleano JSON: ausente, `null` o `false` es online;
  `true` es diferido; un string es un ítem inválido), nunca el largo de la
  lista. Online el ingreso ES ahora, y el `momento` del lector solo deja un
  diagnóstico en `detalle` si difiere más de `SEGUNDOS_DE_DESFASE_A_ANOTAR`.
  En diferido el `momento` es lo que se registra, tiene que caer entre
  `DIAS_DE_ANTIGUEDAD_TOLERADOS` atrás y `HORAS_DE_ADELANTO_TOLERADAS`
  adelante, y sin id el reintento del backlog duplicaría el ingreso. Un id
  numérico vuelve como string en un evento válido (se guarda como texto).
- **Errores por ítem.** Un evento inválido no tumba el lote: no se registra y
  ocupa su posición con `resultado: "invalido"` y un `error` legible. Solo la
  forma del request entero da 400 (no es JSON, no trae `eventos`, más de
  `MAX_EVENTOS_POR_LOTE`, JSON demasiado anidado). Nunca un 500 por un `tipo`
  desconocido.
- **Se decide en orden cronológico y se responde en el orden del request.**
  `registrar_lote` recorre el lote con un `sorted` estable por `momento` (con
  10:10/10:00/10:20 y 15 minutos de antipassback, desordenado daba un ingreso
  y ordenado dos), y cada respuesta vuelve en la posición de su evento, que es
  como el lector las correlaciona. Todo se escribe con UN
  `bulk_create(ignore_conflicts=True)`; la respuesta sale de `decidir()` y no
  de las filas guardadas. Un `id_evento_equipo` ya registrado devuelve el
  veredicto guardado sin decidir de nuevo.
- **El padrón no lleva a los dados de baja.** `padron_de` filtra
  `alumno__estado=ACTIVO` y credenciales activas: offline un inactivo es una
  credencial desconocida, y el puente lo borra del equipo, cara incluida (su
  QR, una llave al portador, no se queda en la memoria de un equipo de
  terceros). Consecuencia: **reactivar a un alumno obliga a volver a enrolar
  su cara.** El deudor sí viaja, con `habilitado: false` y `motivo: "deuda"`:
  el puente lo bloquea en vez de borrarlo. El padrón no puede aplicar reserva
  ni antipassback (`REGLAS_NO_INCLUIDAS`), y lo que no se aplica offline está
  escrito al lado de los checkboxes de `ingreso:configuracion`, no en una nota
  al pie. Responde `no-store, private` en el 200 y en el 304: son las llaves
  de la puerta y el sitio va proxeado por Cloudflare.
- **El freno por IP es del camino de FALLO, y un lector legítimo no lo recibe
  nunca.** Pasadas `MAX_FALLOS_POR_MINUTO` fallas desde una IP, 429 con
  `motivo: "intentos_fallidos"`, salvo que el hash presentado esté en
  `limites.hashes_activos()`: el set de `token_hash` de todos los lectores
  activos de la plataforma, cacheado `SEGUNDOS_SET_ACTIVOS`. Se recalcula con
  una query cuando el cache está vacío, así que sobrevive a un deploy (el
  `LocMemCache` arranca vacío) y al desalojo del cache; la primera versión
  recordaba cada lector en el proceso y dejaba afuera al legítimo después de
  cada deploy. La IP sale de `solicitudes.antispam.ip_de`
  (`CF-Connecting-IP`); falta verificar en producción que sea la real
  (`ISSUES.md [2026-09-28]`).
- **El tope diario (`MAX_EVENTOS_POR_DIA`) nunca se aplica al padrón.** Es un
  contador en `Dispositivo` (no un `COUNT`, que un lote diferido fechado días
  atrás esquivaba), leído del dispositivo ya autenticado y sumado con un
  `UPDATE` atómico solo si el lote creó filas; los reintentos con el mismo id
  no suman. Va solo en `EventosApiView`: el padrón no escribe, y cortarle el
  poll hace que la puerta abra hoy y deje de abrir mañana. El padrón puede
  recibir un 429, pero solo por `intentos_fallidos`.
- **Rotar la clave pone `eventos_hoy` en 0 en el MISMO `update()` que el hash**
  (`services.rotar_token`) e invalida el set (`limites.olvidar_lectores_activos`,
  igual que el alta y el desactivar/reactivar). Sin lo primero, quien robó el
  token y agotó el tope deja al lector en 429 hasta la medianoche aunque el
  dueño rote; sin lo segundo, el token viejo pasa el freno un minuto más.
  Rotar pide confirmación (GET muestra qué deja de andar, solo el POST rota).
- **Un secreto en pantalla necesita DOS capas, y ninguna alcanza sola.**
  Son dos cachés distintos del navegador, y cada capa tapa uno:
  1. **`hx-history="false"` + `no-store` (`never_cache`)** en la pantalla del
     secreto. htmx 2 guarda el `<body>` en `localStorage` antes de cada
     navegación boosteada y el «atrás» lo restaura sin ir al servidor; el
     atributo lo apaga, y el `no-store` saca la respuesta del caché HTTP.
     `ingreso/dispositivo_token.html` (la clave, que se muestra UNA vez en un
     200 sin redirect ni `messages`), `ingreso/mi_codigo.html`, el QR de
     `partials/credencial_alumno.html`, `alumnos/acceso_credenciales.html` y
     `plataforma/gimnasio_creado.html`. Lo fija
     `tests_vistas.SecretosFueraDelHistorialDeHtmxTests`.
  2. **Salir del boost**, porque la primera capa NO cubre el bfcache. Una
     navegación boosteada (un GET o el POST de un form) mete la respuesta
     ADENTRO del documento anterior, que no lleva `no-store`: al salir con una
     navegación completa («Salir»), ese documento queda en el bfcache con el
     secreto en el DOM y el «atrás» del próximo que agarre la computadora del
     mostrador lo muestra. Por eso **todo form cuyo POST devuelve un secreto
     lleva `hx-boost="false"`**: el alta y la rotación de la clave del lector
     (`dispositivo_form.html`, `dispositivo_rotar_confirmar.html`), el alta de
     acceso y la contraseña nueva del alumno (`acceso_form.html`, y el botón
     de la ficha y del panel de accesos) y el alta de gimnasio de
     `plataforma` (`FormsDeSecretoFueraDelBoostTests`). Los GET que dibujan un
     QR salen igual: los links llevan `hx-boost="false"` (el de «Mostrar mi
     código» y todos los que van a la ficha del alumno), y la red para lo que
     no pasa por un link es `presentacion.fuera_del_boost`: ante un GET
     boosteado, `MiCodigoView` y la ficha con un QR vigente responden
     `HX-Redirect` a su propia URL. Por lo mismo, **el inicio del alumno no
     dibuja el QR**: muestra el estado y el botón, y la llave vive en una sola
     pantalla del portal. **Si agregás un form o una pantalla que muestre un
     secreto, van las dos capas.**
- **Las tarjetas se imprimen en tandas de `TARJETAS_POR_TANDA` (30).**
  Medido en una Mac: ~33 ms por tarjeta (casi todo segno), 100 ≈ 3,4 s. Con el
  worker único, una tanda de 100 dejaba la puerta online de toda la plataforma
  en cola más tiempo del que espera un molinete (ver abajo). Las descargas son
  POST desde forms con `hx-boost="false"`, `never_cache` y un nombre de archivo
  sin datos del alumno. El QR impreso nunca baja de 25 mm
  (`tarjetas.MINIMO_CODIGO_MM`).
- **El QR es fijo por alumno y regenerable**: lo decisivo es que sirve
  impreso para el que no tiene smartphone. **Un QR dado de baja no se
  reactiva con el mismo valor** (la baja pudo ser por una tarjeta robada):
  «Generar código» lo avisa y ofrece regenerar. Un id facial sí se reactiva
  volviéndolo a cargar, porque lo que abre es la cara. No hay backfill: una
  migración que le cree un QR a cada alumno le generaría llaves de puerta a
  gimnasios que no pidieron la feature.

### Cobertura: los mismos lugares que cualquier `TenantOwnedModel`

Los cuatro modelos son datos DEL gimnasio y van a los lugares que ya describen
"Exportador de datos" y "Cuenta de demostración compartida":
`tenants/demo.py::vaciar_gimnasio`, el fixture `_ensuciar` de
`tenants/tests.py` (del que sale `_ensuciar_para_exportar`) y `HOJAS` de
`tenants/exportacion.py`. Los vigilan cuatro tests por introspección que se
rompen en cuanto el modelo existe: `test_ensuciar_cubre_todos_los_modelos_tenant_owned`,
`test_deja_en_cero_todos_los_modelos_tenant_owned`,
`test_todo_modelo_tenant_owned_esta_exportado_o_excluido_a_proposito` y
`test_cada_csv_de_cada_carpeta_tiene_datos` — por eso cada modelo entró en el
mismo commit que lo creó. Dos detalles propios: en `vaciar_gimnasio`,
`EventoIngreso` se borra explícito y antes que `Credencial` (los eventos con
`alumno=None` no cascadean desde los alumnos y seguirían protegiendo al
`Dispositivo`), y `Dispositivo` va después de los eventos; en el exportador,
las hojas de lectores y credenciales salen **sin** `token_hash` y **sin**
`valor` (son llaves de la puerta, igual que las contraseñas). Los
`verbose_name` de los cuatro son load-bearing: `core/borrado.py` arma con ellos
el aviso de borrar un alumno.

### El riesgo del worker único

gunicorn corre con UN worker síncrono (`render.yaml`, sin `--workers`),
compartido entre el panel y la API de los lectores. **Cualquier request largo
del panel —una tanda de tarjetas, un padrón grande, el ZIP del exportador—
deja en cola la puerta online de TODOS los gimnasios** mientras dura, y un
molinete se rinde, se estima, a los dos o tres segundos (sin hardware no se
midió). Un 304 del padrón cuesta lo mismo
que un 200 (la versión es un hash del contenido). La mitigación de hoy son las
tandas chicas y que el puente trate un timeout de la API como un 429: decide
con el padrón local y sube el evento en diferido con el mismo id. La de fondo
es más de un worker o una cola. Spec, riesgos aceptados 13 y 15;
`ISSUES.md [2026-09-28]` (medir en Render antes del primer cliente grande).
Mismo motivo por el que el camino autenticado no tiene freno de requests:
cualquier freno ahí le puede cerrar la puerta a un lector legítimo.

### Purga: retención, nunca un paso previo a exportar

`manage.py purgar_ingresos --gimnasio <slug> --meses N [--dry-run]` borra los
`EventoIngreso` del gimnasio anteriores a N meses de calendario (default
`MESES_POR_DEFECTO` = 24, mínimo `MESES_MINIMOS` = 3), cortando en la
medianoche local, por tandas de pk. Existe por el techo del exportador: un
gimnasio de 300 alumnos generaría del orden de 72.000 eventos por año
(estimación con un supuesto de ~20 visitas por alumno al mes, no medido) contra
`MAX_FILAS_WEB` = 60.000. Es **retención periódica** (primero con
`--dry-run`) para que el botón «Exportar mis datos» no choque contra el techo
en la operación normal. **Nunca se corre antes de exportar los datos de un
gimnasio que se va**: le borraría justo el historial de asistencia que se
quiere llevar. Ese caso va por `manage.py exportar_gimnasio`, que no tiene
techo.

**Pendiente de producto:** el panel muestra «Asistentes hoy» (cuenta `Reserva`,
`tenants/analitica.py`) al lado de «N ingresos hoy» (ingresos reales por la
puerta), y pueden leerse como el mismo número. Queda para decidir con el dueño
del producto (`ISSUES.md [2026-09-28]`).

## Borrar: `core/borrado.py` + `BorrarConExplicacionView`

Agregado el 2026-09-02 a pedido del dueño (eliminar plantillas, ejercicios y
alumnos). Un `DeleteView` pelado acá es una fábrica de 500: casi todo el
historial cuelga con `on_delete=PROTECT`, así que el borrado revienta con
`ProtectedError` justo en los casos más comunes.

**Regla de producto:** borrar de verdad lo que NO tiene historial (cargado por
error, pruebas — el caso real), y cuando no se puede, decirlo en castellano y
ofrecer la salida que ya existe. **Nunca borrar historial de cobros en
cascada**: `Cuota` es el registro de lo que el gimnasio facturó.

- `core/borrado.py` lee el MODELO (`_meta.related_objects`), no una lista
  escrita a mano: si aparece una FK nueva, entra sola en el aviso.
- El chequeo del GET **no reemplaza** al `try/except ProtectedError` del POST:
  el cron de pagos genera filas solo, así que el preview puede quedar viejo.
  Hay un test que postea igual sobre un alumno bloqueado.
- Plantilla: borrado limpio. `RutinaAsignada` es un snapshot **sin FK viva**,
  así que ninguna rutina ya entregada se toca —
  `test_borrar_una_plantilla_no_toca_la_rutina_ya_asignada_del_alumno` fija esa
  garantía, que es lo único que hace seguro el botón.
- Ejercicio en uso → bloqueado, ofrece destildar `activo`. Alumno con pagos o
  rutinas → bloqueado, ofrece «Inactivar alumno».
- **Con confirmación a propósito.** El precedente de POST-sin-confirmar es
  `rutinas:item_eliminar`, un ejercicio suelto; acá se borra un alumno o un
  plan entero.

## Aviso de plan por vencer (`RutinaAsignada.por_vencer_de`)

Un plan dura 4 semanas y nada le recordaba al staff que se terminaba: si nadie
miraba, el alumno llegaba al día 29 sin plan. `DIAS_AVISO_PLAN = 7` (constante,
no un campo configurable — nadie lo pidió, y avisar antes lo vuelve ruido).

- **La ventana se compara contra `fecha_inicio`, no contra
  `fecha_fin_prevista`**, que es una property: "termina dentro de N días" es
  "arrancó hace entre 28-N y 28 días". Con la property habría que traer todo a
  memoria.
- **NO tiene piso: un plan ya vencido sigue apareciendo.** Lo encontró un
  `/code-review`, y era el agujero más serio: el aviso desaparecía justo cuando
  el problema se materializaba. Un plan que llega al día 28 un sábado y no se
  reemplaza el fin de semana, el lunes ya no figuraba en ningún lado — y el
  alumno que HOY está sin plan es el caso más urgente. La lista igual no crece
  sola: se acota al plan VIGENTE de cada alumno, a alumnos ACTIVOS y a los que
  no tienen ya un siguiente cargado.
- **Deja de avisar solo** cuando ya hay un plan siguiente cargado o el alumno
  no está activo: es un recordatorio de tarea pendiente, y uno que no
  desaparece al hacerla se vuelve ruido que el staff aprende a ignorar.
- **Costo fijo: 1 query, no 2 por candidato.** La primera versión llamaba a
  `vigente_de`/`proxima_de` dentro de un list-comprehension — 22 candidatos
  daban **45 queries**, en el dashboard Y en el listado. Es el patrón que este
  archivo prohíbe y que ya causó un 502 con el importador. Las dos condiciones
  son `Exists(...)`: "es el vigente" ⇔ no existe otro plan activo ya empezado
  que gane el orden `-fecha_inicio, -id`; "no hay siguiente" ⇔ no existe uno
  activo con fecha futura. **Si tocás `vigente_de`, el `Exists` tiene que
  seguirlo**: es la misma regla escrita en Python y en SQL.
- Se ve en los tres lugares que pidió el dueño: tarjeta `.aviso-urgente` arriba
  del dashboard (el único donde el staff se entera sin ir a buscarlo, topeada a
  10 + "y N más"), badge `.badge--urgente` en el listado, y el botón «Asignar
  plan siguiente» en `.boton-urgente` en la ficha. **Ámbar y no rojo**: rojo es
  "algo se rompió", esto es "hay algo para hacer esta semana".
- **Lección del review sobre los tests de costo:** el test que decía cubrir
  esto creaba 15 alumnos SIN rutina, así que el conjunto de candidatos no
  crecía y pasaba con el N+1 presente. Un test de escala tiene que hacer crecer
  **lo que de verdad multiplica el costo**, no cualquier cosa que se le
  parezca.

## Un formulario que rechaza sin que se note es igual a uno que no guarda

Dos bugs del mismo día (2026-09-02), reportados por el primer cliente pago
como "no me guarda" cuando en realidad el form devolvía errores que él no
podía ver:

1. **`.errorlist` de Django no tiene ningún estilo en este proyecto.** Con
   `{{ form.as_p }}`, "Este campo es obligatorio" sale en NEGRO, del mismo
   cuerpo que las ayudas grises, y **arriba** de la etiqueta: se lee como una
   instrucción más. Pasó en `rutinas/item_form.html`.
2. **Un campo sin su `{% if form.<campo>.errors %}`** rompe el guardado del
   formulario ENTERO en silencio (los tres links de redes en
   `tenants/gimnasio_form.html`).

**Regla:** al agregar un campo, agregá su línea de error en el mismo commit; y
si una pantalla usa `form.as_p`, o le das estilo a `.errorlist` o renderizás
campo por campo con `.form-campo`/`.config-error` (ver `item_form.html` como
molde: etiqueta con `*` + `.sr-only` para los obligatorios, ayuda, y el error
DEBAJO del campo).

**Corolario sobre qué es obligatorio:** un campo que el sistema puede deducir
no debería serlo. `RutinaPlantillaItem.orden` obligaba al entrenador a
numerar a mano; hoy es opcional y se calcula `max + 1` dentro del día, la
misma regla que `services.agregar_ejercicio_asignado` ya usaba para el flujo
de rutinas asignadas. `series`/`repeticiones` siguen obligatorios **en el
form** a propósito: no hay valor sensato que inventar y un item sin ellas le
llega al alumno como una fila vacía. En el **modelo** de `RutinaPlantillaItem`
son opcionales desde el 2026-09-07 (solo ahí, nunca en `RutinaAsignadaItem`):
es lo que deja entrar los ítems «a completar» del importador de PDF (ver
"Planes en PDF" más abajo). `RutinaPlantillaItemForm` los vuelve a exigir en
`__init__` —editar un ítem es justamente cómo se completa— y
`RutinaAsignada.crear_desde_plantilla` rechaza asignar una plantilla con
ítems incompletos (`AsignarRutinaForm.clean` repite el chequeo para mostrarlo
como error de campo, mismo patrón que el guard de fecha anterior a la
vigente). El detalle marca cada ítem con «A completar» y el listado cuenta
cuántos faltan con un `Count` anotado, nunca `items_incompletos()` por fila.

### El editor de plantillas (2026-09-07)

**El detalle de la plantilla ES la grilla editable**: un día por tarjeta
plegable, un ejercicio por fila, las 4 semanas en columnas y un botón
«Guardar» por día. Reemplazó al alta de a uno (`item_crear`/`item_editar`/
`item_eliminar`, `RutinaPlantillaItemForm` e `item_form.html`, todos
**retirados**): armar el plan real del primer cliente eran 172 formularios.

- **Las celdas viajan en UN campo JSON, nunca en un formset.** 50 ejercicios ×
  4 semanas × 5 campos son 1000 campos, justo contra
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` — el límite que ya produjo un
  `TooManyFieldsSent` (`ISSUES.md [2026-07-28]`). Molde:
  `importaciones.forms.ResolucionesJSONForm`. Los controles de la grilla **no
  llevan `name`**; un listener de `submit` los serializa al hidden.
- **`DiaDePlantillaForm` valida la FORMA** (`clean()` con `add_error(None,…)`,
  porque un error de campo sobre un hidden no se ve en ninguna pantalla), y la
  **vista valida la tenencia**: los `ejercicio_id` se resuelven de una sola vez
  con `in_bulk` contra el catálogo del gimnasio. Nunca una query por fila.
- **Guardar reemplaza el día** dentro de una transacción. Es seguro porque
  `RutinaPlantillaItem` no tiene ninguna FK entrante viva.
- **Si el payload no valida NO se redirige**: los valores viven en el JSON y no
  en el HTML, así que un redirect le haría perder al entrenador todo lo
  cargado. Se re-renderiza con `filas_previas` y el JS repuebla el día.
- **`orden` es la posición de la fila** (antes se tipeaba y el form hacía
  `max+1`), y **`dia_nombre` es un campo por día** que se estampa en todos sus
  ítems (antes se heredaba con la regla de la semana más baja).
- **Un ejercicio no puede repetirse en el mismo día.** `agrupacion.py` agrupa
  por `ejercicio_id`, así que dos filas iguales se fusionarían al releer y una
  se perdería en silencio. Lo encontró la prueba en el navegador, no la suite.
- **Dos interfaces desde el mismo HTML.** En computadora la tabla ancha; abajo
  de 640px las celdas se reorganizan en una tarjeta por ejercicio, con los
  campos de cada semana en una grilla de **3 columnas** (Series/Reps/Kilos y,
  en el renglón siguiente, Descanso/Notas, esta última con `col-span-2` para
  que el grupo mida exactamente dos filas). Apilar los cinco daba 20 campos
  por ejercicio, o sea 220 en un día de 11. **No renderizar dos bloques
  distintos**: serían dos controles por dato y una serialización ambigua.
  Fueron `grid-cols-5` hasta el 2026-09-09; medido a 390px, esas cinco
  columnas quedaban en 43px y rompían dos cosas a la vez: la etiqueta
  «Descanso» se montaba sobre «Notas», y el input, con 12px de padding por
  lado, dejaba 19px de contenido, así que **el valor de Series se veía
  cortado**.
- **El título de cada semana es un `::after` absoluto** sobre la celda de
  Series (`content: attr(data-semana-titulo)`, `width: calc(300% + 1rem)` = las
  3 columnas más sus 2 separaciones). Antes ese lugar lo ocupaba la etiqueta
  larga de la celda («Semana 1 · Series», `attr(data-label)`), que medía dos
  renglones donde las otras ocupan uno y **desalineaba el casillero de Series
  contra los otros cuatro de su fila**. Es pseudo-elemento y no elemento propio
  porque un pseudo-elemento no puede ser ítem de la grilla y un `<td>` nuevo
  agregaría una columna a la tabla de escritorio: **si cambia la cantidad de
  columnas o el `gap-x-2`, el calc los tiene que seguir**. El atributo va en
  las filas renderizadas Y dentro del `<template>` del molde — sin lo segundo,
  un ejercicio agregado con «Agregar ejercicio» sale sin título; hay un test
  por cada uno.
- **El buscador**: el catálogo viaja una vez por `json_script` y Tom Select se
  inicializa **al recibir el foco**. Un `<select>` completo por fila serían más
  de 30.000 `<option>` con una biblioteca real. Y **su CSS se inyecta desde el
  JS**, no por `{% templatetag openblock %} block extra_style {% templatetag closeblock %}`: ese bloque vive en el
  `<head>`, que `hx-boost` nunca reemplaza. Así el gotcha deja de aplicar a
  esta pantalla en vez de repetir `hx-boost="false"` en cada link entrante.
- **Bajar `dias_por_semana` pide confirmación y borra** los ejercicios de los
  días que sobran: si no, quedaban invisibles en la grilla pero
  `crear_desde_plantilla` se los seguía dando al alumno.
- **Se puede crear una plantilla desde la pantalla de asignar**: el alumno
  viaja en la query string por las tres pantallas y vuelve con la plantilla ya
  elegida.

**Historia previa de esta tabla: se agrupó por día un ejercicio por fila y una
columna por semana** (2026-09-07) — la misma reagrupación que recibió
`asignada_detail.html` el 2026-08-31 y por el mismo motivo: el plan real del
primer cliente son 43 ejercicios por semana × 4 semanas = 172 filas planas,
ilegibles. Comparten el agrupador: `agrupacion.py::_filas_por_ejercicio` es el
núcleo, y las dos funciones públicas (`listar_ejercicios_del_dia` para el
snapshot, `listar_ejercicios_de_plantilla` para la plantilla) solo cambian QUÉ
identifica al mismo ejercicio y de dónde salen nombre, categoría y video. La
plantilla agrupa por `ejercicio_id` (tiene FK viva, así que renombrar en la
biblioteca no parte la fila en dos); el snapshot sigue agrupando por nombre.
**Solo se muestran las semanas que la plantilla TIENE**, no siempre 1..4.
Consecuencia sobre el borrado: hay UN botón por fila y la fila son todas las
semanas, así que `RutinaPlantillaItemDeleteView` quita el ejercicio de todas
las semanas de ese día (antes borraba un item suelto) y el label dice de
cuántas se trata — borrar una sola dejaría las otras tres en pantalla como si
no hubiera pasado nada.

## El día del alumno en el celular: una hoja por semana (2026-09-09)

`mi_dia_detalle.html` tiene DOS formas, no una responsiva. En escritorio, la
tabla de 4 semanas lado a lado: su valor es comparar la progresión de un mismo
ejercicio semana a semana. En un teléfono de 360px esa tabla mide del orden de
2000px de ancho, y además responde una pregunta que el alumno no se está
haciendo: parado en el gimnasio entre series, mira **LA semana de hoy y un
ejercicio a la vez**. Abajo de `sm` la tabla se oculta (`hidden sm:block`) y en
su lugar va un carrusel con imán: una hoja por semana, a pantalla completa,
abierta en la semana en curso, con la siguiente asomando para que el gesto se
vea sin ningún cartel que diga «deslizá».

- **Acá SÍ se duplica markup**, al revés que en `.tabla--editable`. La tabla es
  fila-mayor (un ejercicio, sus 4 semanas) y las hojas semana-mayor: no hay CSS
  que transponga eso, así que las dos formas se renderizan siempre y una queda
  con `display: none`. Es seguro porque lo interactivo son un `<select>` de RPE
  y un botón de «entrenado», cada copia en su propio `<form>`, y la copia
  oculta no es alcanzable ni por teclado ni por lector de pantalla. Para que no
  puedan divergir, los dos salen a `templates/partials/rutina_calificacion.html`
  y `rutina_marcar_entrenado.html`. **Si tocás uno, tocá el partial, no una de
  las dos pantallas.**
- **`rutinas/agrupacion.py::listar_semanas_del_dia()`** transpone la salida de
  `listar_ejercicios_del_dia` a semana-mayor. Pura, Django-free y sin queries:
  reusa las filas ya agrupadas, así el celular y el escritorio no pueden
  mostrar cosas distintas. Un ejercicio que esa semana no tiene cargado se
  saltea (en la tabla ese hueco es un «—» porque una grilla no puede tener
  celdas faltantes; en una lista de una sola semana no hay nada que mostrar).
- **La prescripción es el titular de la tarjeta**, no una celda: `3 × 12–15` en
  una pieza (así lo escribe el entrenador y así lo lee el alumno), en
  `tabular-nums` para que las cifras no bailen entre hojas. Kilos y descanso
  aparecen **solo si están cargados** — un «Kilos —» en negrita de 20px es
  vacío gritado.
- **Doble señal en las pestañas, a propósito**: `var(--color-primario)` dice
  «esta es la que estás viendo» y el punto ámbar «esta es la semana en curso».
  Nunca significan lo mismo. El punto va `absolute`: ocupando lugar en el flex,
  «Semana 2» ya no entraba en su cuarto de pantalla y la pestaña activa partía
  el texto en dos renglones.
- **Nada de `position: sticky` adentro del carrusel.** `overflow-x: auto`
  convierte el otro eje en `auto`, así que un encabezado pegajoso ahí se pega
  al carrusel y no al viewport — el mismo gotcha que ya documenta la sección de
  `position: sticky` más arriba. El contexto lo da el encabezado de cada hoja.
- **`overscroll-behavior-x: contain`.** Sin eso, deslizar contra el borde
  dispara el gesto de «atrás» del navegador en Android: el alumno pierde la
  pantalla en vez de cambiar de semana.
- **La memoria de `sessionStorage` es de UNA navegación, no del historial.**
  Calificar un ejercicio y marcar el día entrenado son POST que redirigen a la
  misma URL **sin fragmento** (`RutinaAsignadaItemCalificarView`), así que sin
  ella el alumno que califica algo de la semana 2 vuelve tirado en la semana en
  curso. Por eso se escribe **solo al enviar uno de esos formularios** (en fase
  de CAPTURA: el `onchange` del desplegable llama a `form.submit()`, que no
  dispara el evento `submit` y arranca la navegación ahí mismo) y **se consume
  al restaurar**. La primera versión la escribía en cada scroll, desde el
  `IntersectionObserver`, y eso la volvía permanente para la pestaña: una
  pestaña de PWA vive semanas, así que el alumno que un martes de la semana 3
  desliza hasta la semana 1 para comparar cargas se quedaba abriendo la semana
  1 para siempre, incluso con un plan nuevo ya asignado. Lo encontró un
  `/code-review`. La posición se fija con
  `scrollLeft = hoja.offsetLeft - hojas[0].offsetLeft` (el origen es la primera
  hoja, no el carrusel, para no depender de su padding lateral) y **nunca con
  `scrollIntoView`**, que además scrollea la página en vertical.
- **La barra de pestañas se desliza** (`overflow-x: auto`). No es decorativo:
  con `whitespace-nowrap` cada pestaña se planta en su ancho de texto y no
  encoge más. Medido en un viewport de 320px (esta página anida dos veces
  `.contenido--ancho`, o sea `px-4` dos veces): la barra mide 256px y su
  contenido 279, así que «Semana 4» quedaba cortada — y con el
  `body { overflow-x: hidden }` que el proyecto usa como red de seguridad, no
  había forma de llegar a ella.
- **Costo aceptado del markup duplicado, medido**: un día real (11 ejercicios ×
  4 semanas) pesa 154,8 KB crudos pero **6,6 KB con gzip**, que es lo que
  viaja; el caso extremo de 43 ejercicios en UN día, 560,7 KB crudos y **13,5
  KB gzip**. Un `/code-review` lo marcó calculando sobre bytes SIN comprimir
  («23 MB de datos móviles»), que sobreestima unas 40 veces. Si alguna vez el
  peso importa de verdad, el número a mirar es el comprimido.
- **Gotcha de verificación, no de código**: en una pestaña de Chrome en
  segundo plano (`document.hidden`) el navegador congela `IntersectionObserver`
  y el scroll suave. Eso se ve exactamente igual que un carrusel roto — la
  pestaña no sigue al deslizar y el salto por pestaña no llega. Antes de dar
  por roto algo que dependa de rAF/IO, mirá `document.visibilityState`.
- **Cómo verificar un layout de celular desde el navegador de esta máquina**:
  `resize_window` informa éxito sin cambiar `innerWidth` y `osascript` no logra
  achicar la ventana de Chrome. Lo que SÍ funciona es un **iframe de 390px**
  (las media queries se evalúan contra el viewport del iframe). Django manda
  `X-Frame-Options: DENY` en las vistas pero **no** en `/static/`, así que
  alcanza con `curl` de la página autenticada a un `.html` temporal dentro de
  `static/`, abrirlo en un iframe angosto y borrarlo al terminar.

## Fechas: `timezone.localdate()`, nunca `timezone.now().date()`

`TIME_ZONE` es `America/Argentina/Buenos_Aires` (UTC-3), así que **entre las
21:00 y las 23:59 la fecha UTC ya es la de mañana**. Cualquier "hoy" del
dominio (qué se muestra, qué venció, de qué mes es una cuota) va con
`timezone.localdate()`; `timezone.now()` queda solo para timestamps y
duraciones, donde el instante es lo que importa.

No es teórico: llegó a producción en cuatro lugares a la vez (2026-09-02),
todos con la misma línea mal escrita y consecuencias distintas.

- **`novedades/models.py`** era el peor, porque combinaba dos: el default de
  `fecha_publicacion` fechaba en UTC, así que una novedad publicada a las
  22:00 nacía fechada para MAÑANA — y `notificaciones/signals.py` la tomaba
  por "programada a futuro" (ese sí compara contra `localdate()`) y **no
  mandaba el push**, que recién salía con el cron del día siguiente. Al alumno
  le aparecía igual en el portal, porque `visibles()` también cortaba en UTC:
  veía la novedad sin haber recibido el aviso. Ese mismo corte hacía visible
  esta noche una novedad programada para mañana.
- **`tenants/views.py::_metricas_dashboard`**: el último día del mes después
  de las 21:00, «Pagos del mes» mostraba las cuotas del mes SIGUIENTE
  (ninguna, el cron no las generó todavía) y el dueño veía su facturación en
  cero. «Alumnos con rutina» contaba planes que arrancan mañana, mientras el
  portal del alumno decía que no tenía rutina.
- **`pagos/management/commands/generar_pagos.py`**: corrido a mano el último
  día del mes por la noche, emitía las cuotas del mes siguiente con `dia=1`.
  La corrida agendada (06:30 UTC = 03:30 local) cae fuera de la ventana, así
  que nunca lo disparó sola — pero la corrida manual es la que se hace cuando
  algo ya salió mal.
- **Los tests tienen la misma trampa, y es peor porque no falla siempre.**
  `HomeViewAlumnoTests` armaba su "hoy" en UTC contra un `vigente_de` local:
  fallaba **solo entre las 21:00 y las 23:59**, sin relación con lo que
  estuvieras tocando. Un test de esto se escribe **congelando el reloj**
  (`patch("django.utils.timezone.now", ...)`) en un momento de esa ventana, y
  con una fecha LEJANA a hoy: con una cercana, el test compara contra la fecha
  real y pasa o falla por el motivo equivocado (pasó al escribir estos).
- **Ojo con el objetivo del patch**: `from django.utils.timezone import now`
  liga la función al importar, así que parchear `django.utils.timezone.now` no
  la alcanza. Por eso el código usa `from django.utils import timezone` y
  llama `timezone.localdate()` — resuelto en cada llamada, y testeable.

## `position: sticky` en una celda muere si la tabla tiene `overflow: hidden`

`.tabla` lleva `overflow-hidden` para que sus esquinas redondeadas recorten el
contenido. Cualquier ancestro con `overflow` distinto de `visible` pasa a ser
el contenedor de referencia del `sticky` de una celda — y como la tabla no
scrollea (scrollea `.tabla-scroll`), la celda no tiene rango donde pegarse y se
va de pantalla igual, **con `position: sticky` aplicado y sin ningún error**.
Medido en el navegador el 2026-09-07: `left: -717px` con `position: sticky`,
`left: 0px` y `z-index: 10` todos correctos en el estilo computado.

Por eso `.tabla--rutina-semanas` (las dos tablas de 17+ columnas, plantilla y
rutina asignada) revierte `overflow`/`rounded`/`border`/`shadow` y el marco lo
toma `.tabla-scroll:has(...)`. Si agregás otra columna fija, revisá primero el
`overflow` de TODOS los ancestros hasta el scroller.

**Dos trampas de verificación que costaron media hora acá**, las dos ya
conocidas y las dos silenciosas:
1. El **service worker** cachea `/static/` con estrategia cache-first, así que
   el `app.css` recompilado no llega al navegador aunque el servidor lo sirva
   bien. Se desregistra con `navigator.serviceWorker.getRegistrations()` +
   `unregister()` y `caches.delete(...)`, y **se vuelve a registrar en cada
   carga**, así que hay que repetirlo tras cada rebuild.
2. Un `runserver` que ya estaba corriendo siguió sirviendo el **template
   viejo** después de editarlo (verificado con `curl` autenticado: el HTML no
   traía la clase nueva y pesaba exactamente lo mismo). Reiniciarlo lo
   resolvió. Antes de sospechar del código, compará el HTML servido con el
   archivo en disco.

## Comentarios en templates: `{# #}` es de UNA sola línea

`{# ... #}` solo es comentario para Django si abre y cierra en la **misma
línea**. Con un salto de línea en el medio, Django lo imprime tal cual en la
pantalla del usuario. Para varias líneas: `{% comment %}...{% endcomment %}`.

No es teórico: llegó a producción con 8 casos en 5 templates, y el portal del
alumno mostraba el comentario **en lugar del nombre del ejercicio** (ver
`ISSUES.md` `[2026-08-31]`). Nada lo detecta solo — es HTML válido, no lanza
excepción y no deja log. Lo cubre `ComentariosDeTemplateTests`, que barre
TODOS los templates del proyecto.

## Vistas de staff (Fase 2)

Cada app de dominio tiene `forms.py`/`views.py`/`urls.py` (namespace propio,
p.ej. `alumnos:listado`, `rutinas:asignar`) y templates bajo
`templates/<app>/`. Todas las vistas de gestión combinan
`tenants.mixins.StaffRequiredMixin` (autorización por rol — solo `staff`,
403 para `alumno` o sin `Perfil`) con `core.mixins.TenantScopedMixin`
(aislamiento por tenant), `StaffRequiredMixin` primero en el MRO.

- **Nav**: `templates/base.html` muestra el menú de secciones solo si
  `user.perfil.rol == "staff"`.
- **Dashboard**: `tenants.views.HomeView` — **ya no está montada en ninguna
  URL propia**: desde el sitio público, la ruta `home` (`/`) es `PortadaView`,
  que para un anónimo sirve la landing de producto y para un autenticado
  delega en `HomeView` sin tocarla ni renombrarla (ver "Sitio público y embudo
  de solicitudes"). `HomeView` bifurca por `perfil.rol`. Para `staff`:
  métricas de Fase 2 §1 (alumnos activos, alumnos con pago pendiente, pagos
  del mes, rutinas activas, últimas novedades) +
  analítica (subproyecto 4, agregada después de Fase 6; el 5to gráfico
  "ejercicios más asignados" se sumó más tarde, ver abajo): asistencia por
  día/hora, alumnos por género, RPE por ejercicio, y ejercicios más
  asignados (general + desglosado por género). La agregación vive en
  `tenants/analitica.py` (no en la vista) porque cruza 3 apps (turnos,
  alumnos, rutinas) y se testea mejor sola. Asistencia agrupa TODO el
  historial de `Reserva` por día de semana + hora (no una ventana de
  tiempo) para revelar el patrón recurrente de horas pico — decisión
  explícita del dueño del producto; "ejercicios más asignados" sigue el
  mismo criterio (todo el historial, no solo rutinas activas). Los
  gráficos siguen la skill `dataviz`: la grilla de calor de asistencia es
  HTML/CSS puro (color secuencial azul; Chart.js no trae heatmap nativo
  sin plugin aparte), género y "ejercicios más asignados" (general) son
  una barra Chart.js de un solo color (las categorías ya se identifican
  por el eje), RPE por ejercicio es una barra apilada horizontal
  **divergente** azul↔rojo (mismo tratamiento que una escala Likert), y
  "ejercicios más asignados por género" es una barra apilada horizontal
  con una **paleta categórica** nueva de 4 colores (azul/naranja/aqua/
  amarillo, slots 1-4 del tema por defecto de `dataviz`, documentada en
  `DESIGN.md` § "Paleta categórica de dataviz") — cargados por CDN solo en
  `home.html`, no en todo el sitio. `ejercicios_mas_asignados_por_genero`
  reusa el ranking (mismo conjunto, mismo orden) de `ejercicios_mas_asignados`
  en vez de ordenar independiente, para que los dos gráficos se lean lado a
  lado sin que las barras cambien de orden entre uno y otro — el costo
  aceptado es correr la query de ranking dos veces por carga del
  dashboard (agregado liviano, acotado por gimnasio). "Ejercicios más
  asignados" cuenta CUALQUIER `RutinaAsignadaItem` asignado (a diferencia
  de RPE, que excluye `rpe=""`): mide qué se pone en las rutinas, no qué
  se calificó. Cada gráfico tiene su "Ver como tabla" (`<details>`, sin
  JS) como equivalente accesible. Para `alumno`: el portal de Fase 3 (su

  rutina activa, su cuota del mes, últimas novedades) — ver más abajo.
- **`RutinaPlantillaItem`/`RutinaAsignadaItem`** no son `TenantOwnedModel`
  (no tienen `gimnasio` propio): sus vistas resuelven el aislamiento
  buscando primero el padre vía `for_gimnasio()` antes de tocar el item — ver
  `rutinas/views.py` (`ItemPlantillaMixin`).
- `Cuota` sigue sin vista de "crear" — el staff solo confirma pagos ya
  autogenerados (principio no negociable §3).

## Portal del alumno y acceso (Fase 3)

- **Alta de gimnasios: nunca self-serve.** `/accounts/register/` era público
  y sin throttling (cualquiera creaba User + Gimnasio + Perfil STAFF y quedaba
  logueado); se cerró el 2026-07-29, ver `ISSUES.md`. Hoy hay TRES caminos y
  los tres pasan por la misma función, `tenants/services.py::crear_gimnasio`
  (que crea al dueño con `set_unusable_password()` cuando va a entrar por
  Google): `python manage.py crear_gimnasio`, el alta del panel
  (`plataforma:gimnasio_crear`) y la aprobación de una solicitud
  (`solicitudes.services.aprobar`). Los tres los ejecuta el dueño del
  producto, nunca el interesado. **No reintroduzcas una vista de registro**
  sin volver a discutir la decisión — y el formulario público de `/solicitar/`
  NO es una: no crea ningún tenant, deja un lead para moderar.
- **Acceso**: `Alumno.perfil` (`OneToOneField` a `tenants.Perfil`, nullable)
  vincula un alumno con su login. El staff lo crea/resetea desde la ficha del
  alumno (`alumnos:acceso_crear` / `alumnos:acceso_cambiar_password`,
  `alumnos/views.py::CrearAccesoView`/`CambiarPasswordAlumnoView`) — un form
  plano (no `ModelForm`), con la contraseña en texto plano en pantalla
  (`help_text` lo explica: es la única vez que se puede leer, el staff la
  tiene que copiar para pasársela al alumno). `username` es único GLOBAL
  (`auth.User`, sin namespacing por gimnasio) — el form lo valida y sugiere
  uno libre (mismo patrón que `tenants.services.slug_disponible`).
- **`fecha_activacion`**: se registra en el PRIMER login exitoso del alumno,
  no al crear el acceso — vía la señal `user_logged_in` en
  `alumnos/signals.py`, conectada en `AlumnosConfig.ready()`. Mide adopción
  real, no alta administrativa.
- **Portal**: `HomeView._portal_alumno` (mismo patrón de import tardío que
  `_metricas_dashboard`) resuelve `perfil.alumno` y agrega su rutina activa
  (con items), la cuota del mes actual y las novedades visibles al contexto;
  la plantilla lo renderiza en una sola pantalla mobile-first (ROADMAP
  Fase 3: "entiende su rutina sin explicación adicional"). Si el `Perfil` de
  rol `alumno` todavía no está vinculado a un `Alumno`, se muestra un estado
  vacío, no un error 500.
- **Menú y panel en dos columnas en la compu.** El alumno tiene un menú
  (`templates/partials/nav_alumno.html`, incluido por `base.html`; Inicio,
  «Mi rutina» → `home#mi-rutina`, Turnos y, con `puede_usar_tienda`, Tienda)
  y el inicio se parte en dos columnas desde `lg` (`.columnas`). El orden del
  HTML es el del celular y NO cambia: las columnas son sólo CSS, así que el
  celular queda idéntico. Cero queries nuevas: el menú usa
  `user.perfil.gimnasio`, que el `<head>` ya resolvió, y `puede_usar_tienda`
  es una propiedad sin consulta (`tenants/tests_nav_alumno.py` fija las 0
  queries del partial). Con la cuenta suspendida o con los alumnos bloqueados
  el menú no se renderiza: todas las secciones terminan en el mismo cartel.
  Las pantallas del alumno usan `contenido--alumno` (o `--lectura` la
  privacidad) y `.columnas`/`.con-lateral`; el comprobante de cuota muestra los
  medios de cobro con el mismo partial que el inicio
  (`partials/medios_cobro.html`). El detalle visual está en `DESIGN.md`
  («Layout» y «Navigation»); los tests, en `tenants/tests_home_alumno.py` y
  `tenants/tests_portal_pantallas.py`.

## Accesos, revocación y suplantación (Frente B)

Completa lo que a Fase 3 le faltaba para que el dueño opere sin llamar al
desarrollador. Spec y plan en `docs/superpowers/{specs,plans}/
2026-07-30-portal-de-cuentas-*`.

- **El identificador del alumno es su email o su teléfono**, a elección del
  staff. `alumnos/identidad.py` los normaliza (email a minúsculas —
  `User.objects.get(username=...)` es case-sensitive en Postgres, así que sin
  eso `Juan@x.com` y `juan@x.com` serían dos cuentas; teléfono a `+54...`
  sacando el `0` de característica y el `15`). El módulo es **Django-free a
  propósito** y se testea con `SimpleTestCase`: el riesgo real es que la
  normalización difiera entre el alta y el login, porque ahí el alumno no entra
  y no puede darse cuenta solo.
  - **No hace falta un `User` custom**: `UnicodeUsernameValidator` acepta `@` y
    `+` (regex `^[\w.@+-]+\Z`). Hay un test que fija ese supuesto.
- **La contraseña la genera SIEMPRE la app** (`alumnos/services.py`, reusando
  `tenants.services.generar_password`) y se muestra **una sola vez** en
  `acceso_credenciales.html`. **No pasa por `messages`**: `messages` se
  serializa en la sesión, que en este proyecto vive en la base de datos. El
  POST no redirige (se rompe PRG a propósito; el F5 lo cubre el guard de "este
  alumno ya tiene acceso").
- **`Alumno.estado` es el maestro del acceso**, y la sincronización con
  `User.is_active` vive en **un solo lugar**:
  `alumnos/signals.py::sincronizar_acceso_con_estado` (`post_save` sobre
  `Alumno`). **No la repitas en las vistas.** El estado se escribe desde tres
  caminos —el botón de baja, el form de la ficha (donde `estado` es editable) y
  `crear_acceso` sobre un alumno ya dado de baja— y ponerlo en cada vista
  garantiza que alguna se olvide; una revisión encontró exactamente eso, con
  dos de los tres caminos rotos. El receiver chequea `raw` para no repetir el
  problema de `calendario/signals.py`.
  - No hace falta invalidar sesiones a mano: `ModelBackend.get_user()`
    revalida `is_active` en CADA request. Regenerar la contraseña también
    expulsa al alumno, porque `get_session_auth_hash()` deriva del hash.
  - `crear_acceso` toma `select_for_update()` sobre el `Alumno` y traduce
    `IntegrityError` a `IdentificadorEnUso`: sin eso, un doble submit del form
    (va boosteado por htmx) creaba dos `User`+`Perfil` y dejaba uno huérfano
    que podía loguearse y no aparecía en ningún panel.
- **Panel `alumnos:accesos`**, colgado del listado de alumnos y **no del nav**
  (ya tiene 8 ítems tras el esfuerzo de bajarlo de 10; mismo criterio que el
  importador). El `select_related("perfil__usuario")` no es cosmético: sin él
  son 17 queries donde ahora hay 7, y hay un test que lo prueba comparando dos
  tamaños de conjunto (no un `assertNumQueries` fijo, que se rompe con cambios
  internos de Django).
- **NO se guardan contraseñas legibles.** Se pidió mostrar las de todos los
  alumnos en una sección y se descartó: ver `ISSUES.md`. La alternativa es
  suplantación + regeneración.
- **Suplantación** (`tenants/suplantacion.py`, servicio; auditada en
  `RegistroSuplantacion`, que SÍ es `TenantOwnedModel`). Reglas: solo staff,
  solo alumnos activos del propio gimnasio (404), nunca a otro staff ni a una
  cuenta con privilegios, no anidable, POST-only, y **máximo 2 h** — el límite
  lo aplica `tenants/middleware.py::ExpirarSuplantacionMiddleware`, que es el
  único middleware propio del proyecto: la expiración tiene que evaluarse en
  cada request y no hay otro lugar donde hacerlo.
  - `iniciar()` también chequea `usuario.is_active`, porque **`login()` no lo
    valida**: con un usuario desactivado la suplantación "funcionaba" y el
    staff perdía su sesión en el request siguiente, sin poder ni volver.
  - **Las dos trampas de `django.contrib.auth.login()`**, cada una con test de
    regresión. (1) `login()` hace `session.flush()` al cambiar de usuario: la
    clave de retorno se escribe **DESPUÉS**, nunca antes. (2) `login()` emite
    `user_logged_in`, y dos receivers corromperían datos —
    `alumnos/signals.py` estamparía `fecha_activacion` a un alumno que nunca
    entró, y `update_last_login` pisaría el "último ingreso" del panel. Se
    resuelven con `request._suplantacion_en_curso` y un `UPDATE` de
    restauración. **Nunca con `signal.disconnect()`**: muta estado global y no
    es thread-safe.
  - `last_login` se lee de la BASE, no del objeto en memoria: con una
    instancia desactualizada, "restaurar" borraría el valor real.
  - `volver()` es fail-closed y revalida TODO contra la base, **incluido que
    el staff sea del mismo gimnasio** — sin eso, una sesión manipulada
    permitía saltar de tenant.
  - `VolverDeSuplantacionView` **no** lleva `StaffRequiredMixin`: durante la
    suplantación el usuario es el ALUMNO, y exigir rol staff dejaría al staff
    atrapado.
  - **Conectar/desconectar Google Calendar está bloqueado mientras se
    suplanta**: el flujo OAuth usa la cuenta de Google de quien está frente al
    navegador, así que el staff vincularía la suya al calendario del alumno.
  - **Deuda para un futuro `PerfilModelBackend`**: `tenants/suplantacion.BACKEND`
    apunta a `ModelBackend`. El login con Google (Frente C, ver sección propia
    más abajo) ya existe y también loguea con `backend="...ModelBackend"`
    explícito, así que esta deuda sigue exactamente igual que antes — si
    algún día aparece `PerfilModelBackend` (y django-axes por delante), hay
    que actualizar los DOS lugares, o `login()` elige mal el backend.

## Turnos, reservas y Google Calendar (más allá del ROADMAP original)

Agregado después de Fase 4, fuera del scope que describe `ROADMAP.md` (que
llama "Fase 6" al primer piloto pago, no a esto) — el detalle real vive en
`ISSUES.md` y en los commits ("Fase 6, Task N" para turnos; "Parte A/B/C"
para la migración de reservas desencajadas y Google Calendar).

- **`turnos`** (`turnos/models.py`, `turnos/services.py`): agenda de clases
  con cupo. `ConfiguracionTurnos` (duración + cupo default, una fila por
  gimnasio) + `HorarioAtencion` (franjas por día de semana) + `CupoExcepcion`
  (pisa el cupo un día/horario puntual, incluso a 0) generan la grilla;
  `Reserva` es lo que un alumno ocupa. Toda la lógica de negocio (crear
  reserva, cancelar, calcular la grilla semanal) vive en `services.py`, no en
  las vistas ni en los modelos — `crear_reserva()` toma
  `select_for_update()` sobre `ConfiguracionTurnos` para serializar altas
  concurrentes contra el cupo.
- **Reservas desencajadas**: cuando el staff cambia horarios/duración,
  reservas existentes pueden quedar fuera de cualquier franja vigente.
  `reconciliar_reservas_desencajadas()` las reubica en la franja vigente más
  cercana (o las cancela si no hay ninguna) y llama a
  `_generar_novedades_personales()` para avisarle a cada alumno afectado vía
  una `Novedad` dirigida a él. **Riesgo aceptado a propósito**: esta función
  NO toma lock (a diferencia de `crear_reserva()`) porque solo corre cuando
  el staff edita su propia grilla — ver la entrada `[2026-07-06]` en
  `ISSUES.md` para el razonamiento y cómo cerrarlo si hiciera falta.
- **`calendario`** (`calendario/models.py`, `calendario/services.py`):
  integración **opcional** con Google Calendar, por alumno (no por
  gimnasio). `GOOGLE_CALENDAR_ENABLED` en `settings.py` se activa solo si las
  4 env vars `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI` +
  `GOOGLE_TOKEN_ENCRYPTION_KEY` están seteadas (todas o ninguna — falla al
  arrancar si están parciales). Scope usado:
  `calendar.app.created` (NO da acceso al calendario principal del alumno,
  solo a un calendario secundario que la app crea, "Turnos de {gimnasio}").
  Tokens (`refresh_token`/`access_token`) se guardan cifrados con
  `EncryptedTextField` (`calendario/fields.py`, usa `cryptography` +
  `GOOGLE_TOKEN_ENCRYPTION_KEY`), nunca en texto plano. La sync
  reserva→evento es **síncrona** vía `transaction.on_commit` (no hay
  Redis/Celery/django-q en este proyecto).
  - Ninguno de los dos modelos de `calendario` es `TenantOwnedModel`: se
    scopean a través de su FK (`alumno`/`reserva`), que ya está acotada por
    gimnasio — mismo precedente que `NovedadLeida`.
  - **Gotcha de PKCE**: `google-auth-oauthlib` activa PKCE por defecto;
    `build_authorization_url()` tiene que devolver y persistir el
    `code_verifier` en la sesión (no solo `state`) para que el callback
    pueda reconstruir el mismo `Flow` — si armás un `Flow` nuevo en el
    callback sin pasarle el verifier original, Google devuelve
    `invalid_grant: Missing code verifier`. No lo detectan los tests que
    mockean `build_authorization_url`/`intercambiar_code`: hace falta un
    test que ejercite connect→sesión→callback sin mockear ninguna de las
    dos puntas. Ver `ISSUES.md` `[2026-07-08]`.
  - **Gotcha de hx-boost**: cualquier link que dispare un redirect
    cross-origin (como "Conectar Google Calendar", que redirige a
    `accounts.google.com`) necesita `hx-boost="false"` explícito — htmx
    intercepta el click, hace el GET por XHR, y no puede seguir un redirect
    cross-origin, así que el click queda tragado sin error visible. Mismo
    criterio que los forms de upload de archivo (ver "UI y white-label"
    abajo).

## Categorías de ejercicio por gimnasio (más allá del ROADMAP original)

Agregado el 2026-08-26 tras un defecto reportado por el primer cliente pago:
subió un Excel de 748 ejercicios con tres columnas (`NOMBRE`, `LINK`,
`CATEGORÍA`) y el importador se lo dejó entero sin clasificar, pidiéndole
elegir el grupo muscular de cada uno a mano.

Eran **dos defectos distintos**, y el segundo no se arreglaba con un parche:

1. `ALIAS_BIBLIOTECA["grupo_muscular"]` no incluía `"categoria"`, así que la
   columna no se detectaba.
2. Aunque se detectara, 11 de sus 13 categorías no mapeaban a nada.
   `Ejercicio.GrupoMuscular` era un `TextChoices` **anatómico, global y
   cerrado** (pecho/espalda/piernas/...) y este gimnasio clasifica por
   **patrón de movimiento** (EMPUJE, TRACCIÓN, RODILLA, CADERA) más bloques
   (INTERMITENTE, DEPORTIVOS, MOVILIDAD, ACCESORIOS) y skills (MUSCLE UP,
   HANDSTAND, SKILLS ANILLAS). Solo CORE coincidía, por casualidad.

- **`CategoriaEjercicio(TenantOwnedModel)`** reemplaza al `TextChoices`.
  `nombre_normalizado` (calculado en `save()`, no editable) existe solo para
  sostener `UniqueConstraint(gimnasio, nombre_normalizado)`: sin esa clave,
  "CORE", "Core" y "core" serían tres filas distintas, que es exactamente lo
  que haría el importador con un Excel donde la misma categoría viene escrita
  de varias formas. **La etiqueta visible en toda la UI es "Categoría"**, no
  "Grupo muscular" — MOVILIDAD o MUSCLE UP no son grupos musculares.
- **`Ejercicio.categoria` es nullable a propósito**: el Excel real trae una
  fila con la celda vacía y el importador no debe trabarse por eso. En el
  formulario de alta manual **sí es obligatoria** (`EjercicioForm.clean`), o
  se acumulan ejercicios que no salen en ningún filtro.
- **El form de ejercicio deja crear una categoría sin cambiar de pantalla**
  (`categoria_nueva`, un `CharField` que no es del modelo). Va por
  `get_or_create` sobre `nombre_normalizado`, así que escribir "core" con
  "CORE" ya cargada reusa la que hay.
- **Siembra asimétrica, a propósito.** A un gimnasio NUEVO
  (`crear_gimnasio` → `ejercicios/services.py::sembrar_categorias_iniciales`)
  se le crean las 8 de siempre como punto de partida editable. A los
  gimnasios YA existentes, la migración `ejercicios/0003` les crea **solo las
  que de verdad usan**: ahí hay datos reales de los cuales deducir el
  catálogo, y un gimnasio funcional no tiene por qué arrancar con seis
  categorías anatómicas vacías.
- **El snapshot de rutinas guarda el NOMBRE VISIBLE, no un slug.** Es el
  punto más frágil de todo el cambio: `rutinas/agrupacion.py` traducía
  `"cuerpo_completo"` → `"Cuerpo completo"` con un dict module-level armado
  desde las choices globales. Con catálogo por tenant ese dict deja de ser
  correcto. Guardando el nombre ya renderizado, `agrupacion.py` queda en
  `item.categoria_snapshot or "Sin categoría"` — sin lookup, sin importar
  `ejercicios`, y por fin Django-free de verdad (su docstring ya lo decía).
  **Si tocás el snapshot, no vuelvas a meter un lookup contra un catálogo
  global.**
- **Dedupe difuso al importar** (`importaciones/matching.py::resolver_categorias`,
  función pura, mismo patrón que `resolver_nombre`). Tres intentos: exacto
  contra el catálogo, `fuzz.ratio` ≥ `UMBRAL_CATEGORIA` (85) contra el
  catálogo, y ≥85 contra las ya encoladas en ESA importación (lo que evita
  que "TRACCIÓN" y "TRACION" del mismo archivo queden como dos categorías).
  **El 85 está medido, no elegido a ojo**: sobre las 12 categorías reales del
  cliente más las 8 sembradas, el par DISTINTO más parecido puntúa 61.5
  (`Hombros`/`Brazos`) y el typo más flojo de los que deben fusionarse da
  88.9 (`MOVILIDAD`/`MOBILIDAD`). Hay tests fijando los dos bordes: si
  cambiás el umbral, fallan. Se usa `fuzz.ratio` y **no `WRatio`** (el de los
  nombres de ejercicio): los nombres de categoría son palabras cortas donde
  las heurísticas de WRatio inflan el puntaje y fusionarían categorías
  legítimamente distintas.
- **El preview sigue sin escribir en la base**: guarda
  `categoria_resuelta = {"tipo": "nueva", "nombre": ...}` en el JSON y las
  crea `confirmar_importacion_biblioteca` con `get_or_create` dentro de la
  transacción que ya existía.
- **CRUD** en `ejercicios:categorias_*`, molde de `pagos.MedioCobro`, sin
  `DeleteView` (desactivar es destildar `activo`, y `on_delete=PROTECT` lo
  respalda). Entra desde el listado de Ejercicios, **no desde el nav** (ya
  tiene 8 ítems). `CategoriaEjercicioForm.clean_nombre` traduce el choque
  contra la `UniqueConstraint` en un error de campo: sin eso, renombrar a una
  categoría existente escrita distinto daba un 500.
- **`Ejercicio.grupo_muscular` sigue en la base, sin uso** (expand/contract):
  se dejó un release para que la vuelta atrás sea revertir el código. **Borrarla
  es un commit pendiente**, una vez confirmado que producción está sana.

## Importador de Excel (Proyecto 2)

Agregado después de Fase 6, fuera del scope original de `ROADMAP.md` (lo
llama "Proyecto 2" en el spec/plan de `docs/superpowers/`) — deja que el
staff cargue rutinas y ejercicios en lote desde un `.xlsx` en vez de
hacerlo fila por fila desde el panel. Spec y plan completos en
`docs/superpowers/specs/2026-07-27-importador-planes-entrenamiento-design.md`
y `docs/superpowers/plans/2026-07-27-importador-planes-entrenamiento-plan.md`
(14 tareas, cada una con su propia revisión — la Tarea 14 y el fix wave post
revisión-final surgieron de una revisión de rama completa, no estaban en el
plan original).

- **`importaciones`** (`models.py`, `parsing.py`, `matching.py`,
  `services.py`, `forms.py`, `views.py`): dos flujos independientes,
  namespace `importaciones:` — `plantillas_subir`/`plantillas_preview` (crea
  `RutinaPlantilla`) y `biblioteca_subir`/`biblioteca_preview` (crea
  `Ejercicio`). Mismo patrón subir → previsualizar → confirmar en los dos:
  `previsualizar_importacion_*` parsea el archivo y crea una fila
  `Importacion` (`TenantOwnedModel`, `resultado` es un `JSONField` con todo
  lo necesario para el preview y el confirm — nunca vuelve a abrirse el
  archivo original) sin tocar `RutinaPlantilla`/`Ejercicio`;
  `confirmar_importacion_*` recién ahí escribe, adentro de una transacción
  con `select_for_update()` sobre la `Importacion` (mismo patrón anti-TOCTOU
  que el resto del repo — evita doble confirmación concurrente).
- **Entrada al importador desde el listado, no desde el nav**: `Importar
  rutinas`/`Importar ejercicios` ya no son items propios del nav-staff
  (`base.html`) — el alta manual y la importación desde Excel de cada
  dominio quedan juntas en su pantalla de listado (`rutinas/plantilla_list.
  html`, `ejercicios/ejercicio_list.html`), un botón `.boton` ("Nueva
  plantilla"/"Nuevo ejercicio") junto a uno `.boton-secundario` ("Importar
  desde Excel") en el mismo `.acciones-lista`, mismo patrón que ya usaba
  `pagos/pago_list.html` con dos acciones secundarias. Acorta el nav de 10 a
  8 items y pone las dos formas de cargar datos en el mismo lugar en vez de
  dispersas.
- **`parsing/`** es un paquete Django-free a propósito (testeable con
  `SimpleTestCase`, sin DB): `comun.py` (normalización, alias, detección de
  columnas, dataclasses, celdas), `tabular.py` (los dos lectores de "un
  encabezado, un registro por fila") y `ancha.py` (la matriz por semanas).
  `__init__.py` es una **fachada sin lógica** salvo el despachador de layout:
  la ruta `importaciones.parsing` **no se puede romper**, la importan seis
  módulos incluidas dos migraciones históricas (`rutinas/0006`,
  `ejercicios/0003`). Fila inválida = se salta y se lista con motivo, nunca
  invalida la hoja entera (salvo que falte una columna REQUERIDA en TODA la
  hoja, ahí se excluye esa hoja sola, no el archivo).
- **Dos layouts, y el ORDEN de detección es lo más importante del diseño**
  (2026-08-31, a raíz de que la planilla real del primer cliente pago daba 0
  ejercicios; ver `ISSUES.md`). La **matriz ancha** — una fila por ejercicio y
  las semanas a lo ancho, encabezado en dos filas, día en celda combinada a la
  izquierda, código de bloque + nombre en columnas separadas — **se prueba
  SIEMPRE antes** que la tabla larga. Al revés, una hoja ancha matchea igual el
  layout largo (su fila de grupos tiene `EJERCICIOS`, la de subcampos tiene
  `Series`/`Reps`/`Carga`) y produce filas plausibles con las columnas
  corridas: basura silenciosa, peor que cero items. Al revés no puede pasar
  porque `RE_SEMANA` exige el dígito y el `Semana` a secas del layout largo no
  matchea. **Si tocás el despachador, no inviertas ese orden.**
- **En la matriz ancha una fila de Excel produce hasta un item POR SEMANA**, y
  eso cambia la cardinalidad de `FilaInvalida`: un `series` malo en la semana 2
  no puede invalidar las otras tres, y el preview agrupa los motivos por fila
  para no listar la misma cuatro veces.
- **La fila de títulos no tiene que ser la primera.** `buscar_fila_encabezado`
  mira las primeras `FILAS_BUSQUEDA_ENCABEZADO` (15) y se queda con la PRIMERA
  que tenga todos los campos requeridos — no con "la que más detecta", que en
  una matriz por semanas elegiría la fila de subcampos y correría todas las
  columnas. Esto hizo que un archivo con un título arriba de la tabla, que
  antes se rechazaba con un mensaje pidiendo borrar esa fila, **ahora se
  importe bien**; los dos tests de biblioteca que fijaban el comportamiento
  viejo se reescribieron, con el porqué en el docstring de cada clase.
- **El staff elige qué hojas importar** (`plantillas/<pk>/hojas/`,
  `SeleccionHojasView`) entre subir y previsualizar: el archivo real trae 7
  hojas y 6 son auxiliares. La elección va en `resultado["hojas_elegidas"]`
  (lista de nombres) — sin campo de modelo nuevo, sin estado nuevo y sin
  reabrir el archivo. **El pareo hoja↔decisión es POR NOMBRE, no posicional**:
  filtrando hojas, alinear por índice crea plantillas con el objetivo y el
  nivel de otra hoja, en silencio.
- **`plantillas/ejemplo.xlsx`** genera al vuelo un archivo de ejemplo listo
  para llenar, con los encabezados y la hoja de ayuda derivados de
  `ALIAS_PLANTILLA`. **No lo conviertas en un binario versionado**: se
  desincronizaría del parser sin que nadie se entere. Hay un test que lo pasa
  por el propio importador.
- **`matching.py`**: matching difuso de nombres de ejercicio contra la
  biblioteca del gimnasio vía `rapidfuzz` (`PISO_SCORE=60`,
  `UMBRAL_AMBIGUO=87` — por debajo de 60 es "nuevo", 60-86 es "ambiguo",
  ≥87 se trata como confiable). Un match ambiguo NUNCA se resuelve solo:
  queda pre-marcado "usar existente" pero el staff tiene que elegir
  activamente en el preview (plantillas vía `ResolucionEjercicioFormSet`;
  biblioteca vía el mismo campo JSON único que ya lleva `grupo_muscular`,
  ver el punto siguiente). `Ejercicio.grupo_muscular` nuevo nunca tiene
  default silencioso — choices cerradas, el staff lo elige siempre.
- **Gotcha de escala (biblioteca)**: el flujo de biblioteca reemplaza el
  patrón de "un form de Django por ejercicio pendiente" (el que sí usa
  plantillas) por un único campo JSON serializado a mano con un poco de JS
  vanilla (sin build, sin Alpine) — una biblioteca real puede traer miles de
  filas, y un formset de ese tamaño rompe
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` (default 1000 de Django). Si tocás este
  flujo, NO reintroduzcas un formset por ítem ahí — ver `ISSUES.md`
  `[2026-07-28]` para el detalle completo (incluye el caso simétrico de
  plantillas, aceptado como riesgo documentado en vez de arreglado, porque
  el dueño confirmó que una plantilla real nunca supera ~300 ejercicios
  distintos).
- **Gotcha de escala #2 (confirmar): el costo en queries tiene que depender
  del catálogo, no de la cantidad de filas.** `confirmar_importacion_biblioteca`
  hacía dos queries por fila (el `SELECT` de la categoría + el `INSERT` del
  ejercicio) y el Excel real de un cliente (748 ejercicios) se comía los 30 s
  de timeout de gunicorn: **502 en producción** (ver `ISSUES.md`
  `[2026-08-27]`). Hoy `_CatalogoCategorias` lee las categorías del gimnasio
  una sola vez y `Ejercicio.objects.bulk_create()` inserta todo junto — 7
  queries para 200 ejercicios, las mismas que para 20, fijado por
  `ImportacionBibliotecaEscalaTests`. **No vuelvas a meter una query adentro
  del loop de items** en ninguno de los dos flujos de confirmación. El flujo de
  PLANTILLAS tenía exactamente el mismo defecto y se corrigió el 2026-08-31
  (139 → 57 queries con el archivo real): los ejercicios se resuelven todos de
  una antes de tocar las plantillas, y `ImportacionPlantillasEscalaTests` fija
  las dos propiedades por separado — el costo no crece con la cantidad de
  FILAS, y no crece linealmente con la cantidad de EJERCICIOS.
- **Los largos de campo se validan en el preview, no se descubren en el
  `INSERT`.** SQLite no valida el largo de un `varchar` y Postgres sí: dos
  links de 306 caracteres en el Excel de un cliente eran un `DataError` que
  abortaba la transacción y se llevaba puestos los otros 746 ejercicios, con
  un 500 mudo (ver `ISSUES.md` `[2026-08-27]`). `url_video` pasó a
  `max_length=500` y `_motivo_si_no_entra()` descarta la fila en el preview,
  con motivo y número de fila, leyendo los límites de `Ejercicio._meta` en vez
  de copiarlos. Misma familia de trampa que `select_for_update()` siendo
  no-op en SQLite: **si un campo puede desbordar, el test local no te lo va a
  decir.**
- **El desplegable de "Ejercicios a resolver" ofrece las categorías que el
  gimnasio YA tiene Y las que ese mismo archivo va a crear.** Las segundas
  todavía no tienen pk (el preview no escribe en la base), así que viajan por
  nombre con el prefijo `nueva:` en el `<option>` y como `categoria_nueva` en
  el JSON de resoluciones; `_categoria_para()` las valida contra los
  `nombre_normalizado` de `categorias_a_crear` de ESA importación antes de
  crear nada — es, para el nombre, el equivalente del re-fetch scopeado que ya
  protege a `categoria_id`. **El string del POST se usa solo como clave de
  búsqueda: lo que se persiste es el nombre canónico del preview**
  (`nuevas_permitidas` es un dict, no un set) — `normalizar_texto` colapsa
  espacios internos y `save()` solo hace `.strip()`, así que confiar en el
  string del cliente permitía desbordar el `varchar(60)` y voltear la
  transacción entera. Sin esto, un gimnasio importando por primera vez
  (catálogo vacío) no tenía NINGUNA categoría real donde ubicar una fila con la
  celda de categoría en blanco: la única salida era «Sin categoría» y
  arreglarlo después a mano (ver `ISSUES.md` `[2026-08-27]`). «Sin categoría»
  sigue siendo la salida cuando el archivo directamente no trae columna de
  categoría.
- **El preview muestra el video de cada fila, no un "Estado" constante.** La
  columna `LINK` del Excel se parsea desde siempre pero no se veía en el
  preview, y la columna que ocupaba su lugar decía "Nuevo" en las 748 filas
  (ver `ISSUES.md` `[2026-08-27]`). "Ya existe" quedó como badge al lado del
  nombre: aparece solo cuando de verdad hay algo que decir.
- **Ningún `ModelChoiceField` por ítem en un formset** (2026-09-07).
  `ResolucionEjercicioForm.categoria` lo fue durante dos semanas: un form por
  ejercicio nuevo, y cada `ModelChoiceField` cuesta una query al renderizar
  (`ModelChoiceIterator`, que además usa `.iterator()` = un cursor de servidor
  en Postgres) y otra al validar. Contra el pooler de Neon eso mató un worker
  en producción con el archivo del primer cliente (ver `ISSUES.md`
  `[2026-09-07]`). Hoy es un `TypedChoiceField` con las categorías del
  gimnasio calculadas UNA vez en la vista (`PreviewPlantillasView._categorias`)
  y compartidas por `form_kwargs`; la barrera de tenant sigue en
  `confirmar_importacion_plantillas`, que re-resuelve el pk contra el catálogo
  del gimnasio. `PreviewPlantillasEscalaTests` fija que GET y POST no crecen
  con la cantidad de ejercicios nuevos.
- **Un archivo de una sola hoja saltea la pantalla de elegir hojas** (todo
  PDF, y el Excel de una hoja como el del cliente): con una sola hoja esa
  pantalla era un «Continuar» obligatorio.
- **El preview muestra solo los avisos de las hojas ELEGIDAS** (2026-09-21).
  Se guardan por hoja (`hojas[].advertencias_columnas`) además de la lista
  global, y `services.advertencias_para_preview()` los filtra con fallback a
  la global para JSON anteriores. El archivo real del cliente trae 11 hojas y
  las 10 auxiliares producían nueve avisos ámbar que el dueño leyó como «se
  leyó mal» (ver `ISSUES.md [2026-09-21]`).
- **Objetivo y Nivel son opcionales** en el preview y en el modelo
  (`blank=True`, `rutinas/0015`): el dueño sube el archivo y confirma. Lo
  único obligatorio es la **categoría de cada ejercicio nuevo**, y el rechazo
  se resume ARRIBA de la página (`.aviso-error`, con los nombres y un link a
  «Ejercicios a resolver»): el error por fila queda al final de cientos de
  filas y el POST vuelve al tope, así que solo eso parecía «no hace nada».

### Importar alumnos (2026-10-15)

Un gimnasio que llega con su padrón en una planilla (cientos de fichas) lo
cargaba de a uno. `importaciones:alumnos_subir`/`alumnos_preview` (botón
«Importar desde Excel» en el listado de alumnos, no en el nav) calcan el flujo
de biblioteca —subir → previsualizar → confirmar— sobre `.xlsx` y `.csv`. Spec
en `docs/superpowers/specs/2026-10-15-importar-alumnos-design.md`, plan en
`docs/superpowers/plans/2026-10-15-importar-alumnos.md`. Piezas:
`importaciones/parsing/alumnos.py` (lector Django-free),
`importaciones/alumnos.py` (preview, confirmación, descarte y el ejemplo),
`Importacion.Tipo.ALUMNOS` y `Alumno.importado`. Las acciones sobre los
alumnos ya cargados tienen sección propia: «Acciones en lote del listado de
alumnos».

**La regla que ordena todo: no inventar ni perder en silencio.** Un dato
opcional ilegible o que no entra deja el campo vacío (o en su default), la fila
lleva un aviso y el texto original se agrega a `observaciones` en UNA línea
(`Datos del archivo que no se pudieron cargar: Sexo «X»; …`). Una fila se
descarta solo si falta nombre o apellido (y no se puede partir), si superan
`max_length` (120) o si es la fila de ejemplo (`MARCA_FILA_EJEMPLO`, chequeada
antes de agregar nada a observaciones). **Quien escribe esa línea es un solo
lugar, `_componer_observaciones` de `importaciones/alumnos.py`**: el lector
devuelve `no_cargados` por fila y no toca observaciones, porque el servicio
detecta además email, teléfono y largos que el lector no ve.

- **Los negativos de salud no son un «sí».** Una columna «Discapacidad» llena de
  «Ninguna»/«No tiene»/«N/A»/«-» es el formato más común de una ficha real:
  tomarla como «sí, con detalle “Ninguna”» habría inventado un dato de salud
  para cada alumno. En `tiene_*` un negativo da falso sin detalle ni aviso, y
  un texto que no es ni negativo ni afirmativo («Sordera») da verdadero con el
  texto como detalle y aviso. **Esto corrige la redacción original de la
  spec** («otro texto no entendido en `tiene_*` → verdadero»): ganó el
  principio rector, y la spec se corrigió en el mismo fix. Lo fijan
  `tests_alumnos_parsing` `test_negativos_en_tiene_no_inventan_datos_de_salud`
  y `test_texto_libre_que_no_es_negativo_sigue_siendo_si`. **Los negativos son
  una lista cerrada** (`_FALSO`, `_DETALLE_VACIO` y `_NEGATIVOS_CONOCIDOS`:
  «no tiene», «no posee», «no presenta», «no refiere», «ninguna conocida»,
  «ningún problema», «nada»…, como frases enteras), nunca un prefijo: «No ve
  bien» o «No camina sin ayuda» SON el dato de salud. Un texto que empieza con
  «no»/«ningún» y no está en la lista (`empieza_negando`) queda en falso **con
  aviso y el texto original en `no_cargados`** (o sea, en observaciones): ni un
  sí inventado ni un no que pierde el dato. `detalle_informa` usa los mismos
  negativos, así que un detalle «No tiene» no marca `tiene_*`. Lo fija
  `test_un_texto_que_empieza_con_no_queda_en_no_con_aviso_y_no_se_pierde`.
- **Sexo con una letra: «M» = masculino, «F» = femenino**, siempre (también
  «H»/«V» = masculino). Hubo una versión que leía «M» como «mujer» si la
  columna traía «H»; el dueño del producto la descartó el 2026-10-16: en una
  ficha argentina «M» es masculino. Lo fija `test_m_es_masculino_aunque_la_columna_traiga_h`.
- **Nombre completo contra nombre/apellido**: gana el nombre completo cuando
  matcheó EXACTO y nombre/apellido solo por prefijo («Alumno | Nombre del
  tutor»); la columna que no se usa queda en `columnas_ignoradas`. El orden de
  `ALIAS_ALUMNOS` importa (`detectar_columnas` resuelve el prefijo en ese
  orden), y «Fecha de alta» **no** es alias de «Inicio del ciclo»: es el día que
  se anotó, quizá hace años.
- **«Inicio del ciclo» y «Estado» son columnas opcionales.** Una fecha fuera de
  `[hoy − 27, hoy + 60]` o un estado ilegible → vacío/ACTIVO con aviso y texto a
  observaciones. `hoy` lo pasa quien llama (`timezone.localdate()`); el lector
  no lo calcula.
- **Duplicados** (`_ClavesDeAlumnos`, UNA query sobre el gimnasio, activos e
  inactivos): clave de email en minúsculas, de teléfono
  (`identidad.clave_telefono`: `+54…`, sin el 9 de celular, 10 dígitos
  nacionales; solo para comparar, `normalizar_telefono` del login no se toca) y
  de nombre (tokens ordenados, no depende de dónde se cortó entre nombre y
  apellido). Es duplicado si coincide contacto **y** nombre, o si el nombre
  coincide y **a alguno de los dos lados le falta contacto**: la fila no trae
  email ni teléfono, o el alumno de la base (o la fila anterior del archivo) no
  tiene ninguno. El segundo lado es el gimnasio que cargó a mano solo el nombre
  y después importa el padrón con celulares: comparado por contacto daba
  distinto, la persona quedaba duplicada y se le cobraba dos veces. Cada
  identidad del índice guarda `tiene_contacto` (en la base, email o teléfono
  no vacío; en el archivo, `trae_contacto`). **La excepción son las familias,
  con contacto en los dos lados: contacto igual con nombre distinto se crea con
  aviso** (padres e hijos comparten celular), y nombre igual con contacto
  distinto también. Nunca se actualiza a un existente. Dentro del archivo gana
  la primera aparición.
- **`trae_contacto` se mide ANTES de validar y viaja en el JSON.** Un homónimo
  con email inválido o teléfono ilegible *tiene* contacto en el archivo aunque
  el valor limpio quede vacío; si se midiera después, el preview lo prometía
  como «a crear» y la confirmación lo salteaba como duplicado de un nombre
  igual: pérdida silenciosa de una persona distinta. La confirmación lee
  `fila.get("trae_contacto", <fallback por claves>)` (una `Importacion`
  EN_REVISION anterior al deploy no tiene la clave). Fijan
  `ConfirmarContactoInvalidoTests` (cada caso exige que se creen tantos como
  prometió el preview) y
  `DuplicadosTests.test_homonimo_con_email_invalido_se_crea_con_aviso`. Preview y
  confirmación comparten `_ClavesDeAlumnos.evaluar`: **no dupliques la regla**.
- **Por qué `bulk_create`, y qué completa a mano.** Mismo criterio que
  `confirmar_importacion_biblioteca`: el costo en queries depende del
  catálogo, no de las filas (hay test de escala, 20 vs 200). Pero `bulk_create`
  no corre `pre_save` ni señales, así que **el servicio hace a mano lo que haría
  el alta**: `importado=True`, `fecha_baja=None` (un inactivo importado no tiene
  fecha de baja real y no aparece en el gráfico de bajas), sexo implícito si el
  gimnasio no es mixto y vino vacío, y **el ancla tal cual viene del archivo**,
  sin que el `pre_save` la estampe en hoy. No crea `User`/`Perfil`: el acceso se
  da después, uno por uno.
- **`Alumno.importado` existe solo para la analítica.** Las ALTAS (el gráfico y
  «Altas del mes», que cuentan por `creado`) excluyen `importado=True`: cargar el
  padrón de un día no es crecimiento. Las bajas cuentan igual (una baja
  posterior pasa por `save()` real). Lo fijan `tenants.tests`
  `test_un_alumno_importado_no_cuenta_como_alta_en_el_grafico` y
  `test_un_alumno_importado_no_suma_a_las_altas_del_mes`.
- **`nacen_vencidas`**: cuántas filas ACTIVAS a crear nacerían con el acceso
  pausado esa noche (`pagos.acceso.nace_bloqueada`); los inactivos no cuentan
  porque nunca bloquean. El preview lo avisa en ámbar.
- **La cuenta demo no puede importar alumnos** (`gimnasio.es_demo` →
  `ImportacionInvalida` antes de leer el archivo, `test_cuenta_demo_se_rechaza_antes_de_leer`):
  es compartida y un padrón real con datos de salud quedaría a la vista de
  otros prospectos hasta 6 h. De paso, una observación igual a `"[demo]"`
  (`tenants.demo.MARCA`) se reescribe `"[demo] (importado)"` con aviso, porque
  `sembrar_demo --borrar` borra por esa marca exacta y se llevaría a un alumno
  real.
- **Topes y CSV.** `MAX_FILAS_ALUMNOS = 1000` (más → rechazo entero, no se
  trunca; el lector levanta apenas pasa el tope y el mensaje dice «más de
  1000»), 200 filas vacías seguidas cortan la lectura con aviso (cubre un
  `max_row` inflado) y 2 MB de archivo (`SubirAlumnosForm`; 1000 filas llenas
  pesan ~76 KB). **Con el worker único, cargar un xlsx entero para rechazarlo
  es el riesgo** (5 MB y ~72.000 filas: 22 s y ~570 MB). Antes de la carga,
  `parsing/alumnos.py::verificar_filas_xlsx` abre el archivo en `read_only`: con
  `max_row ≤ 3015` no hace nada; por encima, o sin dimensión (`None`), cuenta
  en streaming las filas con datos y rechaza al pasar 1015. **Nunca rechaza
  solo por `max_row`**: una dimensión inflada por formato no son alumnos.
  Pero sí cuenta las filas que EXISTEN en el archivo (celdas con formato y sin
  valor; las que openpyxl rellena por huecos son `EmptyCell` y no cuestan): más
  de 3015 → `FormatoEnFilasVacias`, «copiá solo las filas con datos a un
  archivo nuevo». Sin eso, 1,9 MB con formato en 62.000 filas pasaba la
  pasada previa (10 alumnos) y la carga completa costaba 7,3 s y +405 MB;
  ahora se rechaza en 0,3 s y +1,5 MB. La puntuación final («N/A.», «No.»)
  no cambia un negativo de `tiene_*`.
  `libro_desde_csv` corta con el mismo tope mientras arma el libro. `libro_desde_csv`
  arma un `openpyxl.Workbook` en memoria —el mismo adaptador que
  `parsing/pdf.py`— y reusa `leer_hoja_alumnos`: UTF-8 con BOM y, si no, cp1252;
  separador por `csv.Sniffer`; UTF-16 u otra codificación → «Guardalo como .xlsx
  o como CSV UTF-8».
- **`Importacion.resultado` (tipo ALUMNOS)** lleva `a_crear`, `ya_existen`,
  `filas_invalidas`, `avisos_lectura` (avisos de la lectura ENTERA, p. ej. «Dejé
  de leer en la fila X»: el preview tiene que mostrarlos), `nacen_vencidas` y
  `total_filas_leidas`. Las fechas van como texto ISO (un `date` en un
  `JSONField` da `TypeError`) y las claves de `datos` se filtran contra
  `CAMPOS_IMPORTABLES` antes de `Alumno(**datos)`. Al confirmar o descartar
  queda achicado a `{"version": 1, "resumen": {…}}` y el archivo se vacía en la
  misma transacción y se borra en `transaction.on_commit` con `try/except` +
  log: si el storage falla queda un objeto huérfano, nunca un link roto ni un
  500 (el test del archivo borrado verifica contra el storage, no contra el
  campo: sin eso seguía verde aunque se quitara el `on_commit`).
- **Lo que se ve y lo que se repite.** `alumnos_preview.html` lleva
  `hx-history="false"` (datos de salud en el `localStorage` del mostrador, mismo
  patrón que las pantallas con secretos) y la vista es `never_cache`. Confirmar
  y descartar toleran el doble click: una importación ya procesada redirige con
  un `messages.info` en vez de dar 404 (solo para ALUMNOS; plantillas y
  biblioteca conservan el 404). `_leer` rebobina el archivo con `seek(0)` y, si
  falla, **levanta** en vez de guardar uno truncado.
- **`plantillas/ejemplo.xlsx` tiene su gemelo**: `importaciones:alumnos_ejemplo`
  genera al vuelo la hoja «Alumnos» (dos filas con `MARCA_FILA_EJEMPLO`, una con
  «Inicio del ciclo» vacío y otra con `hoy−3`) y «Cómo llenarlo», con el rango
  sacado de `alumnos/ciclo.py` y los alias de `ALIAS_ALUMNOS`. Nunca como
  binario versionado, por el mismo motivo.

### Planes en PDF (2026-09-07)

`SubirPlantillasForm` acepta `.pdf` además de `.xlsx` (la biblioteca sigue
solo Excel). `importaciones/parsing/pdf.py` (Django-free) lo lee en **dos
intentos, del más exacto al más tolerante**, y la regla de producto que lo
guía es que **la lectura no tiene que ser 100% certera: lo que importa es no
perder ningún ejercicio del plan** — ejercicios, días y semanas primero; los
detalles si se pueden, y lo que falte lo completa el entrenador desde la
plantilla.

- **Intento 1, tablas.** `pdfplumber.extract_tables()` (solo la estrategia
  por líneas dibujadas) en cada página; todas las tablas se apilan en UNA hoja
  de un `openpyxl.Workbook` en memoria y esa hoja pasa por
  `leer_hoja_plantilla(ws, tolerante=True)`. Los lectores solo usan
  `cell().value`/`max_row`/`max_column`/`merged_cells`/`title`, así que un
  workbook armado a mano es un adaptador perfecto y **el lector de Excel no se
  toca**. Tres detalles que no aparecen con un fixture sintético: (1) de cada
  fila de encabezado se conserva la PRIMERA aparición y se saltean las
  repeticiones que Excel imprime en cada página — contar "hasta dos" no
  alcanza, porque si la primera página corta justo después del título esas dos
  filas son la misma; (2) un salto de línea dentro de una celda es un corte
  por ancho de columna (`PUENTE\nSUPINO` → `PUENTE SUPINO`) salvo que lo siga
  una viñeta, que es un renglón real del marcador de día; (3) **el nombre de
  la hoja no pasa por `Worksheet.title`** — se estampa con
  `dataclasses.replace` en `leer_pdf`, para los dos lectores por igual. Es el
  nombre del ARCHIVO, y openpyxl le imponía sus reglas de título de hoja: 31
  caracteres y un `ValueError` con `[ ] : * ? / \`, o sea un 500 con un
  `plan[1].pdf`. **Se probó y descartó la estrategia `text` de pdfplumber**
  para tablas sin bordes: parte palabras por la mitad y produce columnas
  plausibles con basura adentro.
- **Intento 2, texto** (`leer_texto_tolerante`), cuando no hay tablas: una
  máquina de estados por línea — `SEMANA n` (una o varias en la línea) fija
  las semanas activas; `DÍA n` abre un día y las líneas con viñeta que siguen
  son su nombre; una línea que es solo palabras de encabezado se ignora; el
  resto es un ejercicio: código de bloque opcional (`A1.`), nombre hasta el
  primer token numérico, y los números se reparten por semana en orden
  (`4 20 4 25`, `3x12`, `40kg`). Antes del primer marcador, una línea sin
  números es el título del documento, no un ejercicio. Sin `SEMANA` en todo
  el texto, todo entra en la semana 1.
- **Modo tolerante** (`tolerante=True`, solo lo activa el PDF): una fila con
  nombre pero series ilegibles o sin repeticiones produce un ítem «a
  completar» (`ItemParseado.series=None`, `repeticiones=""`) en vez de una
  `FilaInvalida`; en la matriz ancha, un ejercicio sin ninguna celda cargada
  entra en todas las semanas del encabezado. **El `.xlsx` sigue estricto**:
  es un flag con default `False`, así que no cambia nada probado; si algún
  día se quiere tolerancia también para Excel, es cambiar un default.
- **Límites explícitos, con mensaje:** un PDF sin capa de texto (foto o
  escaneo) → «foto o escaneo… exportalo desde Excel o Google Sheets» (no hay
  OCR en el free tier de Render, riesgo aceptado); más de `MAX_PAGINAS_PDF`
  (30) → rechazado antes de leerlo (pdfminer tarda ~1 s/página y hay 30 s de
  gunicorn); un Excel ancho impreso **partido a lo ancho** en varias páginas
  no se reconstruye (cae en el mensaje de «no reconocí ejercicios»); un corte
  a mitad de palabra por una columna angostísima (`UNILATERA L`) queda así en
  el nombre y lo resuelve el matching difuso contra la biblioteca (medido:
  98 de score contra el nombre real, sobre un umbral de 87).
- **El preview cuenta por semana, no filas.** Decía «172 ejercicios · 4 días»,
  que se lee como 172 ejercicios DISTINTOS y para 4 días es absurdo: el propio
  dueño del producto lo leyó como un error del importador. Son 43 por semana
  repetidos en 4 semanas, y el modelo guarda un item por ejercicio Y POR
  SEMANA porque cada una tiene su progresión (35 de los 42 ejercicios del
  archivo real cambian de prescripción entre semanas). El JSON del preview
  guarda `semanas` y `ejercicios_por_semana` (la semana más cargada, **no** un
  promedio: `len(items)/semanas` da decimales y miente si el entrenador no
  programó todo en todas las semanas), y las dos pantallas lo muestran
  separado. Las dos claves se leen con `.get()` y fallback, porque una
  `Importacion` EN_REVISION creada antes del deploy no las tiene.
- **El preview avisa cuántos ítems quedaron a completar** (por hoja y como
  advertencia a nivel archivo) y marca las celdas; al confirmar, los ítems
  se crean con `series=None`, la plantilla muestra «A completar» y no se
  puede asignar hasta terminarlos.
- **Fixtures de tests con `fpdf2`** (ya era dependencia): `_pdf_con_tabla` y
  `_pdf_con_texto` en `importaciones/tests.py`. Helvetica no tiene `•`: las
  viñetas de los fixtures van con `-`, y `multi_cell` necesita
  `new_x="LMARGIN", new_y="NEXT"` o la segunda línea no tiene espacio.

## Acciones en lote del listado de alumnos (2026-10-15)

El listado de alumnos tiene buscador (`q`), filtros `estado` y `sin_fecha`,
selección múltiple y una barra con cuatro acciones: **fijar fecha de inicio,
dar de baja, reactivar y generar códigos de ingreso**. Nació junto con el
importador de alumnos (ver su subsección en «Importador de Excel»): un padrón
importado sin fecha de inicio se arregla acá. La lógica vive en
`alumnos/lote.py` (topes, `separar_por_cuotas`, `fijar_fecha_inicio`,
`cambiar_estado`, `nombres`, `filtros_seguros`), el form es `LoteForm` y la
vista única es `alumnos:lote` (`AlumnoLoteView`, solo POST; un GET redirige al
listado). Pantalla de confirmación: `alumnos/lote_confirmar.html`.

- **Los ids viajan en UN campo oculto separado por comas** (`LoteForm.ids`), no
  como un checkbox con `name` por fila: con cientos de filas se supera
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` y bajo `hx-boost` el 400 es un click mudo. El
  campo se sincroniza al tildar y no solo en el `submit`: bajo `hx-boost` htmx
  puede serializar el form antes de que corra un listener de `submit`, y el lote
  salía con ids vacío. **Siempre se revalidan** con
  `Alumno.objects.for_gimnasio(g).filter(pk__in=ids)`: un id ajeno se descarta y
  se informa («1 no se encontró»), y el tope cuenta solo los ids propios.
- **Ningún error de usuario devuelve 4xx**: redirect con `messages` o render 200
  de la confirmación con el error debajo del campo. `filtros` (para volver al
  listado filtrado) se re-valida con `parse_qsl`: solo claves permitidas,
  primera aparición, re-codificado con `urlencode`, así un `//evil.com` no es
  un open redirect.
- **Fijar fecha** (`MAX_LOTE_FECHA = 1000`) va en dos pasos y la fecha se ve
  siempre antes de ejecutar: el POST de la barra renderiza la confirmación (fecha
  propuesta = hoy, quiénes cambian, quiénes se saltean, y a cuántos activos
  `nace_bloqueada` les pausaría el acceso esa noche); un form recalcula con la
  fecha que se tipee y el de «confirmar» lleva esa fecha ya revisada en un
  hidden, así no se confirma una fecha distinta de la que generó el aviso. Se
  valida contra `rango_inicio_ciclo(timezone.localdate())`: el test del reloj
  congelado a las 23:30 locales **falla con `timezone.now().date()`** (la primera
  versión del test pasaba con el bug, porque el `now` parcheado conservaba el
  tzinfo local; hubo que pasarlo a UTC como lo devuelve `timezone.now()`).
- **Los alumnos que ya tienen cuotas se saltean y se nombran** (hasta 10 + «y N
  más»), incluida una `ANULADA`: mover el ancla con cuotas emitidas solapa
  períodos y vuelve a cobrar días ya cobrados, y el `unique_together` no lo
  impide (solo prohíbe dos cuotas con el MISMO arranque). Se ejecuta con UN
  `UPDATE` con `~Exists(Cuota…)` —que además cierra la carrera con el cron— y
  `modificado` explícito (`update()` no dispara `auto_now`).
  `anclar_ciclo_a_la_primera_rutina` sigue anclando en la fecha de la primera
  rutina a un alumno sin cuotas: comportamiento estándar, dicho en el
  `help_text` y en la confirmación.
- **Baja y reactivación (`MAX_LOTE_ESTADO = 200`) NUNCA con
  `QuerySet.update()`**: `save(update_fields=["estado", "fecha_baja",
  "fecha_inicio_ciclo", "modificado"])` por alumno, como
  `AlumnoToggleEstadoView`, para que corran `registrar_fecha_de_baja` y
  `sincronizar_acceso_con_estado` (que corta `User.is_active`). Un `update()`
  dejaría al dado de baja entrando a la app y sin `fecha_baja`. Va en un
  `atomic` con `select_for_update(of=("self",))`,
  `select_related("perfil__usuario")` (sin él la escala se dispara: lo fija
  `BajaTests.test_escala_a_lo_sumo_3_queries_por_alumno`, verificado por
  mutación) y `.exclude(estado=destino)`, así un doble POST no re-estampa
  `fecha_baja`. El `of=("self",)` hace falta en Postgres porque `perfil` es
  nullable y cae del lado nullable de un outer join; **SQLite lo ignora y no
  está probado contra Postgres** (`ISSUES.md [2026-10-15]`). `cambiar_estado`
  devuelve la **lista** de alumnos que cambiaron y no un entero: el mensaje
  necesita los nombres.
- **La baja pide confirmación y la reactivación no.** La baja avisa que pierden
  el acceso a la app y que su QR y su cara salen del padrón del lector (al
  reactivar hay que volver a enrolar la cara, ver «Control de ingreso»).
  Reactivar es directo y su mensaje cuenta cuántos siguen sin fecha de inicio:
  reactivar un ancla `None` no la inventa (ver «Apps de dominio» → `pagos`).
- **Generar códigos** es directo y sin tope (es un solo `bulk_create`), vía
  `ingreso.services.generar_credenciales_en_bloque(g, alumno_ids=ids)`; sin ids
  se comporta como siempre. Solo para activos.
- **Costo por el worker único**: una baja de 200 alumnos tarda del orden de
  1,2 s (estimado en la spec, no medido en Render). Es el motivo del tope de
  200, y de que fijar fecha —un solo `UPDATE`— pueda llegar a 1000.

## UI y white-label (Fase 4)

- **Tailwind sin reescribir plantillas**: en vez de convertir las ~25
  plantillas existentes a clases utilitarias, se redefinieron los mismos
  nombres de clase que ya usaban (`.tarjeta`, `.boton`, `.badge--ok`,
  `.tabla`, `.contenido--ancho`, etc.) como clases de componente con `@apply`
  en `styles/input.css` (`@layer components` — patrón que la propia
  documentación de Tailwind recomienda). Si agregás una plantilla nueva,
  reusá estas clases en vez de escribir utilidades sueltas repetidas; si te
  hace falta una nueva, defínila ahí, no inline en el HTML.
- **`styles/input.css` (fuente) vive fuera de `static/`** a propósito: si
  quedara dentro de `STATICFILES_DIRS`, Django lo recolecta como si fuera un
  asset servible y WhiteNoise intenta parsear sus `@import`/`@source` como
  URLs de CSS, rompiendo `collectstatic` (pasó en Fase 5, ver `ISSUES.md`).
  Solo `static/css/app.css` (el output compilado) se sirve.
- **Build**: `npm run build:css` compila `styles/input.css` →
  `static/css/app.css` (el que de verdad se sirve). `npm run watch:css`
  durante desarrollo. El output SÍ se versiona en git (`node_modules/` no) —
  Render no corre `npm`, así que el CSS compilado tiene que estar en el repo.
  **Si tocás `input.css`, corré `npm run build:css` antes de commitear.**
- **Colores por gimnasio**: desde el rediseño "Un Paisaje por Gimnasio"
  (2026-08-13, `85ca0a3`) ya no son 2 colores libres — `Gimnasio.paleta` es
  un catálogo cerrado de 4 paisajes curados (Bosque/Océano/Arena/Pizarra,
  `Gimnasio.PALETAS`), cada uno con sus 3 roles (`fondo`/`primario`/
  `secundario`) ya armonizados. Datos de runtime, no algo que Tailwind
  conozca en build-time: se definen como variables CSS
  (`--color-fondo`/`--color-primario`/`--color-secundario`, default en
  `input.css`) y `base.html` las sobreescribe inline por request si el
  gimnasio logueado tiene un paisaje propio. El resto de la UI los
  referencia vía `bg-[var(--color-primario)]` (clases arbitrarias) o, para
  lo ya existente, a través de `.boton`/`.tabla th`/etc. El canvas de fondo
  (`body`, `.landing`) no es un color sólido plano: lleva una atmósfera de
  blobs radiales suaves mezclados con `color-mix()` sobre esos mismos
  tokens (2026-08-14, ver "The Atmospheric Canvas Rule" en `DESIGN.md`).
  Al elegir un logo nuevo en `gimnasio_form.html`, `tenants/
  paisaje_matching.py::sugerir_paisaje()` extrae su color dominante
  (ignorando fondo blanco/negro/transparente) y preselecciona el paisaje
  curado más parecido vía `tenants:logo_sugerir_paisaje` — sugerencia pura,
  no persiste nada, el dueño la confirma o la cambia a mano con "Guardar
  cambios" (ver `ISSUES.md` `[2026-08-14]` sobre por qué la distancia es
  RGB simple, no Lab/CIEDE2000).
- **Tipografía por gimnasio**: `Gimnasio.tipografia` es un `TextChoices` con
  5 opciones curadas (Plus Jakarta Sans —el default—, Sora, Manrope, Outfit,
  Space Grotesk) — texto libre queda afuera a propósito, mismo criterio que
  `grupo_muscular` de `Ejercicio`: un catálogo cerrado evita que el dueño rompa
  la estética con una fuente ilegible. **El default está AUTO-HOSPEDADO y las
  otras cuatro salen de Google Fonts.** Plus Jakarta Sans se declara con
  `@font-face` en `styles/input.css` sobre el `.woff2` variable que
  `npm run prebuild:css` copia a `static/fonts/` desde
  `@fontsource-variable/plus-jakarta-sans` (ver `package.json`), así que la
  mayoría del tráfico —todo gimnasio que no cambió el default— nunca dispara
  una carga externa: es el rol que cumplía la opción `sistema`, que salió del
  catálogo con el rediseño de marca (antes el catálogo eran 6 fuentes de
  Google —Inter, Montserrat, Poppins, Oswald, Playfair Display— más
  `sistema`). El mapeo tipografía → (familia CSS, query de Google Fonts, que
  es `None` para la auto-hospedada) vive en `Gimnasio.TIPOGRAFIA_FUENTES`,
  única fuente de verdad para `base.html` y el preview en vivo de
  `gimnasio_form.html`. La variable `--font-gimnasio`
  sigue el mismo patrón que los colores (default en `input.css`, override
  inline en `base.html`), aplicada vía `font-[family-name:var(--font-gimnasio)]`
  en `body` — el hint `family-name:` es necesario porque sin él Tailwind
  interpreta un valor arbitrario de font sin ese hint como `font-weight`, no
  `font-family` (ambigüedad de la sintaxis de valores arbitrarios). **Gotcha
  de autoescape**: el valor de
  `tipografia_css_family` se inyecta en `base.html` con `|safe` a propósito —
  `<style>` es un elemento "raw text" y el navegador no decodifica entidades
  ahí adentro, así que sin `|safe` el autoescape de Django convierte las
  comillas de `'Plus Jakarta Sans'` en `&#x27;` y rompe el CSS en vez de
  protegerlo (el valor sale de un dict fijo del código, nunca de input de
  usuario, por eso es seguro). Ver `tenants.tests.GimnasioUpdateViewTests.
  test_tipografia_con_comillas_no_queda_html_escapada`.
- **`tenants:gimnasio_editar`** (`GimnasioUpdateView`, sin pk en la URL —
  siempre edita el gimnasio del `Perfil` logueado): logo, colores, tipografía,
  texto de bienvenida, contacto, redes, con preview en vivo (JS vanilla sobre
  el mismo `<form>`, sin depender de htmx porque el form ya tiene
  `hx-boost="false"` por el upload de logo) de cómo el alumno va a ver esos
  cambios antes de guardar. Es lo que le faltaba a Fase 1/2: el modelo tenía
  estos campos desde Fase 1 pero no había ninguna vista para editarlos fuera
  de `/admin/`. **Validación del logo** (`GimnasioForm.clean_logo`,
  `tenants/forms.py`): tamaño máximo, formato (JPEG/PNG) y resolución
  mínima, mismos chequeos que ya tenía `clean_fondo_imagen` (Fase 4) —
  ambos comparten el helper `_validar_imagen()`, con sus propios
  umbrales por campo (el logo tiene un piso de resolución más chico,
  200×200, porque `notificaciones/icons.py` lo estira a un ícono PWA de
  hasta 512×512 y un logo muy chico quedaría pixelado ahí). **La
  resolución se mide como superficie + lado más corto, no como ancho y
  alto por separado** (2026-09-02): el fondo se pinta con
  `background-size: cover`, así que el navegador ya lo recorta centrado a
  cada pantalla y la FORMA de la imagen no importa — la regla vieja
  (`ancho ≥ 1280 Y alto ≥ 720`) rechazaba una foto cuadrada de 1080×1075
  con más píxeles que el mínimo, y una foto vertical de celular solo por
  la orientación. El piso por lado sigue existiendo aparte porque una
  panorámica de 4000×250 supera la superficie pero con `cover` hay que
  estirarle el alto a la pantalla entera. **No recortes del lado del
  servidor**: habría que fijar una proporción, y la pantalla del alumno no
  tiene una (un celular en vertical es casi 9:19) — `cover` sobre el
  original da mejor resultado en más dispositivos.
- **HTMX**: `hx-boost="true"` en `<body>` (`base.html`) — convierte toda
  navegación por `<a>`/`<form>` normal en transiciones AJAX sin reescribir
  ninguna vista (siguen devolviendo la página completa; htmx solo evita el
  reload duro). Excluido explícitamente (`hx-boost="false"`) en los dos
  forms con upload de archivo (`pagos/pago_confirmar.html`,
  `tenants/gimnasio_form.html`, para no arriesgar el envío de multipart) y en
  el link "Conectar Google Calendar" (`mis_turnos.html`, para no tragarse el
  redirect cross-origin a `accounts.google.com` — ver la sección de Google
  Calendar arriba). Regla general: cualquier `<a>`/`<form>` que dependa de un
  redirect externo o de multipart necesita `hx-boost="false"`.
- **Alpine.js**: solo para el toggle del nav en mobile (`x-data` en `<body>`,
  compartido entre el botón ☰ del header y el `<nav>` — deben estar en el
  MISMO scope de `x-data`, si no el toggle no hace nada). El `x-data` existe
  para el staff **y para el alumno** (desde el 2026-10-17 el alumno tiene su
  menú, `partials/nav_alumno.html`, con su propio ☰ y la misma condición:
  sin cuenta suspendida ni con los alumnos bloqueados); el superusuario sin
  Perfil no lo tiene, por eso su nav va sin `:class`. No se usó para nada
  más ("solo si hace falta", ROADMAP Fase 4).

## Sitio público y embudo de solicitudes (`solicitudes/`, 2026-09-25)

Hay **dos** superficies públicas y no hay que confundirlas: la landing de un
GIMNASIO (`g/<slug>/`, ver la sección siguiente) y el sitio del PRODUCTO, que
es esto. Acá el visitante es un dueño de gimnasio que todavía no es cliente, y
lo único que puede hacer es dejar una solicitud: el alta de tenants sigue
cerrada (ver "Portal del alumno y acceso").

**`tenants.views.PortadaView` es la raíz `/`** y se quedó con el nombre de ruta
`home`, que se referencia sin namespace en todo el proyecto — por eso el
reemplazo no rompió nada. Bifurca en tres:

- **Autenticado** → delega en `HomeView` (dashboard de staff, portal del
  alumno, o el redirect del superadmin sin `Perfil` a `plataforma:inicio`).
  Delegación pura: `HomeView` no se tocó ni se renombró, y por eso dejó de
  tener URL propia.
- **Anónimo con la cookie `gimnasio_preferido`** → al login de su gimnasio,
  exactamente lo que ya hacía `LoginView.get` cuando el anónimo caía en
  `/accounts/login/` (misma función compartida, no una copia).
- **Anónimo sin cookie** → la landing de producto (`tenants/portada.html`).

Sin `LoginRequiredMixin` a propósito, misma familia que `GimnasioLandingView`.
Antes de esto, un anónimo en `/` era un redirect al login: el producto no tenía
ninguna página que se pudiera mandar por WhatsApp.

- **La portada no inventa ningún dato que ya viva en el código.** Los precios
  salen de `plataforma/precios.py` vía `_contexto_precios()` (con el importe en
  pesos sólo si hay cotización, ver "Cotización del dólar"), y los colores del
  selector de paisajes del hero salen de `Gimnasio.PALETAS` — **nunca hex
  escritos en el template ni en el CSS**, así que retocar un paisaje o mover un
  escalón de precio se refleja solo. `og:image`/`og:url` van **absolutas**
  (`build_absolute_uri` sobre `static(...)`): los crawlers de redes no bajan
  una URL relativa, y el hash del manifest de WhiteNoise hace que escribir la
  ruta a mano sea imposible. La sección de venta del control de ingreso
  (`id="ingreso"`) **no nombra ninguna marca de equipo** a propósito: no hay
  hardware probado todavía (ver "Control de ingreso").
- **Fotos de contexto (2026-10-11).** La portada lleva cinco fotos generadas
  con IA (hero, «antes», rutinas, ingreso, cierre) en `static/img/portada/`,
  como WebP en varios anchos. La regla completa está en DESIGN.md, «Fotos de
  contexto»: ninguna pantalla legible (lo que se lee de la app es la maqueta
  HTML superpuesta), sin marcas ni texto, y rotuladas «Imágenes ilustrativas»
  porque las personas no son clientes. Tres trampas, cada una con su test en
  `PortadaFotosDeContextoTests`:
  - **Un WebP sin commitear es un 500 en `/` en producción**: con `DEBUG=False`
    el manifest de WhiteNoise levanta `ValueError` por cualquier `{% static %}`
    que falte. `finders.find` lo encuentra igual en disco, así que el test
    pregunta a `git ls-files`.
  - **La foto va FUERA de cualquier `aria-hidden` y de cualquier `[data-demo]`**
    (el `data-demo` pasó de `.funcion__demo` a `.funcion__superpuesta` en las
    funciones con foto).
  - **Solo la foto del hero carga sin `lazy`**, con `fetchpriority="high"`.

  `sw.js` excluye `/static/img/portada/` de su caché, que nunca expira (la
  dejaría para siempre en el teléfono de cada visitante anónimo), y desde la
  `v2` poda las cachés viejas en `activate`. Las fotos se convierten con
  `scripts/fotos_portada.py <carpeta> static/img/portada`, que recorta, aplica
  la corrección de color hacia el `fondo` de Bosque (leído de
  `Gimnasio.PALETAS`) y escribe los anchos que pide el template. Toca sólo las
  escenas que encuentra en la carpeta, y los placeholders salen únicamente con
  `--placeholders`.

### El embudo: doble opt-in, y por qué

`/solicitar/` (`solicitudes:solicitar`) → mail de confirmación →
`solicitudes:verificar` → la solicitud entra a la cola del panel
(`plataforma:solicitud_lista`). Los estados son `NO_VERIFICADA` →
`PENDIENTE` → `APROBADA`/`RECHAZADA`/`LISTA_ESPERA`, más `EXPIRADA` para la que
nunca confirmó.

- **El aviso al dueño del producto sale recién con el email confirmado.** Sin
  doble opt-in, cualquier bot le llena la bandeja —que ES la cola de trabajo—
  de leads con mails inventados, y encima el mail de invitación de una
  aprobación iría a una casilla ajena.
- **Un solo trámite ABIERTO por email**, con un `UniqueConstraint` parcial
  sobre `ESTADOS_ABIERTOS` (índice parcial: anda igual en SQLite y en
  Postgres). Cerrada la solicitud (aprobada, rechazada o expirada) el mismo
  email puede volver a pedir.
- **Todo camino termina en la misma pantalla «enviada»**, y eso incluye el
  choque contra ese índice, el honeypot, el rate-limit y el duplicado: la
  respuesta es NEUTRAL o el formulario se vuelve un oráculo que dice qué mails
  ya pidieron una cuenta. El `IntegrityError` se traga en el service y
  `crear_solicitud` devuelve `None`.
- **`solicitudes/services.py` es el ÚNICO lugar que escribe** sobre estos
  modelos. Todas las transiciones toman `select_for_update()`, son
  idempotentes, y los mails salen por `transaction.on_commit` (mismo patrón que
  `notificaciones`/`calendario`: si la transacción se revierte no sale ningún
  mail). Las URLs absolutas las arma la vista, que es la que tiene el
  `request`, y se pasan como valores o callables.
- **Aprobar NO reimplementa el alta**: llama a `tenants.services.crear_gimnasio`
  y descarta la contraseña generada. Devuelve `(gimnasio, resultado)` con
  `resultado` en `{"aprobada", "ya_existia", "email_en_uso"}` — con
  `email_en_uso` **no crea ni aprueba nada** y le manda un «ya tenés cuenta»,
  porque ese email ya es de alguien que entra hoy.
- **El link para definir la contraseña reusa el token de reset de Django**
  (`solicitudes/tokens.py`: `default_token_generator` + `uidb64` apuntando a
  `password_reset_confirm`, que acá resuelve a `StaffPasswordResetConfirmView`
  con `post_reset_login=True`). Como se valida con `check_token(user, token)`,
  **no pasa por `ResetPasswordStaffForm.get_users()`** y funciona aunque el
  usuario recién creado nunca haya pedido un reset. Caduca a
  `PASSWORD_RESET_TIMEOUT` (3 días): de ahí el botón «reenviar» del panel.

### Anti-spam en capas, sin una dependencia nueva

`solicitudes/antispam.py`: honeypot (`website`), tiempo mínimo **firmado** con
`signing.dumps` (un campo oculto sin firmar lo falsifica cualquiera), dedup por
email y rate-limit por IP, los dos sobre `django.core.cache`. Nada de esto
reemplaza al índice único ni a la neutralidad de la vista: son capas baratas
para frenar bots antes de tocar la base.

- **La IP sale de `CF-Connecting-IP`, nunca del primer `X-Forwarded-For`.** El
  sitio va proxeado por Cloudflare, que pone ese header; `X-Forwarded-For` lo
  puede mandar el cliente falseado y rotarlo para saltear el rate-limit
  (Cloudflare **agrega** la IP real al final, no reemplaza lo que vino). Es la
  misma función que reusa el freno por IP del control de ingreso.
- **La IP se guarda hasheada** (`sha256(valor + SECRET_KEY)`), nunca en claro:
  trazabilidad anti-abuso sin persistir un dato personal.

### Los mails del embudo

`solicitudes/emails.py`, texto plano (mismo criterio YAGNI que el reset de
contraseña), con `EmailMessage` para poder poner `Reply-To` (ver
"Olvidé mi contraseña"). El aviso al dueño va a `SOLICITUDES_AVISO_EMAIL`, con
`DEFAULT_FROM_EMAIL` como fallback.

**`motivo_decision` NO va en el cuerpo de ningún mail, y no hay que volver a
ponerlo.** Es la nota de triage del superadmin: se pide como «Motivo interno» en
`plataforma/solicitud_detalle.html` y se muestra en el historial de auditoría.
Los templates de rechazo y de lista de espera la interpolaban tal cual, así que
un «no es un gimnasio real» escrito para uso interno le llegaba a la persona de
la que se escribió — **un campo no puede tener dos audiencias**. Si algún día
hay que decirle algo puntual a un lead, va un campo NUEVO rotulado como visible
para él. Lo fija `CuerpoDeLosMailsDeDecisionTests`, y el rechazo además tiene
que dejar la puerta abierta y una casilla real: es el único mail que ese
interesado va a leer del producto. La casilla sale de `EMAIL_REPLY_TO` y **no**
de `SOPORTE_CONTACTO` (texto libre de pantalla, puede no ser un email); sin
ella, el mail no invita a escribir a ninguna parte.

**`fail_silently=True` a propósito**, y el motivo importa: estos envíos corren
en `transaction.on_commit`, o sea DESPUÉS de que la fila se commiteó. Si un
fallo transitorio de SMTP levantara ahí, el interesado se comería un 500 con la
solicitud YA creada, y el índice único parcial le impediría reintentar. La
recuperación es el reenvío desde el panel y el cron de expiración, no una
excepción. **Consecuencia aceptada:** un mail que rebota no deja rastro en
ningún log — si una casilla de aviso no entrega, la evidencia es idéntica a la
del estado sano (mismo modo de falla que una bandera opcional apagada).

### Nada de esto es `TenantOwnedModel`

`SolicitudAcceso`/`SolicitudAccesoToken`/`SolicitudEvento` heredan de
`TimeStampedModel` y **quedan fuera de los tres lugares** donde va todo modelo
tenant-owned (`tenants/demo.py::vaciar_gimnasio`, el fixture `_ensuciar` y
`HOJAS`/`EXCLUIDOS` del exportador). Son datos de la plataforma sobre un
interesado: los ve el superadmin, el gimnasio no los ve en ninguna pantalla, y
`SolicitudAcceso` ni siquiera tiene FK a `Gimnasio` hasta que se aprueba —
mismo criterio que `plataforma.PagoPlataforma` (la regla completa está en esa
sección).

- **`SolicitudEvento` es el historial completo** (una fila por transición, en la
  misma transacción que el cambio de estado); los campos `decidida_*` de
  `SolicitudAcceso` son sólo la ÚLTIMA decisión, denormalizados para que el
  listado del panel no haga un subquery por fila.
- **`manage.py expirar_solicitudes` no es housekeeping: es el camino de
  recuperación.** Pasa a `EXPIRADA` las `NO_VERIFICADA` con el token vencido, y
  eso es lo que **libera el email** — mientras la solicitud siga abierta, el
  índice único parcial le impide a ese interesado volver a pedir, así que sin
  este cron alguien que tipeó mal su casilla o nunca vio el mail queda trabado
  para siempre. **No manda ningún mail** (no hay nada útil que decirle a quien
  nunca confirmó). Lo agenda `.github/workflows/expirar-solicitudes.yml`,
  07:00 UTC, después de `generar-pagos`.

## Landing pública (subproyecto 5, más allá del ROADMAP original)

`tenants.views.GimnasioLandingView` (ruta `g/<slug>/`, `tenants/urls.py`) es
la **primera vista del proyecto sin ningún mixin de autenticación** —
accesible logueado o no. Sin subdominios por gimnasio (principio no
negociable #6): la URL se resuelve por `Gimnasio.slug`, que existía desde
Fase 1 sin ningún uso público hasta ahora. `get_queryset` filtra
`activo=True`: un gimnasio desactivado o un slug inexistente dan 404 por
igual (no revela cuál de los dos casos es).

No hay alta de leads propia ni formulario de contacto — decisión explícita
del dueño del producto: los alumnos NO pueden autoregistrarse (el staff
asigna usuario/contraseña a mano, ver `alumnos/views.py::CrearAccesoView`),
así que la landing solo ofrece contactar al gimnasio (`link_whatsapp`/
`link_instagram`/`contacto`, campos que ya existían desde Fase 1) o, si ya
es alumno, ir al login de siempre. **Eso es sobre los ALUMNOS de un gimnasio
y sigue igual**: el embudo de leads que sí existe es el del PRODUCTO
(`/solicitar/`, ver "Sitio público y embudo de solicitudes"), donde el
interesado es un dueño de gimnasio, no un alumno.

**Blanco-etiquetado sin tocar el `:root` global**: `templates/tenants/
landing.html` pisa `--color-primario`/`--color-secundario` y `font-family`
con un `style` inline en su propio `<div class="landing">` — como son
variables CSS heredadas, `.boton`/`.boton-secundario`/`a` reusan
automáticamente el color de ESE gimnasio adentro del `.landing`, sin mutar
las variables globales que usa `base.html` cuando hay un usuario logueado
(esas dependen de `user.perfil.gimnasio`, que un visitante anónimo no
tiene). El `<main>` de `base.html` no envuelve esta página en
`.contenido`/`.contenido--ancho` (`{% block main_class %}` vacío): el hero
necesita ir a todo el ancho de la pantalla, algo que ningún otro template
del proyecto necesitaba hasta ahora.

**Modo "Persuade"** (skill impeccable/dataviz): a diferencia del resto del
sitio (paneles de gestión, modo "Operate"), esta es la primera superficie
pensada para persuadir, no para operar — hero a todo el ancho con un
degradé de los dos colores del gimnasio (estrategia de color "Committed":
el color de marca ocupa una región entera, no un acento suelto) y un único
CTA primario (WhatsApp). El resto del sitio sigue en la paleta neutra
existente; este tratamiento es exclusivo de `landing.html`.

## Login por gimnasio y fix de usuario ya autenticado (subproyecto 6, más allá del ROADMAP original)

Agregado tras un reporte real en producción, con capturas: un staff ya
logueado que visitaba `/accounts/login/` veía su propio topbar, la nav
completa de staff y el fondo de su gimnasio **superpuestos** con el
formulario de login — muy confuso. Causa: `auth_views.LoginView` no redirige
por default a un usuario ya autenticado, y `base.html` renderiza el
topbar/nav en base a `user.is_authenticated` sin ningún caso especial para
la página de login.

`tenants.views.LoginView` agrega `redirect_authenticated_user = True` sobre
el `LoginView` de Django — un usuario ya logueado que visita
`/accounts/login/` (o `g/<slug>/login/`) es redirigido directo a
`LOGIN_REDIRECT_URL` ("home") en vez de ver el form. Vive como clase propia
(no como kwarg inline en `tenants/urls.py`) para que `GimnasioLoginView` la
herede sin duplicar el flag.

**`GimnasioLoginView`** (ruta `g/<slug>/login/`) es la versión "gym-specific":
resuelve el `Gimnasio` por slug con `gimnasio_activo_o_404` (helper
extraído de lo que antes era `GimnasioLandingView.get_queryset`, ahora
compartido por las dos vistas para que no puedan divergir en el criterio de
404 "no revela si el slug existió alguna vez"). No hereda de
`DetailView`/`SingleObjectMixin`: `LoginView` ya es una `FormView`, mezclar
dos jerarquías de vista genérica no aporta nada — alcanza con resolver el
gimnasio en `dispatch` y agregarlo al contexto.

**El slug es puramente estético, no una barrera de autenticación**: el
proyecto no tiene subdominios por gimnasio (principio no negociable #6), así
que un alumno de OTRO gimnasio, o un miembro de staff, pueden loguearse
igual desde `g/<cualquier-slug>/login/` — es el mismo `User`/`Perfil` de
siempre, sin restricción adicional. Solo cambia qué `Gimnasio` se le pasa al
template para pintar colores/logo/tipografía/copy antes de loguearse. Hay
un test de regresión (`test_alumno_de_otro_gimnasio_puede_loguearse_igual`)
que fija este comportamiento a propósito.

`templates/registration/login.html` NO reusa el `<style>` del `<head>` de
`base.html` (que depende de `user.perfil.gimnasio`, inexistente para un
visitante anónimo) — en cambio duplica el patrón ya usado por
`landing.html` (variables CSS inline en el wrapper `.auth-hero`,
`{% block extra_style %}` para fondo imagen/doodle, mismo criterio
`isolation: isolate` para que el doodle no quede tapado). Se evaluó
generalizar el bloque de `base.html` para que acepte un `gimnasio` de
contexto además de `user.perfil.gimnasio`, y se descartó: acoplaría el
head-style de TODA página autenticada a una necesidad exclusiva del login.
`.auth-hero--gimnasio` en `styles/input.css` duplica el mismo canvas
atmosférico de 3 blobs radiales que ya usan `body` y `.landing` — tercera
copia a propósito, mismo criterio ya documentado ahí: sin preprocesador CSS
no hay forma limpia de compartirlo.

Con `gimnasio` en contexto, el copy de marketing genérico se reemplaza por el
nombre del gimnasio y su `texto_bienvenida` — decisión explícita del dueño del
producto: un alumno logueándose a SU gimnasio no debería ver un pitch de venta
dirigido a dueños de gimnasios. Sin `gimnasio` en contexto (login genérico, o
cuando Django redirige acá desde `LOGIN_URL` por una vista protegida — ese
flujo no tiene forma de saber el slug) va la versión de producto, con la
misma voz que la portada.

**El login del gimnasio es la puerta del gimnasio, no solo un formulario**
(2026-09-26, el dueño del producto la encontró «muy vacía»). Muestra lo que el
gimnasio ya cargó para su landing —consultas (WhatsApp, `contacto`, redes) y
horarios, vía `tenants/views.py::horarios_por_dia`, compartida con
`GimnasioLandingView`— y un bloque fijo «Qué vas a encontrar adentro» con lo
que el portal del alumno hace de verdad. Reglas que conviene no deshacer:
- **Cada bloque depende de su dato**, y el ítem «Tus turnos» sale solo si hay
  horarios: sin agenda, prometer turnos es falso. El bloque fijo existe para
  que un gimnasio que no cargó nada igual tenga una pantalla completa.
- **Cada dato una vez**: WhatsApp va como botón con texto, así que las redes
  salen con `sin_whatsapp=True` (parámetro de `partials/redes_sociales.html`).
- **En celular el formulario va antes que los contactos** (grilla con áreas
  nombradas en `.auth-hero--gimnasio`): la mayoría viene a entrar.
- **La marca de la topbar lleva a la landing del gimnasio**, no a `home`: para
  un anónimo `home` es la portada de VENTA. TuGimApp aparece solo en el pie
  («Hecho con TuGimApp»), sin ningún CTA — el test «no vende el producto» lo
  fija.
- **El fondo vive en `.auth-gimnasio`** (envoltorio a todo el ancho), no en
  `.auth-hero`: con `max-w-5xl` la foto quedaba encerrada en una caja de
  1024 px. El formulario es un include compartido por las dos variantes
  (`registration/_tarjeta_ingreso.html`).

`landing.html` enlaza "Iniciar sesión" a `login_gimnasio` con el slug del
gimnasio que se está visitando (antes iba al login genérico, sin contexto).

**Cookie `gimnasio_preferido` (recordar el gimnasio entre logins).**
Primera cookie PROPIA del proyecto (aparte de `sessionid`/`csrftoken` de
Django). Resuelve el caso que el punto anterior no cubre: un alumno que NO
llega por el link de su gimnasio (bookmark de la URL genérica, historial
del navegador) seguía viendo siempre el paisaje Bosque. Tras cualquier
login exitoso — genérico o por slug — `tenants.views.LoginView.form_valid`
guarda el slug de `user.perfil.gimnasio` en esta cookie
(`setear_cookie_gimnasio`). `GimnasioLoginView` hereda `form_valid` sin
overridearlo, así que también la deja.

La próxima vez que ESE dispositivo llegue a `/accounts/login/` sin sesión
activa (típicamente porque `LoginRequiredMixin` lo mandó ahí desde
`LOGIN_URL` con `?next=...`), `LoginView.get` la lee (`gimnasio_de_cookie`)
y si apunta a un gimnasio activo lo redirige directo a `g/<slug>/login/`
preservando `?next=` a mano (`RedirectURLMixin.get_redirect_url()` solo lee
`next` del request actual, no lo hereda de un redirect propio). Si el
gimnasio de la cookie ya no existe o está inactivo, se ignora en silencio y
la cookie se borra sola en esa misma respuesta.

Este chequeo SOLO corre en el login genérico: `getattr(self, "gimnasio",
None)` es `None` únicamente ahí — `GimnasioLoginView.dispatch` ya resolvió
`self.gimnasio` por el slug de la URL antes de que `get()` corra, así que
un slug explícito en la URL nunca es pisado por la cookie.

Si dos usuarios de gimnasios distintos comparten dispositivo, cada login
exitoso pisa la cookie con el gimnasio correcto — se autocorrige solo, sin
mecanismo de "olvidar" explícito. Para el caso en que alguien SÍ quiere ver
el login genérico a propósito (por ejemplo, loguearse con la cuenta de otro
gimnasio en el mismo dispositivo), `login.html` muestra un link "¿No es tu
gimnasio?" hacia `{% url 'login' %}?otro_gimnasio=1`, que `LoginView.get`
reconoce para saltear el chequeo esa vez y borrar la cookie vieja — sin
esto habría un loop de redirect infinito.

Seguridad: `secure=not settings.DEBUG` replica el criterio ya usado en
`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` (`config/settings.py`,
`if not DEBUG:`). `httponly=True` porque nada del lado del cliente necesita
leerla. `samesite="Lax"` explícito porque `set_cookie()` NO hereda
`SESSION_COOKIE_SAMESITE` (ese setting solo aplica al cookie de sesión vía
`SessionMiddleware`). `max_age` de 1 año, mismo criterio de literal
explícito que `SECURE_HSTS_SECONDS`. Es puramente cosmética/pre-login: no
tiene ninguna relación con cómo se resuelve el tenant post-login
(`user.perfil.gimnasio`, sin cambios). Sin banner de consentimiento a
propósito: es una cookie estrictamente funcional, no de tracking/publicidad
— no hay ninguna política escrita en el proyecto que lo restrinja.

**Gotcha de `hx-boost` (encontrado auditando esta feature, corregido acá y
retroactivamente):** el `{% block extra_style %}` que arma el fondo de
imagen/doodle vive en `<head>` (`base.html`), y `hx-boost="true"` es global
en `<body>` — htmx, por default, solo reemplaza `<body>` en una navegación
boosteada, nunca `<head>`. Mismo patrón que ya rompió 4 veces antes en el
proyecto (Google Calendar, PDF de rutina, upload de logo, Tom Select) y que
login/logout ya resuelve desde el commit `8bc7964`. El link "¿No es tu
gimnasio?" lleva `hx-boost="false"` por este motivo, y de paso se corrigió
el mismo gap retroactivo en el link "Iniciar sesión" de `landing.html`
(le faltaba desde que se agregó `GimnasioLoginView`) y en el link de marca
del topbar (`base.html`) — este último **solo** para el visitante anónimo
(destino: login genérico): el caso autenticado (destino: `home`) no tiene
ningún `extra_style` de por medio y conserva la transición boosteada de
siempre, condicionando el atributo con `{% if not user.is_authenticated %}`
en vez de sacarlo sin más (perder el boost ahí habría sido una regresión de
UX para el click más común del sitio). Regla general:
cualquier link/form nuevo cuyo destino dependa de que `extra_style` se
actualice necesita `hx-boost="false"`.

## Login con Google para staff (Frente C)

Agregado a pedido explícito del usuario ("ya tengo otra app con Google
funcionando"), cerrando el ítem que el propio proyecto venía anticipando
desde Fase 3 (`tenants/services.py::crear_gimnasio` ya usa el email del
dueño como `username`) pero nunca había construido. **Coexiste con
usuario+contraseña, no lo reemplaza** — decisión explícita del dueño del
producto: el staff elige cómo entrar cada vez. Coincide con lo que
`ISSUES.md` [2026-07-29] ya dejaba anotado ("no inutilizar las contraseñas
de staff hasta que Google esté verificado contra producción").

- **Solo para staff/dueño, nunca para alumnos** — decisión explícita: los
  alumnos siguen sin autoregistrarse (el staff les asigna usuario/
  contraseña, ver Fase 3 arriba), y Google abriría esa puerta si se lo
  diera a ellos también. El spec (sin código) de "Sub-cuentas de staff"
  (`docs/superpowers/specs/2026-08-12-subcuentas-staff-design.md`) ya
  anotaba que esto aplicaría a cualquier `Perfil(rol=STAFF)`, dueño o
  futuro empleado — sigue así: el matching es "¿hay un
  `Perfil(rol=STAFF)` activo con ese email?", sin distinguir niveles
  (`Perfil.nivel` todavía no existe en el modelo).
- **Nunca crea cuentas nuevas.** Es la restricción más importante del
  diseño: `tenants/google_login.py` solo VERIFICA la identidad de Google
  (`verificar_identidad()`, que intercambia el `code` y valida el
  `id_token` con `google.oauth2.id_token.verify_oauth2_token` — nunca
  confía en un email de query param) y `GoogleLoginCallbackView`
  (`tenants/views.py`) busca un `User` YA EXISTENTE por
  `username=email, is_active=True` con `Perfil.rol == STAFF`. Si no
  matchea, mensaje de error genérico (no distingue "no existe" de "es
  alumno" de "está desactivado" — mismo criterio anti-enumeración que el
  resto del proyecto) y listo — respeta al pie de la letra la política de
  "no self-service" que cerró `/accounts/register/` (ver `ISSUES.md`
  [2026-07-29]).
- **Reusa el mismo Client ID/Secret de Google Cloud que ya usaba Google
  Calendar** (`calendario/services.py`), agregando solo una "Authorized
  redirect URI" adicional en la MISMA consola de Google Cloud — nunca se
  reemplaza la de Calendar, se suma. Variable nueva:
  `GOOGLE_LOGIN_REDIRECT_URI`. Todo-o-nada independiente y paralelo al
  chequeo de Calendar (`GOOGLE_STAFF_LOGIN_ENABLED`, `config/settings.py`)
  — **riesgo aceptado**: como comparte `CLIENT_ID`/`CLIENT_SECRET` con el
  chequeo de Calendar, un entorno nuevo que solo quisiera activar login
  (sin Calendar) tendría que setear las 4 variables de Calendar igual, o
  el chequeo de Calendar revienta en el arranque por verlas "parciales".
  No es un problema hoy: local y producción ya tienen las 4 de Calendar
  completas.
- **Mismo patrón OAuth que Calendar (`Flow` + PKCE), pero mucho más chico**:
  no persiste tokens a largo plazo (no hace falta `GOOGLE_TOKEN_ENCRYPTION_KEY`
  acá), solo intercambia el `code` una vez y lee el email verificado.
  `access_type="online"` (no `offline`: no hace falta `refresh_token` para
  una verificación puntual) y `prompt="select_account"` (para que un staff
  con varias cuentas de Google pueda elegir cuál usar).
  `GoogleLoginRedirectView`/`GoogleLoginCallbackView` (`tenants/views.py`)
  siguen el mismo esqueleto que `ConectarCalendarioView`/
  `CalendarioCallbackView` (`calendario/views.py`): `state` con TTL de 10
  minutos guardado en sesión, mismo manejo de `?error=` (usuario canceló
  el consentimiento en Google).
- **`next` se preserva a través del roundtrip a Google** (login →
  `accounts.google.com` → vuelta) guardándolo en sesión en el paso de
  redirect y leyéndolo en el callback, validado con
  `url_has_allowed_host_and_scheme` (mismo criterio que
  `LoginView._redirigir_a_login_gimnasio`) para que un `?next=` externo no
  pueda usarse para un open redirect.
- **`login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")`
  con el backend explícito** — mismo patrón que ya usa
  `tenants/suplantacion.py`. Esto NO toca ni resuelve la deuda ya anotada
  de `PerfilModelBackend` (ver "Accesos, revocación y suplantación" más
  arriba): sigue pendiente, sin relación con este login.
- **`setear_cookie_gimnasio(response, usuario)` en el login exitoso** —
  mismo efecto secundario que `LoginView.form_valid` para el login por
  contraseña, así el staff que entra por Google también deja la cookie
  `gimnasio_preferido`.
- **Botón "Iniciar sesión con Google"** en `templates/registration/login.html`
  (aparece en las dos variantes, login genérico y `g/<slug>/login/`, mismo
  template), condicionado a `GOOGLE_STAFF_LOGIN_DISPONIBLE` — nuevo
  context processor global (`tenants/context_processors.py`, mismo patrón
  que `VAPID_PUBLIC_KEY`). **Lleva `hx-boost="false"` explícito**: redirige
  cross-origin a `accounts.google.com`, mismo gotcha de siempre (ver
  "Login por gimnasio..." arriba) — es la 9na aparición documentada de este
  patrón en el proyecto.
- **Si un cliente ve el warning de Google "esta app no está verificada"**: no
  es un bug de código, es el consent screen de Google Cloud en modo
  "Testing" (mismo Client ID que Google Calendar, mismo límite de 100
  usuarios de la lista "Test users") — ver `ISSUES.md` `[2026-08-24]` para
  el diagnóstico completo y los dos pasos (alta manual del email como Test
  user + verificación de Google para pasar a "In production").

## "Olvidé mi contraseña" — reset por email, SOLO para staff (más allá del ROADMAP original)

Agregado a pedido explícito del usuario, destrabado por el dominio propio
(`tugimapp.com`, sección "Deploy" más abajo) — antes no había forma real de
mandar email transaccional. Hasta acá, si el dueño de un gimnasio se
olvidaba la contraseña, la única salida era que el desarrollador la
reseteara a mano desde la Shell de Render (`manage.py changepassword`).

- **Hallazgo real de diseño, no solo teórico:** el `PasswordResetForm`
  estándar de Django busca por `User.email`. Pero
  `alumnos/services.py::crear_acceso` también puebla `User.email` cuando el
  staff elige EMAIL (no teléfono) como identificador del alumno — el mismo
  campo que usaría una cuenta de staff. Sin filtro extra, un alumno con
  email como identificador podría auto-resetear su propia contraseña,
  contradiciendo la decisión de producto de que el staff es quien
  asigna/regenera el acceso del alumno. **`tenants/forms.py::ResetPasswordStaffForm`**
  sobreescribe `get_users(email)` (el punto de extensión que la propia
  documentación de Django recomienda para restringir el reset a un
  subconjunto de usuarios) filtrando además por `Perfil.rol == STAFF`. Un
  alumno con email como identificador sigue viendo la misma pantalla
  genérica de "si el email existe, te mandamos instrucciones"
  (anti-enumeración que Django ya trae por default) pero **nunca** recibe
  el mail. `alumnos/identidad.py` **no cambia** — el alumno sigue pudiendo
  usar email o teléfono como identificador, a elección del staff; ninguno
  de los dos casos le da acceso a este flujo.
- **Email: Resend vía el `EMAIL_BACKEND` SMTP que Django ya trae, sin
  librerías nuevas** (ni `django-anymail` ni el paquete `resend`) — mismo
  criterio de "reusar antes que agregar dependencias" que el login con
  Google. `config/settings.py`: `EMAIL_HOST`/`EMAIL_HOST_USER`/
  `EMAIL_HOST_PASSWORD`/`DEFAULT_FROM_EMAIL` todo-o-nada vía
  `_bandera_todo_o_nada()` — helper extraído el 2026-08-20 (hallazgo de
  code-review) que unifica el criterio que antes se copiaba a mano en R2,
  Google Calendar, Google Login y VAPID/Web Push; **si agregás una 6ta
  integración opcional con el mismo criterio, usá este helper en vez de
  copiar el bloque de nuevo** → `PASSWORD_RESET_ENABLED`, que controla si
  el link "¿Olvidaste tu
  contraseña?" se muestra en `login.html` (nuevo context processor
  `password_reset_disponible`). Sin configurar, `EMAIL_BACKEND` cae al de
  consola de Django — nunca rompe nada, el link simplemente queda oculto
  para no ofrecer un flujo que no entrega nada. `EMAIL_PORT`/`EMAIL_USE_TLS`
  son constantes fijas (el submission port TLS de Resend), no variables de
  entorno. **`EMAIL_REPLY_TO` es una variable SUELTA, fuera del grupo** (mismo
  criterio que `SOPORTE_CONTACTO`): el `From` es una casilla no-reply, así que
  sin ella la respuesta de quien recibe el mail no llega a ninguna parte. Hoy
  la consumen SÓLO los mails de `solicitudes/emails.py`, que por eso usan
  `EmailMessage` y no `send_mail`; **el reset de contraseña de Django no pasa
  por ahí y sale sin `Reply-To`**. **En tests, Django ya fuerza
  `EMAIL_BACKEND` a `locmem` automáticamente** (built-in del test runner) — a
  diferencia de R2/Google, no hace falta usar `TESTING` para esto.
- **Las 4 vistas de Django, wireadas a mano** (`tenants/urls.py`), mismo
  criterio que `login`/`logout` (nunca `include('django.contrib.auth.urls')`
  en este proyecto): `password_reset`, `password_reset_done`,
  `password_reset_confirm`, `password_reset_complete`. No hace falta
  ningún chequeo de rol adicional en el paso de confirmación: el token de
  Django ya está firmado para el usuario específico que pasó el filtro de
  `ResetPasswordStaffForm` al pedir el reset.
- **`StaffPasswordResetConfirmView`** (`tenants/views.py`, subclase de
  `auth_views.PasswordResetConfirmView`) con `post_reset_login=True`
  (loguea automático apenas confirma la contraseña nueva, sin volver a
  tipearla) y `post_reset_login_backend` explícito (mismo criterio que
  suplantación y el login con Google — el proyecto no tiene
  `AUTHENTICATION_BACKENDS` custom). Override de `form_valid` para llamar
  `setear_cookie_gimnasio(response, self.request.user)`, mismo efecto
  secundario que los otros dos logins (contraseña, Google).
  `templates/registration/`: 4 HTML (extienden `base.html`, reusan
  `.tarjeta`/`.boton` — sin la lógica de estética-por-gimnasio de
  `login.html`, esta pantalla no depende de ningún slug) +
  `password_reset_subject.txt` + `password_reset_email.html` (texto plano,
  sin HTML email — mismo criterio YAGNI que el resto del proyecto).
- **"Cambiar contraseña" self-service (distinto de "olvidé mi
  contraseña")**: `StaffPasswordChangeView`/`StaffPasswordChangeDoneView`
  (`tenants/views.py`, rutas `password_change`/`password_change_done`) le
  dejan a un staff YA LOGUEADO cambiar su propia contraseña sabiendo la
  actual — no depende de email/`PASSWORD_RESET_ENABLED`, así que funciona
  aunque Resend no esté configurado. El link vive en `templates/tenants/
  gimnasio_form.html` ("Mi gimnasio"), no en el topbar global de
  `base.html` (se sacó de ahí, ver ISSUES.md 2026-08-24, para no sumar un
  ítem más en mobile).
- **Fuera de alcance, decisión reconfirmada:** reset de contraseña para
  alumnos — sigue como hoy, el staff regenera el acceso desde el panel
  (`CambiarPasswordAlumnoView`). Rate limiting/`django-axes` sigue siendo
  deuda futura del Frente C (C5), no se agregó acá.

## Política de privacidad y redes sociales en el portal del alumno

`templates/tenants/privacidad.html` (ruta `privacidad/`, nombre
`politica_privacidad`) es una página estática pública (sin mixin de auth,
igual que la landing) montada directamente en `tenants/urls.py` con
`TemplateView.as_view(template_name=...)` — no amerita una clase en
`views.py` porque no tiene ningún contexto dinámico. Describe la relación
real del sistema (cada gimnasio es responsable de los datos de sus propios
alumnos; la aplicación es la plataforma técnica) y las 3 cookies reales que
usa el proyecto (`sessionid`/`csrftoken` de Django + `gimnasio_preferido`),
no un texto genérico de "cookies de analítica" que no aplica acá. **Es un
documento base, no asesoramiento legal** — si el dueño de un gimnasio
necesita ajustarlo a su jurisdicción, que lo revise con un abogado antes de
tratarlo como definitivo.

Enlazada desde los dos "perfiles" del sistema: el portal del alumno
(`home.html`, al pie) y "Mi gimnasio" (`gimnasio_form.html`, debajo del
form). No se agregó a la nav de staff a propósito — ya tiene 8 ítems tras
el esfuerzo de bajarlo de 10 (ver importador de Excel), mismo criterio que
mantuvo afuera el importador.

**Redes sociales** (`templates/partials/redes_sociales.html`): botones
circulares con el logo SVG de cada red, en el portal del alumno (`home.html`,
al pie, **arriba de la política de privacidad**) y en la tarjeta de contacto
de la landing (`landing.html`). Pasaron por tres formas: links de texto
sueltos → botones con el nombre escrito → íconos (2026-09-02, el dueño
encontró "desabrida" la versión con el nombre). Cuatro decisiones que
conviene no deshacer sin querer:

- **Un solo partial para los dos templates.** Antes cada uno tenía su copia
  y divergieron. La duplicación de estilos entre bloques BEM sigue siendo
  aceptable en este proyecto (ver `.auth-hero--gimnasio`), pero duplicar
  markup con SVG de 40 líneas no.
- **El color es `--color-primario` del gimnasio, NO el oficial de cada
  marca.** El glifo ya identifica la red; el verde/azul/degradé rompería la
  paleta curada (`Gimnasio.PALETAS` existe justamente para que ninguna
  combinación quede ilegible).
- **El nombre vive en `aria-label` + `title`.** Es lo único que un lector de
  pantalla puede anunciar de un `<svg>`; sin eso son tres links vacíos. Los
  tests fijan los tres `aria-label`.
- **El CTA del hero de la landing sigue siendo texto** ("Escribinos por
  WhatsApp"): el hero persuade y tiene que decir qué hacer con palabras. Los
  íconos son el cierre de la página, no la llamada a la acción. Hay un test
  que lo fija.

Los botones miden 44×44 (mínimo táctil cómodo) aunque el glifo sea de 20px.

## PWA instalable + Web Push (app `notificaciones`, más allá del ROADMAP original)

Agregado tras un pedido explícito de uno o más gimnasios pagos ("una app
para el celular"). Se evaluó Expo/React Native y se descartó: hubiera
significado construir una API REST completa sobre Django (hoy no existe
ninguna) y duplicar toda la UI de alumno y staff en dos códigos separados
para siempre. En cambio, `app_gim` se hizo **instalable como PWA** desde el
navegador (sin publicar en app stores) — reusa el 100% de las templates
Django/HTMX existentes, y agrega solo la capa de instalabilidad + push
notifications.

- **`notificaciones`** va **después de todo el dominio** en `INSTALLED_APPS`
  a propósito: sus signals/servicios leen
  `Novedad`/`RutinaAsignada`/`Reserva`/`Cuota`/`Gimnasio`/`Perfil` (mismo
  criterio de orden que el resto del archivo). Fue la última de la lista hasta
  que aparecieron `solicitudes` y `plataforma`, que dependen de más todavía;
  hoy el orden termina en `notificaciones`, `solicitudes`, `plataforma`.
- **`SuscripcionPush`** (una fila por dispositivo/navegador, NO OneToOne con
  el usuario — un alumno puede tener el celu y la PC suscriptos) **es
  `TenantOwnedModel`**, no solo scopeada vía FK a `usuario`: mismo argumento
  que `RegistroSuplantacion` — la consulta natural de un broadcast es "todas
  las suscripciones de MI gimnasio", no "las de este usuario". `gimnasio` se
  stampa en el alta desde `usuario.perfil.gimnasio`, nunca del cliente.
- **`RecordatorioEnviado`** es el mecanismo de dedup para los eventos que
  dispara el cron (ver abajo) — modelo satélite en `notificaciones`, sin
  agregar ningún campo "ya notificado" a `Novedad`/`Cuota`/`Reserva`,
  mismo patrón que `calendario.ReservaCalendarEvent`.
- **Manifest e íconos dinámicos por gimnasio**: `notificaciones/views.py`
  expone `/g/<slug>/manifest.json` y `/g/<slug>/icono-<192|512>.png`
  (`notificaciones/icons.py`, Pillow, reusa `Gimnasio.color_*_css`/
  `PALETAS`). Se generan on-the-fly y se cachean con el cache default de
  Django (clave invalidada por `gimnasio.modificado`) — **no** hay un
  `ImageField` nuevo en `Gimnasio`. El logo en R2 sale con URL firmada que
  expira en 1h (`AWS_QUERYSTRING_EXPIRE`), no apta para `icons[].src` de un
  manifest, por eso el ícono se sirve siempre a través de esta vista propia,
  nunca apuntando directo a `gimnasio.logo.url`.
  **La URL del ícono va versionada** (`icons.icono_pwa_url`, `?v=<modificado
  en ms>`) en el manifest, en el `apple-touch-icon` de `base.html`
  (templatetag `icono_pwa_url`) y en el `icon` de los push: el teléfono
  guarda el ícono al instalar y solo lo vuelve a pedir si la URL del
  manifest cambia — con URL fija, cambiar el logo no cambiaba nada (ver
  `ISSUES.md` `[2026-09-03]`). Por eso el ícono responde `immutable` y el
  manifest `no-cache`. **El lienzo del ícono es el color de fondo del PROPIO
  logo** (`icons.color_lienzo`: borde opaco → ese color; borde transparente
  → `fondo` de la paleta; sin logo → `primario`, el de la baldosa del
  placeholder) y el manifest usa ese mismo valor como `background_color`,
  para que en el splash de Android el ícono no flote en un cuadrado de otro
  color. Los márgenes uniformes se recortan antes de encajar, y hay una
  variante `icono-<size>-maskable.png` (marca en el 80% central) declarada
  con `purpose: maskable` aparte del `any`. iOS no actualiza el ícono de una
  app ya agregada a inicio: eso es de la plataforma, se reinstala.
- **`/sw.js` se sirve en la RAÍZ del dominio** (`ServiceWorkerView`, no vía
  `{% static %}`): WhiteNoise con `CompressedManifestStaticFilesStorage`
  hashea nombres de archivo en producción, lo que rompería la URL estable
  que un service worker necesita, y limitaría su scope a `/static/` salvo
  el header `Service-Worker-Allowed`. `static/js/sw.js` (el contenido real)
  **nunca cachea HTML ni `/media/`**, solo `/static/` (assets versionados,
  cache-first) — el sitio es multi-tenant resuelto por `user.perfil.gimnasio`
  (no por slug en la URL para rutas autenticadas), así que cachear HTML
  podría filtrar el tema/logo de un gimnasio a otro usuario en un
  dispositivo compartido.
- **Envío** (`notificaciones/services.py`): `VAPID_PRIVATE_KEY`/
  `VAPID_PUBLIC_KEY`/`VAPID_ADMIN_EMAIL` todo-o-nada (mismo criterio que las
  `GOOGLE_*`) → `PUSH_ENABLED` en `False` sin las 3 (y siempre `False` bajo
  `TESTING`, mismo criterio que R2). La PWA es instalable de entrada aunque
  `PUSH_ENABLED` sea `False` — solo push queda apagado. `_enviar` construye
  un `Vapid01` explícito con `Vapid01.from_pem(...)` (no pasa el string
  crudo a `pywebpush.webpush(vapid_private_key=...)`, que solo entiende
  ruta-de-archivo o el formato raw/DER de `Vapid.from_string`, NO un PEM con
  headers) — así `VAPID_PRIVATE_KEY` puede guardarse tal cual la imprime
  `vapid --gen`.
- **7 eventos** disparan una notificación: novedad publicada, pago por
  vencer, pago vencido, turno próximo, rutina nueva asignada, nueva reserva
  (avisa al staff) y comprobante subido (avisa al staff). Los 3 que
  enganchan a un `post_save` viable (`notificaciones/signals.py`: Novedad,
  RutinaAsignada, Reserva) siguen el mismo patrón que `calendario/
  signals.py` — `if raw: return`, `transaction.on_commit(...)` para el
  envío real, viven en `notificaciones` (no en `novedades`/`rutinas`/
  `turnos`) para no acoplar esas apps a la infraestructura de push. **Pago
  vencido NO usa signal**: `marcar_vencidos()` usa `QuerySet.update()`, que
  no dispara `post_save` (límite ya documentado en el propio código) — el
  cron lo detecta por `modificado__date=hoy` sin que `pagos/` sepa que
  `notificaciones` existe.
- **Comprobante subido por el alumno es un flujo NUEVO**: hasta acá, quien
  subía el comprobante era siempre el staff, en `ConfirmarPagoView`. Ahora
  `pagos:comprobante_subir` (`AlumnoComprobanteUpdateView`,
  `AlumnoRequiredMixin`) deja que el alumno suba el comprobante de su propio
  `Cuota` PENDIENTE/VENCIDO — **no cambia `estado`**, el staff sigue
  siendo el único que confirma el pago. Esta vista dispara la notificación
  con una llamada directa (no un signal): un `post_save` genérico sobre
  `Cuota` no podría distinguir "subió el alumno" de "confirmó el
  staff" sin flags frágiles.
- **Cron único** (`notificaciones/management/commands/
  enviar_recordatorios.py`, `.github/workflows/enviar-recordatorios.yml`,
  cada 15 min) cubre los eventos que dependen del paso del tiempo: novedades
  con `fecha_publicacion` de hoy (publicación programada a futuro, que el
  signal no ve al crearse), pagos a `dia_vencimiento_pago - hoy.day` entre 0
  y 3 días, pagos recién vencidos, y turnos dentro de los próximos 60
  minutos (usa `turnos/services.py::_ahora_local()`, no `timezone.now()`
  pelado, para no comparar aware-UTC contra naive-local). Es el primer cron
  del proyecto con granularidad menor a diaria — hasta acá todo (`generar-
  pagos.yml`, `backup.yml`) corría 1 vez por día; GitHub Actions no
  garantiza puntualidad exacta al minuto en `schedule`, así que "avisá 1
  hora antes" es aproximado, no exacto.
- **Suplantación**: bloqueada server-side (403 en
  `SuscripcionPushCreateView` si `suplantacion.esta_activa(request)`) y
  client-side (`pwa.js` no intenta pedir permiso si `data-suplantacion` es
  `"true"` en `<body>`) — un staff suplantando a un alumno nunca debe
  suscribir su propio dispositivo a nombre del alumno.
- **Botones "Instalar app"/"Activar notificaciones"** viven una sola vez en
  el topbar de `base.html` (no duplicados en la nav de staff y en el portal
  del alumno por separado): el topbar ya es común a los dos roles, así que
  alcanza con condicionar la visibilidad ahí.

La tienda suma dos tipos de `RecordatorioEnviado`: `PEDIDO_VENCIDO` y `PEDIDO_EN_REVISION` (dedup de los avisos del cron por pedido). El paso `vencer_pedidos` corre en `.github/workflows/enviar-recordatorios.yml`; tras un deploy que trae migraciones falla hasta que Render termina de migrar.

## Tour de bienvenida para staff nuevo (más allá del ROADMAP original)

Notas dismissibles ("x" o "Siguiente") que guían al staff nuevo en 6 pasos
(logo → colores/tipografía → fondo → importar ejercicios → crear rutina),
mostradas de a una según en qué pantalla está — pedido explícito del usuario
para que un dueño nuevo entienda la app en ~5 minutos sin tocar `/admin/`.

- **Elegibilidad, la única pieza server-side**:
  `tenants.context_processors.tour_onboarding_disponible` habilita el tour
  solo para `Perfil.rol == STAFF` cuyo `Perfil.creado` (auto_now_add, sin
  campo ni migración nueva) sea posterior a `settings.TOUR_ONBOARDING_DESDE`
  — sin esto, un dueño que ya usa la app hace meses lo vería igual, porque
  `localStorage` no tiene forma de distinguir "nuevo" de "viejo" por sí solo.
  La comparación usa `timezone.localtime(perfil.creado).date()`, no
  `.date()` a secas: `creado` se guarda en UTC, y sin convertir a hora local
  (`TIME_ZONE = America/Argentina/Buenos_Aires`) un Perfil creado entre las
  21:00 y 23:59 caería en el día siguiente. El context processor corta
  temprano si `request.resolver_match.app_name == "admin"` — evita una
  query de `perfil` de más contra Neon (scale-to-zero) en cada carga de
  `/admin/`, donde el tour nunca se renderiza igual.
- **Todo el progreso vive en `localStorage`** (`static/js/tour_onboarding.js`),
  namespaced por gimnasio (`tour_onboarding_paso_<slug>`) para el caso raro
  de que el mismo navegador se use para más de un gimnasio. Un solo índice de
  paso avanza con "Siguiente" o la "x" (misma acción); "No mostrar más" lo
  termina del todo. Sin botón de reinicio (YAGNI).
- **Cada paso está asociado a una pantalla** (`home`, `gimnasio_editar`,
  `ejercicios`, `rutinas`) declarada por esa pantalla en un bloque
  `{% block tour_pagina %}{% endblock %}`. La tarjeta solo se muestra cuando
  el paso actual coincide con la pantalla — el staff avanza navegando con el
  nav normal, sin que nada bloquee la navegación.
- **Gotcha real de `hx-boost` encontrado implementando esto (10ma+ aparición
  del patrón, ver "Login por gimnasio..." más arriba para las anteriores),
  pero de una variante nueva**: `hx-boost` reemplaza el *contenido* de
  `<body>` en cada navegación boosteada, pero NUNCA los atributos del propio
  tag `<body>` — un `data-tour-pagina` puesto ahí quedaba pegado en el valor
  de la primera carga completa (siempre "home"), sin actualizarse al navegar.
  Un review posterior encontró que el mismo problema aplicaba a
  `data-tour-habilitado`/`data-tour-gimnasio`, y no solo por navegación
  normal: `suplantar`/`suplantacion_volver` (formularios boosteados, sin
  `hx-boost="false"`) cambian `request.user` en el mismo swap, así que el
  tour del staff podía quedar visible mientras suplanta a un alumno (o
  escondido para el staff recién vuelto). Los tres viven ahora en
  `#tour-datos-marcador`, un único `<span hidden>` como primer hijo de
  `<body>` — regla general: cualquier dato que dependa de la respuesta
  actual (qué template hijo se renderiza, o directamente quién es
  `request.user`) va en un elemento DENTRO de `<body>`, nunca en un atributo
  de `<body>` mismo.
- La tarjeta (`.tour-tarjeta` en `styles/input.css`) es un modificador de
  posicionamiento sobre `.tarjeta` (reusa fondo/borde/sombra/padding, no los
  duplica) para una tarjeta flotante fija generada por JS (`crearTarjetaTour`
  le pone las dos clases), no un coachmark apuntando a elementos puntuales —
  evita recalcular posición contra un nav que colapsa distinto en mobile/PC.
- **Gotcha de testing manual, no de código**: el service worker de la PWA
  (`static/js/sw.js`) cachea `/static/` con estrategia cache-first — durante
  el desarrollo de esta feature, un `tour_onboarding.js` editado no se veía
  reflejado en el navegador hasta desregistrar el Service Worker manualmente
  (`navigator.serviceWorker.getRegistrations()` + `unregister()`), aunque el
  servidor ya sirviera el archivo nuevo. Si un cambio a un `.js`/`.css` no
  parece aplicarse en local con la PWA instalada o ya visitada, sospechá del
  Service Worker antes que del código.

## Tienda del gimnasio (`tienda/`, 2026-10-17)

Diseño completo y decisiones tomadas: `docs/superpowers/specs/2026-10-17-tienda-design.md` (incluye «Ajustes del 17-10» y «Decisiones de ejecución»). Esto es lo que hay que saber para tocar el código.

**Qué es.** Un add-on: el dueño del producto lo habilita por gimnasio (`Gimnasio.tienda_habilitada`, sólo desde el panel de plataforma; `puede_usar_tienda` es la puerta y la cuenta demo siempre la pasa) y se cobra aparte (USD 20 por mes, o 10 meses al año; **Hito 3 hecho el 2026-10-18**: se suma a la suscripción sólo si `gimnasio.tienda_habilitada`, nunca por `puede_usar_tienda`, que es verdadera en la demo; ver «Precios, ciclo y prueba», «Panel de plataforma» y «Cobro online de la suscripción». **Se cobra desde el próximo pago**: prenderla a un gimnasio que ya pagó no toca lo pagado, y apagarla a mitad de período no devuelve nada). El gimnasio vende productos con variantes (talles, sabores) a sus alumnos, que reservan, pagan y retiran en el gimnasio. **Hito 1: transferencia. Hito 2 (hecho el 2026-10-18, APAGADO hasta la prueba guiada): Mercado Pago a la cuenta del propio gimnasio**, ver «Mercado Pago de la tienda» más abajo; el principio #3 de `CLAUDE.md` lo abre sólo para la tienda, las cuotas (`pagos`) siguen sin MP.

**Modelos** (`tienda/models.py`; `TenantOwnedModel` salvo `Variante`, `FotoProducto` e `ItemPedido`, que cuelgan de su padre): `ConfiguracionTienda` (una por gimnasio vía `obtener_configuracion`: `publicada`, `horas_reserva_transferencia`, `max_pedidos_activos`, `instrucciones_retiro`), `CategoriaProducto`, `Producto` (`publicado`; su columna `foto` quedó muerta, ver `ISSUES.md` 2026-10-17), `FotoProducto` (hasta 5 por producto, `posicion` 0 = principal; hija de `Producto` como `Variante`, no `TenantOwnedModel`), `Variante` (`stock`, `activa`, `codigo`, nombre calculado de `valor_1/2`; ver «Variantes por características»), `Pedido` e `ItemPedido` (snapshot de nombre y precio). Los tres lugares de la regla transversal (`vaciar_gimnasio`, `_ensuciar`, `HOJAS`/`EXCLUIDOS`) están cubiertos.

**`Variante.stock` es el DISPONIBLE, no el físico.** Reservar descuenta con `update(stock=F("stock") - n)`; liberar suma. Lo reservado se calcula con `services.reservado_por_variante` (una query agregada sobre `ESTADOS_QUE_RETIENEN`) y la pantalla del staff muestra «disponible / reservado». Por eso el stock no se edita en absoluto: hay un «ajuste ±» con `F()` que no pisa reservas (`StockTests.test_ajuste_concurrente_con_una_reserva_no_pisa`). La siembra de la demo respeta esto. Ojo: `sembrar_demo --borrar` seguido de otra siembra deriva el stock (`borrar_demo` borra los `Pedido` sin devolver lo reservado y los productos sobreviven); el camino normal, `restaurar_demo`, vacía todo y no se ve afectado.

**Máquina de estados** (`Pedido.Estado`): `PENDIENTE_PAGO` → `EN_REVISION` (comprobante a tiempo) → `PAGADO` → `ENTREGADO`; `PENDIENTE_PAGO` → `VENCIDO`; las cancelaciones terminan en `CANCELADO` o `DEVUELTO`. Retienen stock `PENDIENTE_PAGO`, `EN_REVISION` y `PAGADO`. Toda transición es `filter(pk=…, estado__in=ORIGEN).update(...)` y el stock se libera sólo si tocó una fila (`TransicionesDelStaffTests`). El staff puede «Marcar pagado» directo desde `PENDIENTE_PAGO` (pago en mostrador).

**Rechazo del comprobante = reintento.** Rechazar no cancela: vuelve `EN_REVISION → PENDIENTE_PAGO` con `reservado_hasta` nuevo (`horas_reserva_transferencia`), el motivo visible para el alumno y **el stock sigue reservado** todo el tiempo. Si no resube, vence como cualquier pendiente. El archivo viejo se borra **sólo si el nuevo entró** (el nombre viejo se lee de la base, con la fila bloqueada, justo antes del `UPDATE`). El recordatorio de 24 h por comprobante en revisión sale una vez por pedido (dedup por `RecordatorioEnviado`); queda aceptado que un reintento no lo vuelva a disparar. Tests: `ReintentoDelComprobanteTests`, `TransicionesDelStaffTests.test_rechazar_*`.

**Arrepentimiento.** Pagado sin retirar: se puede pedir siempre. Retirado: 10 días desde `entregado_en` en fecha argentina (`localdate`, nunca UTC). El «Botón de arrepentimiento» (Res. 424/2020) es un link visible en el catálogo y en «Mis pedidos», que lista los pedidos cancelables; filtra en la base con `q_puede_pedir_cancelacion`, fijada contra `puede_pedir_cancelacion` por un test. El staff acepta (→ `DEVUELTO`) o rechaza (`cancelacion_resuelta_en`). Riesgo legal abierto: `ISSUES.md` 2026-10-17. Tests: `CancelacionTests`, `BotonDeArrepentimientoTests`.

**Estadísticas de ventas** (`tienda/analitica.py`, molde de `tenants/analitica.py::ingresos_por_mes`): cuentan `PAGADO` + `ENTREGADO` por mes de `pagado_en`; `DEVUELTO` reescribe el mes (esa plata se devolvió). Agrupan con `TruncMonth` en hora argentina, una fila por mes aunque esté vacío y una query por indicador. La tarjeta «Ventas de la tienda» del dashboard corre sólo con `puede_usar_tienda`: **sin la tienda no corren las queries de la tarjeta del dashboard** (`VentasEnPantallasTests.test_sin_tienda_no_calcula_la_tarjeta_de_ventas`, que prueba que `panel_de_ventas` no se llama). El «Vendido este mes» de `PedidoListView` corre **sin condición** (`resumen_del_mes`): esa pantalla se alcanza con pedidos vivos aunque el add-on esté apagado. Costos aceptados: el contador de alertas (`contadores_panel`, 1 query agregada) corre en el inicio del staff de **todos** los gimnasios, porque tiene que detectar pedidos vivos con el add-on apagado; y el portal del alumno hace un `exists()` (`tienda_visible_para_alumnos`) en cada GET cuando la tienda no está visible. Tests: `tests_analitica.py`.

**Camino perezoso vs cron.** Los crons de GitHub llegan tarde (2-4 h en el plan free), así que un plazo de 30 min no puede depender de ellos: `services.vencer_pedidos` corre perezosamente al abrir catálogo, carrito, checkout, mis pedidos del alumno, el listado y el detalle del staff y antes de aceptar un comprobante. **Ese camino nunca toca la red ni manda push** (con un worker, seis llamadas de 5 s son un 502; `VencimientoPerezosoSinPushTests`). El comando `vencer_pedidos` (paso del workflow `enviar-recordatorios`) es el respaldo y el que avisa por push.

**Las 14 trampas de la spec, una línea cada una:**

1. Variante de otro gimnasio en la sesión: el `UPDATE` de la reserva NO filtra por `producto__gimnasio` (sin JOIN a propósito, ver trampa 3); el alcance viene de `_variantes_vendibles(gimnasio)`, que acota el carrito antes de reservar (`CarritoTests.test_no_se_agrega_la_variante_de_otro_gimnasio`, `CrearPedidoTests.test_variante_de_otro_gimnasio_no_toca_su_stock`).
2. Cantidad ≤ 0 suma stock: se valida en el carrito y en `crear_pedido`, más `CheckConstraint` (`ItemPedidoTests.test_la_cantidad_cero_la_rechaza_la_base`).
3. Sobreventa: nunca leer-y-restar, `UPDATE … stock__gte=n` por ítem en `atomic()` (`CrearPedidoTests.test_la_segunda_compra_de_la_ultima_unidad_falla_sin_tocar_nada`).
4. Doble liberación: transición condicional, se libera sólo si tocó una fila (`VencimientoTests.test_vencer_dos_veces_no_devuelve_dos_veces`, `test_si_otro_proceso_movio_un_pedido_no_libera_su_stock`).
5. El staff pisa reservas al editar el stock: ajuste ± con `F()` (`ProductoFormTests.test_editar_el_nombre_no_pisa_el_stock_reservado`).
6. Los crons llegan tarde: vencimiento perezoso (`PedidosStaffTests.test_listado_vence_perezosamente`).
7. `vencer_pedidos` concurrente: `skip_locked` y costo independiente de la cantidad de pedidos (`VencimientoTests.test_el_costo_no_crece_con_la_cantidad_de_pedidos`).
8. Comprobante subido justo al vencer: gracia de 2 min y borrado del archivo si la transición no entró (`ComprobanteTests.test_fuera_de_plazo_se_rechaza_y_no_deja_el_archivo`, `ComprobanteVistaTests.test_subir_tarde_dice_que_el_plazo_vencio_y_libera_el_stock`).
9. Acaparar stock: tope de pedidos en curso por alumno y sin compra suplantando (`CrearPedidoTests.test_tope_de_pedidos_en_curso`, `test_no_se_compra_suplantando`).
10. Borrar un producto vendido daba 500: con ventas se archiva (`BorrarProductoTests.test_con_ventas_no_se_borra_y_lo_explica`).
11. `notificar_a_gimnasio` sin `rol` avisa también a los alumnos: siempre `rol=STAFF` (`AvisosAlStaffTests.test_siempre_con_rol_staff`).
12. Fotos de producto (galería de hasta 5): vista propia por foto (`/tienda/productos/<producto>/fotos/<foto>/`), URL versionada `?v=` e `immutable`, nunca la `.url` firmada de R2; `tienda_foto_producto` va a `URLS_SIN_BLOQUEO` (`FotoTests.test_sirve_la_foto_cacheable_sin_cookie`, `test_logueado_tampoco_lleva_cookie_ni_registra_actividad`, `test_foto_de_otro_gimnasio_404`). El tope de 5, la principal y el borrado de filas (el archivo queda en R2) los fija `GaleriaDeFotosTests`. En el catálogo, con más de una foto se muestra la fila deslizable `.producto-fotos` con «N fotos»; con una o ninguna, la imagen suelta de siempre (`CatalogoGaleriaTests.test_tres_fotos_van_en_orden_dentro_de_una_region_deslizable`, `test_una_foto_se_ve_como_antes_sin_region_ni_contador`, `test_sin_fotos_no_hay_imagen_ni_region`; patrón en `DESIGN.md`).
13. Apagar el add-on con pedidos vivos: el alumno conserva «Mis pedidos» y el staff sigue entregando (`TiendaApagadaTests`, `TiendaApagadaAlumnoTests`).
14. Demo compartida: sin Mercado Pago y con siembra sólo en `es_demo` (`SembrarDemoTests.test_la_tienda_no_se_siembra_en_un_gimnasio_que_no_es_demo`).

**Detalles de pantalla.** «Quedan N» en el catálogo y badge «Stock bajo» en la lista del staff cuando el disponible llega a `services.UMBRAL_POCAS_UNIDADES` (3; con 0 se ve «Agotado», no «Stock bajo»). En la demo, la barra y el título de la tienda llevan el chip «Adicional».

**Archivos a R2:** `FotoProducto.imagen` (`tienda/productos/`) y `Pedido.comprobante` (`tienda/comprobantes/`); ver § Deploy. Todo form que sube archivos lleva `hx-boost="false"`.

**Código de barras y búsqueda (2026-10-18).** `Variante.codigo` (32, `db_default=""`): el de fábrica o, si queda vacío, el **interno** que inventa `Variante.save()` después del INSERT: `tienda/codigos.py::codigo_interno(pk)` = EAN-13 `20` + `pk` a 10 dígitos + verificador (rango GS1 20–29, «uso interno del comercio»: ningún producto de fábrica lo trae, y al salir del `pk` es único sin carreras). Se normaliza a mayúsculas y sin espacios en `Variante.save()` (así también desde el admin); vaciarlo al editar lo regenera. **La unicidad por gimnasio vive en Python** (`BaseVarianteFormSet._validar_codigos`, una query `codigo__in` para todas las filas): `Variante` no tiene `gimnasio`, así que no hay constraint en la base (riesgo aceptado en `ISSUES.md`); se excluyen las variantes del propio formset (intercambiar dos códigos no es un falso duplicado), no cuentan las filas que se borran y sí las con ventas posteadas «Quitar». Un código de 13 dígitos que empieza con `20` sólo vale si es el interno de esa misma variante. El admin normaliza pero no chequea unicidad (lo usa sólo el superadmin). La migración `0007` llenó el interno de las existentes; las que haya creado la versión vieja durante el deploy quedan en «» (la lista muestra «—») hasta que se guarde su producto (`VarianteForm.has_changed` guarda la fila aunque no se toque si no tiene código). El catálogo del alumno no muestra el código.

**Buscar y escanear** (`ProductoListView`): el buscador (`?q=`, ≤ 60) recibe también el lector USB (teclado + Enter). Un código **exacto** (`codigo=normalizar(q)`: mayúsculas y sin espacios, como se guardan; con `filter`, nunca `get`) deja sólo sus productos, marca la fila (`data-destacada`) y pone `autofocus` en su «Sumar o restar» (y no en el buscador: un solo `[autofocus]` por página). Si no, `icontains` sobre nombre de producto y de variante, y sobre el código sólo desde 4 caracteres (con menos, «20» traería todos los internos), con `.distinct()`. Un `delta` que es exactamente el código de una variante del gimnasio (el lector escaneó con el foco en el ajuste) se rechaza sin tocar el stock. El largo del código se valida después de normalizar (el form le saca el `max_length`): un código pegado con espacios no se rechaza. **Escaneo en serie**: el ajuste hecho desde la búsqueda postea `desde_busqueda=1` y vuelve a `?ajustada=<pk>` (fila marcada, buscador vacío con el foco), así el próximo escaneo cae en el buscador y nunca en el input del ajuste, donde se sumaría el código como cantidad. `ajustada` inválido se ignora (`int()` con `try`, nunca `isdigit()`: `"²".isdigit()` es verdadero). En el formulario de producto, `static/js/tienda_variantes.js` convierte el Enter del lector en los inputs `[data-codigo-barras]` en «campo siguiente». Tests: `tienda/tests_codigos.py`.

**Variantes por características (2026-10-18).** Un producto tiene hasta dos características, texto libre (`Producto.caracteristica_1/2`, 30: «Talle», «Color»; nada de `TextChoices` global) y cada variante un valor por característica (`Variante.valor_1/2`, 60). **`Variante.nombre` se calcula** (`tienda/variantes.py::componer_nombre`: «M · Negro», «M», o «» sin características) y se queda como columna porque todo lo demás lo lee (catálogo, carrito, `str()` del snapshot del pedido, exportación): ningún consumidor cambió. `Variante.save()` lo calcula si hay algún valor (sin valores no lo toca: se puede seguir creando con `nombre=`); el form del staff y el del admin lo asignan siempre (también «»). `Variante.clean()` sólo valida el largo ≤ 60 del nombre calculado, como error de `valor_1` (campo de los dos forms). Sin características, el producto tiene **una sola** variante «» (el alumno ve el botón directo).
- **Sin constraint de nombre único** (`tienda/0008`, ver `ISSUES.md`): la unicidad la validan `BaseVarianteFormSet._validar_nombres` (staff) y `VarianteAdminFormSet` (inline del admin). Duplicados entre activas, y de una activa contra una desactivada **sin** ventas («Ya existe «M» desactivada: tildá Activa…»); dos desactivadas nunca chocan, ni una activa con una desactivada con ventas (así sacar una característica a un producto con ventas no deja un callejón sin salida).
- **Reglas del formset** (sobre filas «vivas» = no borradas de verdad, con `pk` o con algún dato, más la fila única): sin `caracteristica_1`, como mucho una activa; con ella, cada activa con su valor (y el de la 2); al menos una viva siempre. Los valores de una característica vacía se limpian en `VarianteForm.clean()` (no en el formset: cuando corre, `_post_clean` ya copió los valores a la instancia). **Trampas de Django que fijan los tests** (`tienda/tests_variantes.py`): `full_clean` del formset llama `has_changed()` y `_should_delete_form()` de cada fila ANTES del `clean` del formset, así que `_es_viva` es función pura de la fila; la fila única (alta sin características) se arma con `empty_permitted=False` vía `get_form_kwargs` y su `has_changed()` es `True` (si no, una fila sin tocar no se guardaba y quedaba un producto sin variantes); `has_changed()` también es `True` si el nombre calculado cambia sin que cambie lo posteado (característica borrada); una fila nueva sin datos se descarta en `_should_delete_form`, **nunca una con errores** (se perdían en silencio); si una característica tiene error en el form del producto, `add_error` la saca de `cleaned_data` y el formset recibe `caracteristicas_validas=False`: no limpia valores ni corre reglas (sólo código y largo), así no aparecen errores falsos en todas las filas. `con_ventas` (una query) lo arma la vista y lo usa el formset en vez de `items.exists()` por fila. Tope 50 (`validate_max`, cuenta también las inactivas con ventas).
- **Vista**: `_ProductoFormMixin.post` propio valida form y formset **juntos** (los errores de los dos en el primer intento) y llama `form_valid(form)` con la firma de siempre (primero pasa por `TenantScopedMixin.form_valid`; el formset viaja en `self._formset_valido`).
- **Form y JS**: las características se dibujan en «Variantes» (fuera de `campos_generales`) con un `<datalist>` de `SUGERENCIAS`; cada fila dibuja **siempre** `valor_1` y `valor_2` con su error (`templates/tienda/_fila_variante.html`) y `tienda_variantes.js` sólo los esconde cuando la característica está vacía (nunca con error) y actualiza el texto de sus etiquetas. El generador («Agregar combinaciones») arma el producto cartesiano de las casillas + «Otros valores», saltea las combinaciones ya presentes (activas o no), reusa primero las filas sin valor y la extra, y clona el `<template data-fila-vacia>` (`formset.empty_form`, `__prefix__`) para el resto; si pasa de 50 avisa y no agrega nada.
- **Migración `0008`**: una sola variante «» o «Único» → `nombre=""` sin característica; cualquier otro producto con nombres → `caracteristica_1 = "Opción"` y `valor_1 = nombre` (el dueño la renombra). Mixto («» + «Chocolate»): la «» activa queda sin valor y el form pide completarla o desactivarla. Las variantes creadas por la versión vieja durante el deploy quedan sin valores: el form precarga `valor_1` con su nombre, y si el producto entero es de esa ventana (sin característica, con variantes de nombre libre sin valores) `ProductoForm` propone «Opción» con la misma regla (el formset del GET toma las características del `initial` del form, no de la instancia). Un POST sin la clave `caracteristica_1` (pestaña abierta antes del deploy) no guarda nada y pide recargar.
- La demo siembra la proteína con «Sabor», la remera con «Talle» y la creatina sin características; busca las variantes por `valor_1, valor_2=""` con `filter().first()` (sin la constraint, un visitante que agregó un Color podría dejar dos «M»).

### Mercado Pago de la tienda (Hito 2, 2026-10-18)

Guía de la prueba con cuentas de prueba y checklist de lo no confirmado: `docs/mercadopago-tienda.md`. **Está construido y APAGADO**: se prende cuando están las 4 variables en Render (y los mismos 4 secrets en GitHub). Diseño base en la spec («Mercado Pago del gimnasio»); lo de acá es lo que quedó en el código.

**Bandera.** `TIENDA_MP_ENABLED` = `_bandera_todo_o_nada` de `MERCADOPAGO_CLIENT_ID`, `MERCADOPAGO_CLIENT_SECRET`, `MERCADOPAGO_OAUTH_REDIRECT_URI` y `MP_TOKEN_ENCRYPTION_KEY`, `and not TESTING` (los tests la prenden con `override_settings`). Apagada, nada se ve: el form de configuración no tiene los campos `acepta_*`/`minutos_reserva_mp` (se sacan de `self.fields`), no hay tarjeta, el carrito no ofrece elección y el webhook responde 503. En la demo (`es_demo`) tampoco se conecta nunca.

**Conectar (OAuth + PKCE, `tienda/views_mercadopago.py`, `tienda/mercadopago_oauth.py`).** `ConectarView` arma `code_verifier` y un `state` firmado (`signing.dumps` con gimnasio, usuario y nonce; nonce, verifier y hora van en la sesión) y redirige a `auth.mercadopago.com.ar`. `CallbackView` (`/tienda/mercadopago/callback/`, es el valor de `MERCADOPAGO_OAUTH_REDIRECT_URI`) exige `state` válido, mismo usuario y gimnasio, nonce de un solo uso y ≤ 10 min; canjea el código; **rechaza una respuesta sin `refresh_token`** (depende de `offline_access` en la app de MP) o sin `user_id`; si ya había una cuenta con OTRO `mp_user_id` y hay pedidos MP vivos (`ESTADOS_QUE_RETIENEN` o REVISAR) o de los últimos 7 días que no sean una compra abandonada (vencido/cancelado sin `payment_id`), no cambia de cuenta; antes de mirar vence los links ya vencidos (`vencer_pedidos`), igual que «Desconectar». Si todo sale bien, `update_or_create` de `CuentaMercadoPago` (CONECTADA) y **push a todo el staff** (cualquier staff puede conectar; así nadie desvía ventas en silencio). `es_de_prueba` = `live_mode` falso o token `TEST-` (a confirmar en la prueba). Todos los links y forms que salen a MP llevan `hx-boost="false"`.

**Los tokens** (`CuentaMercadoPago`, uno por gimnasio): `EncryptedTextField(clave_setting="MP_TOKEN_ENCRYPTION_KEY")`, clave Fernet APARTE de la de Google. Sin la clave (o con una mal formada) el campo lee `None` en vez de explotar, así sacar las 4 variables no rompe el admin ni el borrado de un gimnasio; leer `None` pasa la cuenta a RECONECTAR sin llamar a MP. **Un token ilegible nunca se guarda con `save()` completo** (`update()`/`update_fields` sin las columnas de tokens; el admin excluye los tokens y su `save_model` no los toca). En memoria el token viaja envuelto en `Secreto` (`suscripcion/mercadopago.py`, `repr` = «Secreto(***)») para que no aparezca en los `f_locals` de un frame de Sentry (`SENTRY_OPCIONES` además lleva `include_local_variables=False`).

**Refresco y desconexión (`tienda/cuenta_mp.py`).** `refrescar_cuentas_mp` toma las CONECTADAS con `token_expira` a menos de 30 días (`VENTANA_REFRESCO`), con `select_for_update` y relectura tras el lock (el refresh token es de un solo uso); 400/401 o token ilegible → RECONECTAR + push al staff. El paso a RECONECTAR por un 401 al usar el token (`marcar_reconectar`) es un UPDATE condicional por `token_expira` para no pisar una cuenta recién refrescada. **Desconectar** es lógico (DESCONECTADA, apaga `acepta_mercadopago`, prende `acepta_transferencia`: nunca queda sin medio) y se rechaza con pedidos MP en `PENDIENTE_PAGO`. **Los tokens de una desconectada se conservan 7 días** (`PLAZO_TOKENS_DESCONECTADA`) para que `reconciliar_pedidos` y el webhook sigan verificando pagos tardíos; el comando los borra pasado ese plazo, **antes** del chequeo de la bandera (no necesita la clave).

**Compra (alumno).** `medios_disponibles`: MP si bandera + `acepta_mercadopago` + cuenta CONECTADA + no demo; transferencia si `acepta_transferencia` **o si MP no está disponible** (una cuenta en RECONECTAR nunca deja la tienda sin vender; la configuración avisa «tus alumnos ven sólo transferencia»). `crear_pedido(medio=…)` revalida el medio (el POST puede traer cualquier cosa). Un pedido MP reserva `minutos_reserva_mp` (30 por defecto, 20–120) y la preferencia vence `MARGEN_PREFERENCIA` (10 min) ANTES que la reserva (`vence_preferencia`): la reserva local siempre dura más que el link. Si MP falla al crear la preferencia, el pedido **se borra** (nunca tuvo link pagable; el stock vuelve sólo si el UPDATE condicional tocó una fila). Un pedido MP no manda el push «Nuevo pedido» (llegaría por cada intento abandonado: lo reemplaza `pago_mp` al acreditar). Con la preferencia vigente el alumno no puede cancelar (`pago_en_curso`) y el staff no ve «Marcar pagado» ni «Cancelar»; el tope `max_pedidos_activos` cuenta esos pedidos sólo mientras el link está vigente. Retorno: `tienda/mercadopago/retorno/<uuid>/` (alumno dueño, `never_cache`); acredita con el `payment_id` de la URL pero **nunca confía en el `status` de la URL**.

**Acreditar (`tienda/pagos_mp.py::acreditar_pedido`, molde `suscripcion/services.py::acreditar`).** Lo llaman el webhook, el retorno y `reconciliar_pedidos`. Lee el pago de la API con el token de ESE gimnasio (fuera de la transacción) y sólo actúa si el `external_reference` es un pedido MP **del mismo gimnasio**. Toda escritura es un UPDATE condicional (`_transicionar`, nunca `pedido.save()`); si no toca filas se relee y se decide una vez más, y si tampoco, `AcreditacionReintentable` (webhook 500, MP reintenta). Es idempotente por `(payment_id, estado_mp)` y **para el mismo pago `estado_mp` nunca retrocede**. Reglas:
- `approved` + ARS + monto ±0,01: `PENDIENTE_PAGO` → `PAGADO` (`pagado_en`, así cuenta en «Vendido este mes»); `VENCIDO`/`CANCELADO` → re-reserva todo-o-nada en su propio savepoint (primero el pedido, después las variantes) → `PAGADO`, o `REVISAR` si falta stock. Monto o moneda distintos desde esos tres estados → `REVISAR`.
- Un aprobado tardío sobre un pedido que **canceló el staff** (`Pedido.cancelado_por_staff`, lo pone sólo `cancelar_por_staff`) va a `REVISAR`: no se revive.
- Ya `PAGADO`/`ENTREGADO` «marcado a mano» (sin `payment_id`, o con un rechazado anotado): se adjunta el pago sin alarma. Con otro `payment_id` aprobado: **cambio deliberado respecto de la spec**, el pedido NO pasa a `REVISAR` (perdería el estado y el stock); queda como está, el pago va a `respuesta_mp["pagos_adicionales"]`, se prende `revision_mp_pendiente` y el staff recibe «pago_duplicado» (pestaña «Revisar MP»).
- `rejected`/`cancelled`: sólo anotan `payment_id`/`estado_mp` en `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO` (un aprobado posterior lo reemplaza); con el pedido pagado no tocan nada. `in_process`, `pending`, `authorized`, `in_mediation`: nada.
- `refunded`/`charged_back` del `payment_id` del pedido: `PAGADO` → `DEVUELTO` (devuelve stock); `ENTREGADO`/`REVISAR` → `DEVUELTO` sin stock; ya `DEVUELTO`, no-op. De un pago ADICIONAL (el duplicado que el staff devolvió): sólo se marca resuelta su entrada y, cuando no queda ninguna, se apaga `revision_mp_pendiente`; el pedido bueno sigue como está.
- Permisivo: no se niega por `publicada`, `tienda_habilitada` ni cuenta congelada. Avisos por `on_commit` y siempre `rol=STAFF` para el staff (`pago_mp`, `revisar_mp`, `pago_duplicado`, `devuelto_mp`).
- «Entregar igual» (desde `REVISAR`) pide el stock con el UPDATE condicional y pasa a `ENTREGADO` (no a `PAGADO`; pone `pagado_en` si falta); «Ya lo devolví» → `DEVUELTO`; «Ya devolví el duplicado» resuelve una entrada adicional.

**Webhook** (`tienda/v1/webhook/mercadopago/<uuid_webhook>/`, `csrf_exempt`, POST, nunca lee `request.user`): bandera apagada → 503; cuenta inexistente → 200 «ignorado»; DESCONECTADA con tokens (≤ 7 días) → procesa igual; sin tokens (RECONECTAR por token ilegible, o desconectada hace más de 7 días) → 200 sin llamar a MP; tipo ≠ `payment` o sin `data.id` → 200; 404 y 403 de MP → 200; 401 → RECONECTAR condicional + 200; otro error o carrera → 500 (MP reintenta). **La autenticidad sale de la API, no de la firma**: la firma de la `notification_url` de un vendedor OAuth es una pregunta abierta de la prueba; si llega firmada se agrega validación OBLIGATORIA en un cambio aparte (nunca «si viene»). El webhook de `suscripcion` ya no distingue un token de plataforma muerto: un 401/403 al consultar el pago responde 200 con `logger.error` (antes 500 y reintentos infinitos); lo cubren `reconciliar_cobros` y ese log.

**Reconciliación y crons** (sin workflows nuevos: pasos en `.github/workflows/reconciliar-cobros.yml`, cada 6 h, antes del ping a Healthchecks, `if: ${{ !cancelled() }}`, con las 4 variables y las `VAPID_*`). `refrescar_cuentas_mp` y `reconciliar_pedidos` (pedidos MP de los últimos 7 días en `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO` con link, filtrados **por estado y no por `payment_id`** —un rechazado deja su id y el reintento aprobado se perdería—, más nuevos primero, `limite=50`; un 401 saltea esa cuenta en el resto de la corrida). Con la bandera apagada salen 0 «apagado» **salvo que exista una cuenta CONECTADA** (→ 1: secrets olvidados, los tokens vencerían sin que nadie lo note). Lista de secrets: `docs/runbook-respaldos.md`.

**Casos aceptados** (no son bugs):
- **La devolución es siempre manual**, desde la cuenta de Mercado Pago del gimnasio. Cancelar un pedido MP pagado o aceptar un arrepentimiento sólo cambia el estado y avisa «Devolvé la plata desde tu cuenta de Mercado Pago»; la devolución automática queda fuera de esta versión.
- **Pasados los 7 días de una desconexión**, los reintegros y contracargos de pedidos de la cuenta vieja ya no se reflejan solos (los tokens se borraron): el staff usa «Cancelar». **En un cambio de cuenta los tokens viejos se pisan al instante** (`update_or_create` sobre la misma fila, no se conservan 7 días): desde ese momento nada de la cuenta anterior se refleja solo. Por eso el cambio se frena con pedidos MP sin cerrar o con pago de los últimos 7 días (una compra abandonada —vencida o cancelada sin `payment_id`— no frena).
- **Vuelta en iOS:** en la app instalada la vuelta de Mercado Pago puede caer en el login. No importa para el cobro: acredita igual el webhook (o `reconciliar_pedidos`).
- **Dos devueltos de pagos DISTINTOS sobre un mismo pedido no pagado:** sólo queda congelado el primero (el segundo no pisa su marca). Caso límite aceptado.
- Un pago MP aprobado sobre un pedido «marcado a mano» con monto o moneda distintos se adjunta sin alarma (la regla de monto distinto rige sólo desde `PENDIENTE_PAGO`/`VENCIDO`/`CANCELADO`); queda un `logger.warning` sin datos personales.
- El `select_for_update` del pedido sólo protege en Postgres; en SQLite (la suite) la protección es el UPDATE condicional más el reintento. Las carreras reales no se probaron contra Postgres.

## Deploy (Fase 5)

**Estado (2026-09-08): desplegado en Virginia, web y base en la misma
región.** App en `https://www.tugimapp.com` (y `https://tugimapp.com`), servida
por el web service **`app-gim-virginia`** (región Virginia, plan **Starter**),
con la base en Neon **`aws-us-east-1`** y media en el bucket R2
`app-gim-media`. Repo en `https://github.com/fabri07/app-gim` (privado).

**Por qué se migró de región, y la regla que deja:** hasta el 2026-09-08 el web
estaba en Oregón y la base en `sa-east-1` (São Paulo), y **cada query pagaba
182 ms de ida y vuelta**. El panel de inicio hace 28 queries: tardaba ~5,6 s.
La lección, que aplica a cualquier decisión futura de infraestructura: **el
navegador nunca habla con la base.** La latencia navegador↔web se paga una vez
por página y Cloudflare ya la resolvía (TLS terminado en Argentina, 19 ms); la
latencia web↔base se paga **una vez por query**. Tener la base cerca del usuario
y lejos del web era el peor arreglo posible — y era justamente la intuición de
la que se partió. Hoy el RTT es de **2 ms** (p95 2,51). Detalle completo en
`ISSUES.md [2026-09-08]`.

**El servicio NO está manejado por el Blueprint**: se creó a mano porque la
región de un servicio no es editable después de crearlo, y `generateValue: true`
sobre `DJANGO_SECRET_KEY` habría generado una clave nueva, deslogueando a todos
e invalidando los tokens de reset. `render.yaml` sigue describiendo el servicio
viejo (`app-gim`, Oregón), que queda **prendido y sin dominio** como vuelta
atrás; su valor hoy es ser la lista completa de las variables que la app
necesita (28 al 2026-09-29), y
`config/tests.py::BlueprintDeclaraLoQueSettingsLeeTests` falla si `settings.py`
lee una que ahí no está.

**`www` va PROXEADO por Cloudflare (nube naranja), no en "Solo DNS".** No es
cosmético: en Solo DNS la IP de origen queda expuesta, se pierden WAF y
mitigación de DDoS, y **ninguna regla de cache de la zona se aplica** porque el
tráfico nunca pasa por ella. Eso tuvo confundido el diagnóstico un buen rato,
porque `www` devolvía `cf-ray` igual — Render usa Cloudflare como su propio CDN
(`…cdn.cloudflare.net` en la cadena de CNAME). **Cómo distinguirlo:** `dig
+short www.tugimapp.com` tiene que devolver IPs de Cloudflare (`104.21.x` /
`172.67.x`); si devuelve `216.24.57.x` está pegando directo a Render. Proxeado
cuesta ~12 ms de piso y a cambio los estáticos se sirven desde Argentina
(37 ms con `HIT` contra 274 ms sin él).

**Dominio propio: `tugimapp.com`** — comprado en Cloudflare el 2026-07-30,
apuntado a Render el 2026-08-19. `app-gim.onrender.com` ya **no responde**
("Not Found") — el dominio propio es la única forma de llegar a la app
desde ahora. Certificados SSL (root y `www`) emitidos y verificados.
`DJANGO_ALLOWED_HOSTS`/`DJANGO_CSRF_TRUSTED_ORIGINS` en Render ya incluyen
el dominio nuevo (confirmado funcionando: login, con y sin Google, carga
sin error de host). Los redirect URIs de Google (Calendar Y el login nuevo
de staff, ver sección propia más abajo) se actualizaron en Render
(`GOOGLE_OAUTH_REDIRECT_URI`/`GOOGLE_LOGIN_REDIRECT_URI`) y ya estaban
dados de alta en la consola de Google Cloud apuntando a `tugimapp.com` antes
del corte, así que no hubo ventana de corte para ninguna de las dos
integraciones. Los registros SPF/DKIM que pide Resend **ya están cargados**
(ver "Lo que sigue pendiente" más abajo): el dominio es lo que destraba el
email transaccional, porque sin verificación de dominio Resend solo deja
enviar a la casilla propia.

- **Plan elegido: arrancar en el free tier de Render, upgradear cuando entre
  el primer gimnasio pago** (decisión del usuario, coincide con "primero se
  cobra, después se sofistica"). El web service se duerme sin tráfico —
  aceptado a propósito, ver `ISSUES.md`.
- **`render.yaml`** define el Blueprint: **solo el web service (free)**. Ya no
  declara `databases:` — la base salió del Blueprint el 2026-07-29 y vive en
  Neon (ver abajo). Tampoco tiene cron: **Render no ofrece plan free para cron
  jobs**, así que TODOS los trabajos programados corren en GitHub Actions
  (hoy siete workflows).
- **La base es Neon, no Render** (migrada el 2026-07-29, ver `ISSUES.md`). El
  Postgres free de Render expiraba a los 30 días + 14 de gracia y después
  Render borra los datos; el de Neon es free permanente. `DATABASE_URL` se
  carga a mano en el dashboard de Render (`sync: false`) con la URL **POOLED**.
  La URL **DIRECTA** (sin `-pooler` en el host) se usa solo desde el workflow
  de backup: el pooler no sostiene bien la sesión larga de un `pg_dump`.
  `config/db.py` activa `conn_health_checks=True` — es obligatorio contra Neon,
  que suspende el compute por inactividad (scale-to-zero); sin el chequeo
  Django reusa conexiones muertas del pool de `CONN_MAX_AGE` y los requests
  fallan de forma intermitente. **También activa
  `disable_server_side_cursors=True`** (2026-09-07): la URL pooled es
  PgBouncer en modo transacción, y Django documenta que con ese pooler los
  cursores de servidor tienen que estar apagados. No es teórico: cualquier
  `<select>` de un `ModelChoiceField` itera con `QuerySet.iterator()`, que en
  Postgres abre un cursor `DECLARE`, y un worker de gunicorn murió por timeout
  esperando uno de esos mientras renderizaba el preview del importador (ver
  `ISSUES.md` `[2026-09-07]`).
- **Trabajos programados (GitHub Actions, no Render):**
  - `.github/workflows/generar-pagos.yml` — corre `manage.py generar_pagos`
    todos los días 06:30 UTC. Usa la URL **pooled** (conexiones cortas).
  - `.github/workflows/expirar-solicitudes.yml` — `manage.py
    expirar_solicitudes`: cierra los leads que nunca confirmaron el email (ver
    "Sitio público y embudo de solicitudes").
  - `.github/workflows/backup.yml` — `pg_dump` diario a las 06:00 UTC, cifrado
    con GPG y subido al bucket R2 `app-gim-backups`. Usa la URL **directa**.
    El día 1 de cada mes copia también a `monthly/` y **encadena** la
    verificación pasándole el nombre exacto del objeto — no la agenda con un
    cron propio, para que no pueda terminar validando el backup del día
    anterior.
  - `.github/workflows/backup-verify.yml` — baja el objeto, valida checksum,
    descifra y lo **restaura de verdad** en un Postgres descartable. Un backup
    que nunca se restauró no está verificado.
  - Retención: `daily/` 30 días por lifecycle, `monthly/` 12 meses por bucket
    lock. El lock va **solo** en `monthly/`: tiene precedencia sobre la
    lifecycle, así que en `daily/` impediría que la expiración ocurriera nunca.
  - Monitoreo en **Healthchecks.io**: alerta por **ausencia** de ping, que es
    lo único que cubre a la vez el dump fallido, el workflow desactivado por
    inactividad del repo y GitHub Actions caído.
  - **El respaldo usa `pg_dump`, nunca `dumpdata`/`loaddata`** — ver la entrada
    de `ISSUES.md`: `calendario/signals.py` no chequea `raw`, así que un
    `loaddata` sincronizaría cada `Reserva` restaurada contra la API real de
    Google Calendar.
  - Los otros tres viven documentados en su propia sección:
    `enviar-recordatorios.yml` (cada 15 min, ver "PWA instalable + Web Push")
    y `restaurar-demo.yml`/`sembrar-demo.yml` (cada 6 h, ver "Cuenta de
    demostración compartida").
  - `.github/workflows/reconciliar-cobros.yml` (cada 6 h) y
    `.github/workflows/avisar-cobros.yml` (diario, 12:00 UTC = 09:00 local),
    del cobro online (ver "Cobro online de la suscripción"). Los dos necesitan
    los secrets `MERCADOPAGO_ACCESS_TOKEN`/`MERCADOPAGO_WEBHOOK_SECRET` (sin
    ellos salen en rojo a propósito), las `EMAIL_*`/`DEFAULT_FROM_EMAIL`/
    `EMAIL_REPLY_TO` (sin ellas el mail cae a consola en silencio) y
    `NEON_DATABASE_URL_POOLED`; el de reconciliar además `COBROS_AVISO_EMAIL`.
    Pinguean `HEALTHCHECKS_URL_RECONCILIAR_COBROS` y
    `HEALTHCHECKS_URL_AVISAR_COBROS`.
- **Cobro online (Mercado Pago):** tres variables nuevas en Render (`sync:
  false` en el Blueprint): `MERCADOPAGO_ACCESS_TOKEN` y
  `MERCADOPAGO_WEBHOOK_SECRET` son un grupo todo-o-nada
  (`_bandera_todo_o_nada` → `COBRO_ONLINE_ENABLED`; sin ellas el cobro online
  queda oculto y sólo queda el registro manual), y `COBROS_AVISO_EMAIL` es
  suelta. En el `.env` local van las credenciales **de prueba** (`TEST-…`).
- **Cloudflare R2 — creado y en uso.** Bucket `app-gim-media`, endpoint
  `https://<account_id>.r2.cloudflarestorage.com`. Las 4 credenciales
  (`R2_BUCKET_NAME`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/
  `R2_ENDPOINT_URL`) están en el `.env` local y en el dashboard de Render
  (van marcadas `sync: false` en el Blueprint, así que no se leen del repo —
  verificarlas ahí, no acá).
- **Qué se guarda en R2 y qué no** (pregunta recurrente): R2 guarda SOLO los
  archivos subidos por usuarios, que son exactamente seis campos —
  `Gimnasio.logo` (`logos/`), `Gimnasio.fondo_imagen` (`fondos/`),
  `Cuota.comprobante` (`comprobantes/`), `Importacion.archivo`
  (`importaciones/`, el `.xlsx` o el `.pdf` original), `FotoProducto.imagen`
  (`tienda/productos/`) y `Pedido.comprobante` (`tienda/comprobantes/`). **Todo el
  resto de los datos vive en Postgres**: alumnos, rutinas, ejercicios, pagos,
  novedades, turnos/reservas, tokens de Google Calendar, usuarios, y el
  `resultado` JSON de cada importación. Los estáticos (`static/css/app.css`,
  etc.) tampoco van a R2 — los sirve WhiteNoise desde el propio contenedor.
- **Gotcha: con `runserver` en local también se escribe al bucket de
  producción.** Como el `.env` de desarrollo tiene las 4 `R2_*`,
  `STORAGES["default"]` es `S3Storage` también en tu máquina (no existe ni se
  usa `media/`): un logo o un `.xlsx` subido corriendo `runserver` aterriza en
  el MISMO bucket que usa producción. La DB sí está separada (SQLite local vs
  Neon), así que quedan archivos huérfanos sin fila que los referencie —
  molesto pero inofensivo. Si algún día molesta, la salida es un bucket aparte
  para dev (cambiar `R2_BUCKET_NAME` en el `.env` local), no borrar las
  credenciales.
- **Los TESTS sí están aislados de R2** (desde el 2026-07-30; antes no lo
  estaban y habían dejado 816 archivos basura en el bucket, ver `ISSUES.md`).
  `config/settings.py` define `TESTING = "test" in sys.argv` y lo usa en dos
  lados: `PASSWORD_HASHERS` (MD5, para que la suite sea rápida) y
  `STORAGES["default"]` (`InMemoryStorage`). La rama de R2 está guardada con
  `if R2_ENABLED and not TESTING`. **Si agregás un servicio externo nuevo,
  usá `TESTING` para desactivarlo en la suite** — el criterio es que
  `manage.py test` no salga a la red por ningún motivo.
- **Google Calendar (opcional) — credenciales creadas.** Las 4 env vars
  `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`/
  `GOOGLE_OAUTH_REDIRECT_URI`/`GOOGLE_TOKEN_ENCRYPTION_KEY` están en el
  `.env` local (redirect a `http://localhost:8000/calendario/callback/`); en
  Render el redirect apunta al dominio propio
  (`https://www.tugimapp.com/calendario/callback/`, actualizado el
  2026-08-19 junto con el dominio) y está dado de alta en la consola de
  Google Cloud. Las 4 o ninguna — `settings.py` revienta al arrancar si
  están parciales; sin ellas la app funciona igual, el alumno simplemente
  no ve la opción de conectar su calendario (`GOOGLE_CALENDAR_ENABLED =
  False`). Login con Google para staff (Frente C, sección propia más abajo)
  reusa el mismo Client ID/Secret con una variable de redirect propia
  (`GOOGLE_LOGIN_REDIRECT_URI`), también apuntando a `tugimapp.com`.
- **Estado del respaldo (2026-07-30): operativo y verificado de punta a punta.**
  Secrets cargados, bucket `app-gim-backups` con lifecycle en `daily/` y bucket
  lock en `monthly/`, los dos checks de Healthchecks andando. Verificados con
  evidencia real: backup → restore encadenado (`tablas_esenciales=10`), el
  bucket lock rechazando un borrado en `monthly/` mientras `daily/` lo acepta,
  y la alerta por ausencia llegando por mail al minuto que correspondía.
  **La base vieja de Render ya se borró**: Neon es la única base. El runbook
  completo está en `docs/runbook-respaldos.md`.
- **Lo que sigue pendiente**: (a) ~~rotar la contraseña de Neon~~ **cerrado
  por la migración del 2026-09-08**: producción usa un proyecto Neon nuevo con
  una credencial que nunca se expuso. Queda como tarea defensiva rotar la del
  proyecto VIEJO, para que cualquier consumidor que haya quedado apuntando ahí
  falle a los gritos en vez de trabajar en silencio sobre datos muertos;
  (b) ~~apuntar `tugimapp.com`~~ **hecho el 2026-08-19**; (c) smoke test manual
  end-to-end de turnos → Google Calendar contra producción; (d) ~~confirmar los
  registros SPF/DKIM~~ **están cargados** (MX de `send`, SPF, DKIM de
  `resend._domainkey` y DMARC), y desde el 2026-09-08 las 4 `EMAIL_*` están en
  Render, así que «olvidé mi contraseña» aparece en el login. **Falta probar un
  reset real**: el link visible solo prueba que el grupo está completo, no que
  el envío funcione.
- **Settings de producción** (`config/settings.py`): `DATABASE_URL` (Postgres
  si está seteada, SQLite si no — mismo criterio que el resto del archivo),
  `STORAGES["default"]` cambia a `storages.backends.s3.S3Storage` solo si
  `R2_BUCKET_NAME` está seteada (si no, sigue en `FileSystemStorage` como en
  dev), `STORAGES["staticfiles"]` usa el manifest comprimido de WhiteNoise
  SOLO fuera de `DEBUG` (con `DEBUG=True` no hay `collectstatic` corrido, así
  que exigir el manifest rompe `{% static %}` en dev/tests — pasó durante
  esta fase, quedó cubierto por un test). `CSRF_TRUSTED_ORIGINS` nuevo,
  mismo patrón CSV-por-entorno que `ALLOWED_HOSTS`.
- **`WhiteNoiseMiddleware`** va justo después de `SecurityMiddleware` (sirve
  los estáticos sin depender de Nginx/CDN aparte).

