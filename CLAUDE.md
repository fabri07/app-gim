# CLAUDE.md

Guía para Claude Code (y para cualquier humano) trabajando en este repo.

## Qué es esto

SaaS simple, multi-tenant y white-label para gimnasios y entrenadores locales
(Argentina). Reemplaza Excel + papel + WhatsApp: alumnos, planes, rutinas con
video, pagos mensuales, comprobantes, novedades, personalización de
logo/nombre/colores por gimnasio.

El plan completo (fases, modelo de datos, criterios de salida, timeline
comercial) vive en **`ROADMAP.md`** — léelo antes de tocar código nuevo. Este
archivo es la foto rápida de "cómo está armado hoy", no reemplaza al roadmap.

**Fase actual: código de Fases 0-6 completo en `main` y desplegado.** La app
vive en `https://www.tugimapp.com` (Render, free tier, dominio propio desde
el 2026-08-19) y el bucket de
Cloudflare R2 (`app-gim-media`) ya está creado y en uso — los pasos manuales
de Fase 5 que dependían de cuentas de terceros están hechos. Ver "Deploy
(Fase 5)" más abajo para el estado exacto y lo que sigue pendiente. Fases 0-4
(esqueleto, modelos, vistas de staff, portal del alumno, UX/white-label)
completas — un dueño puede usar el sistema de punta a punta desde el panel
web sin tocar `/admin/`. Además del scope original del ROADMAP ya están
mergeadas: agenda de turnos/reservas con cupos, read-receipts de novedades,
medios de cobro configurables, una integración opcional con Google
Calendar por alumno (ver "Turnos, reservas y Google Calendar" más abajo), y
un importador de planes/ejercicios desde Excel (ver "Importador de Excel
(Proyecto 2)" más abajo) — el ROADMAP.md no las documenta todavía como
fases propias, viven en `ISSUES.md` y en los mensajes de commit ("Fase 6,
Task N", "Parte A/B/C", "Proyecto 2, Task N").

**Nota:** el acceso del alumno NO es magic-link como decía la primera versión
del ROADMAP — el dueño del producto pidió que el staff asigne usuario y
contraseña directamente. Ver `ISSUES.md` (2026-07-01) y `ROADMAP.md` Fase 3,
ya actualizados.

## Principios no negociables (resumen — el detalle está en ROADMAP.md)

1. Una sola app, múltiples gimnasios. Nunca se copia el repo por gimnasio.
2. La app es la fuente de verdad; nada de sync en vivo con Excel.
3. Pagos simples: sin Mercado Pago ni integraciones financieras en el MVP.
   Los pendientes del mes se autogeneran por cron; el dueño confirma.
4. Aislamiento por gimnasio desde el modelo de datos: ningún registro
   operativo existe sin `gimnasio`.
5. Archivos de usuario (comprobantes, logos) van a Cloudflare R2, nunca al
   filesystem de Render (es efímero).
6. Sin subdominios por gimnasio en el MVP; el tenant se resuelve por el
   usuario logueado.
7. Primero se cobra, después se sofistica — no construir features que no
   ayuden a conseguir/retener los primeros tres gimnasios pagos.

## Stack

- Django 5.2 (templates + vistas basadas en clases, sin DRF).
- SQLite en dev; Postgres en producción vía `DATABASE_URL` (Render la inyecta).
- Tailwind CSS (build local con Node, ver "UI y white-label" abajo) + HTMX
  (`hx-boost`) + Alpine.js (CDN, solo el toggle de nav mobile). **Nada de
  React/Next** en el MVP.
- Deploy: Render (`render.yaml`, ver "Deploy (Fase 5)") + Cloudflare R2 para
  media (comprobantes, logos) vía `django-storages`.

## Arquitectura multi-tenant

Estrategia: **base de datos compartida + aislamiento por fila** vía FK
`gimnasio` en cada modelo operativo (no schema-per-tenant ni db-per-tenant —
KISS/YAGNI para esta etapa). El patrón viene de `~/gestor-pedidos` (ver
`REUSO.md`), no de Vektor (que es FastAPI, sin este patrón).

- `core/models.py` — `TimeStampedModel`, `TenantQuerySet.for_gimnasio()`,
  `TenantOwnedModel` (abstracto; todo modelo operativo hereda de acá y
  obtiene el FK `gimnasio` con `on_delete=PROTECT`).
- `core/mixins.py` — `TenantScopedMixin`: en toda vista basada en clases,
  resuelve `request.user.perfil.gimnasio`, filtra el queryset, y **stampea
  `gimnasio` del lado del servidor** al guardar (nunca viene del cliente).
- `core/forms.py` — `TenantScopedModelForm`: acota automáticamente cualquier
  FK a otro `TenantOwnedModel` dentro del mismo gimnasio (cierra el hueco de
  FK-injection — sin esto, un form con FK a otra entidad tenant-owned
  permitiría enviar el id de un registro de otro gimnasio).
- `tenants/models.py` — `Gimnasio` (el tenant) y `Perfil` (vínculo 1:1
  User↔Gimnasio + `rol`: `staff` o `alumno`).

**Regla al agregar cualquier modelo de dominio (Fase 1+):** heredar de
`TenantOwnedModel`, y si tiene FK a otro modelo tenant-owned, el form debe
heredar de `TenantScopedModelForm`. Las vistas de gestión van con
`TenantScopedMixin`.

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
  rediseño "tabla ancha por columna") como `rutinas/pdf.py::
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
- **`pagos`** — `PagoMensual(TenantOwnedModel)` y `MedioCobro(TenantOwnedModel)`
  (alias/CBU/lo que el gimnasio muestra al alumno para pagar, editable por
  staff). `pagos/models.py` expone `generar_pagos_pendientes(mes, anio)` y
  `marcar_vencidos(mes, anio, dia)`; `python manage.py generar_pagos` corre
  ambas para el mes/día actual — lo programa
  `.github/workflows/generar-pagos.yml` (GitHub Actions, no Render: no hay
  cron en el plan free). `marcar_vencidos` vence tanto los pendientes de
  meses ya cerrados como los del mes en curso que ya pasaron el
  `Gimnasio.dia_vencimiento_pago` de su propio gimnasio (join por FK, cada
  gimnasio tiene el suyo) — antes ese campo era solo cosmético en el portal
  del alumno.
- **`novedades`** — `Novedad(TenantOwnedModel)` con `NovedadQuerySet.visibles()`
  (activa + publicada + no vencida), y `NovedadLeida` (read-receipt por
  alumno; no es `TenantOwnedModel`, se scopea vía su FK a `Novedad`/`Alumno`
  que ya está acotada) para el badge "Nueva" del portal y el conteo de
  lecturas que ve el staff.
- **`turnos`** y **`calendario`** — agenda de reservas con cupos y su
  integración opcional con Google Calendar; ver sección propia abajo.
- **`importaciones`** — importador de planes de entrenamiento y biblioteca de
  ejercicios desde Excel; ver "Importador de Excel (Proyecto 2)" abajo.

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

## Borrar: `core/borrado.py` + `BorrarConExplicacionView`

Agregado el 2026-09-02 a pedido del dueño (eliminar plantillas, ejercicios y
alumnos). Un `DeleteView` pelado acá es una fábrica de 500: casi todo el
historial cuelga con `on_delete=PROTECT`, así que el borrado revienta con
`ProtectedError` justo en los casos más comunes.

**Regla de producto:** borrar de verdad lo que NO tiene historial (cargado por
error, pruebas — el caso real), y cuando no se puede, decirlo en castellano y
ofrecer la salida que ya existe. **Nunca borrar historial de cobros en
cascada**: `PagoMensual` es el registro de lo que el gimnasio facturó.

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
de rutinas asignadas. `series`/`repeticiones` siguen obligatorios a propósito:
no hay valor sensato que inventar y un item sin ellas le llega al alumno como
una fila vacía.

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
- **Dashboard**: `tenants.views.HomeView` (ruta `home`) — bifurca por
  `perfil.rol`. Para `staff`: métricas de Fase 2 §1 (alumnos activos, alumnos
  con pago pendiente, pagos del mes, rutinas activas, últimas novedades) +
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
- `PagoMensual` sigue sin vista de "crear" — el staff solo confirma pagos ya
  autogenerados (principio no negociable §3).

## Portal del alumno y acceso (Fase 3)

- **Alta de gimnasios: por comando, no self-serve.** `/accounts/register/` era
  público y sin throttling (cualquiera creaba User + Gimnasio + Perfil STAFF y
  quedaba logueado); se cerró el 2026-07-29, ver `ISSUES.md`. Hoy el único
  camino es `python manage.py crear_gimnasio`
  (`tenants/services.py::crear_gimnasio`), que crea al dueño con
  `set_unusable_password()` porque el staff va a entrar por Google. **No
  reintroduzcas una vista de registro** sin volver a discutir la decisión.
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
  la plantilla renderiza todo en una sola pantalla mobile-first (ROADMAP
  Fase 3: "entiende su rutina sin explicación adicional"). Si el `Perfil` de
  rol `alumno` todavía no está vinculado a un `Alumno`, se muestra un estado
  vacío, no un error 500.

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
  6 opciones curadas de Google Fonts (Inter, Montserrat, Poppins, Oswald,
  Playfair Display) más `sistema` como default — texto libre queda afuera a
  propósito, mismo criterio que `grupo_muscular` de `Ejercicio`: un catálogo
  cerrado evita que el dueño rompa la estética con una fuente ilegible. El
  default `sistema` no carga ningún recurso externo: mapea a `var(--font-sans)`,
  el stack que Tailwind v4 ya aplica por preflight, así que un gimnasio
  existente no cambia de aspecto hasta que el dueño elige explícitamente. Las
  demás opciones se sirven desde Google Fonts CDN, no auto-hospedadas (mismo
  criterio que Alpine.js/htmx por CDN) — se reevalúa si el tráfico lo
  justifica. El mapeo tipografía → (familia CSS, query de Google Fonts) vive
  en `Gimnasio.TIPOGRAFIA_FUENTES`, única fuente de verdad para `base.html` y
  el preview en vivo de `gimnasio_form.html`. La variable `--font-gimnasio`
  sigue el mismo patrón que los colores (default en `input.css`, override
  inline en `base.html`), aplicada vía `font-[family-name:var(--font-gimnasio)]`
  en `body` — el hint `family-name:` es necesario porque sin él Tailwind
  interpreta un valor arbitrario de font sin ese hint como `font-weight`, no
  `font-family` (ambigüedad de la sintaxis de valores arbitrarios). **Gotcha
  de autoescape**: el valor de
  `tipografia_css_family` se inyecta en `base.html` con `|safe` a propósito —
  `<style>` es un elemento "raw text" y el navegador no decodifica entidades
  ahí adentro, así que sin `|safe` el autoescape de Django convierte las
  comillas de `'Playfair Display'` en `&#x27;` y rompe el CSS en vez de
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
  MISMO scope de `x-data`, si no el toggle no hace nada). No se usó para
  nada más ("solo si hace falta", ROADMAP Fase 4).

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
es alumno, ir al login de siempre.

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

Con `gimnasio` en contexto, el copy de marketing genérico
("Gestionar tu gimnasio es más fácil...") y el dibujo de atletas
(`atletas_frieze.html`) se reemplazan por el nombre del gimnasio y su
`texto_bienvenida` — decisión explícita del dueño del producto: un alumno
logueándose a SU gimnasio no debería ver un pitch de venta dirigido a
dueños de gimnasios. Sin `gimnasio` en contexto (login genérico, o cuando
Django redirige acá desde `LOGIN_URL` por una vista protegida — ese flujo
no tiene forma de saber el slug), el template renderiza EXACTAMENTE igual
que antes: paisaje Bosque default, copy de marketing y atletas.

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
  entorno. **En tests, Django ya fuerza `EMAIL_BACKEND` a `locmem`
  automáticamente** (built-in del test runner) — a diferencia de R2/Google,
  no hace falta usar `TESTING` para esto.
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

- **`notificaciones`** es la app más nueva del proyecto, **última** en
  `INSTALLED_APPS` a propósito: sus signals/servicios leen
  `Novedad`/`RutinaAsignada`/`Reserva`/`PagoMensual`/`Gimnasio`/`Perfil`, así
  que depende de todo el dominio (mismo criterio de orden que el resto de
  `INSTALLED_APPS`).
- **`SuscripcionPush`** (una fila por dispositivo/navegador, NO OneToOne con
  el usuario — un alumno puede tener el celu y la PC suscriptos) **es
  `TenantOwnedModel`**, no solo scopeada vía FK a `usuario`: mismo argumento
  que `RegistroSuplantacion` — la consulta natural de un broadcast es "todas
  las suscripciones de MI gimnasio", no "las de este usuario". `gimnasio` se
  stampa en el alta desde `usuario.perfil.gimnasio`, nunca del cliente.
- **`RecordatorioEnviado`** es el mecanismo de dedup para los eventos que
  dispara el cron (ver abajo) — modelo satélite en `notificaciones`, sin
  agregar ningún campo "ya notificado" a `Novedad`/`PagoMensual`/`Reserva`,
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
  `PagoMensual` PENDIENTE/VENCIDO — **no cambia `estado`**, el staff sigue
  siendo el único que confirma el pago. Esta vista dispara la notificación
  con una llamada directa (no un signal): un `post_save` genérico sobre
  `PagoMensual` no podría distinguir "subió el alumno" de "confirmó el
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

## Deploy (Fase 5)

**Estado (2026-08-19): desplegado y con dominio propio.** App en
`https://www.tugimapp.com` (y `https://tugimapp.com`) — Render free tier,
Blueprint aplicado, media en el bucket R2 `app-gim-media`. Repo en
`https://github.com/fabri07/app-gim` (privado).

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
integraciones. **Pendiente, sin confirmar todavía**: los registros SPF/DKIM
que pida el proveedor de email — el dominio es lo que destraba el email
transaccional (sin verificación de dominio, Resend solo deja enviar a la
casilla propia), pero no hay evidencia de que este paso ya se haya hecho.

- **Plan elegido: arrancar en el free tier de Render, upgradear cuando entre
  el primer gimnasio pago** (decisión del usuario, coincide con "primero se
  cobra, después se sofistica"). El web service se duerme sin tráfico —
  aceptado a propósito, ver `ISSUES.md`.
- **`render.yaml`** define el Blueprint: **solo el web service (free)**. Ya no
  declara `databases:` — la base salió del Blueprint el 2026-07-29 y vive en
  Neon (ver abajo). Tampoco tiene cron: **Render no ofrece plan free para cron
  jobs**, así que los dos trabajos programados corren en GitHub Actions.
- **La base es Neon, no Render** (migrada el 2026-07-29, ver `ISSUES.md`). El
  Postgres free de Render expiraba a los 30 días + 14 de gracia y después
  Render borra los datos; el de Neon es free permanente. `DATABASE_URL` se
  carga a mano en el dashboard de Render (`sync: false`) con la URL **POOLED**.
  La URL **DIRECTA** (sin `-pooler` en el host) se usa solo desde el workflow
  de backup: el pooler no sostiene bien la sesión larga de un `pg_dump`.
  `config/db.py` activa `conn_health_checks=True` — es obligatorio contra Neon,
  que suspende el compute por inactividad (scale-to-zero); sin el chequeo
  Django reusa conexiones muertas del pool de `CONN_MAX_AGE` y los requests
  fallan de forma intermitente.
- **Trabajos programados (GitHub Actions, no Render):**
  - `.github/workflows/generar-pagos.yml` — corre `manage.py generar_pagos`
    todos los días 06:30 UTC. Usa la URL **pooled** (conexiones cortas).
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
- **Cloudflare R2 — creado y en uso.** Bucket `app-gim-media`, endpoint
  `https://<account_id>.r2.cloudflarestorage.com`. Las 4 credenciales
  (`R2_BUCKET_NAME`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/
  `R2_ENDPOINT_URL`) están en el `.env` local y en el dashboard de Render
  (van marcadas `sync: false` en el Blueprint, así que no se leen del repo —
  verificarlas ahí, no acá).
- **Qué se guarda en R2 y qué no** (pregunta recurrente): R2 guarda SOLO los
  archivos subidos por usuarios, que son exactamente tres campos —
  `Gimnasio.logo` (`logos/`), `PagoMensual.comprobante` (`comprobantes/`) e
  `Importacion.archivo` (`importaciones/`, el `.xlsx` original). **Todo el
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
- **Lo que sigue pendiente**: (a) **rotar la contraseña de Neon** (quedó
  expuesta en texto plano) y actualizarla en los tres lugares — `DATABASE_URL`
  en Render y los dos secrets de GitHub; ojo que ya no hay base vieja como
  vuelta atrás, así que verificar las tres puntas después; (b) ~~apuntar
  `tugimapp.com`~~ **hecho el 2026-08-19** — ver más arriba; (c) smoke test
  manual end-to-end de turnos → Google Calendar contra producción, ahora en
  el dominio nuevo; (d) confirmar si los registros SPF/DKIM del email ya se
  cargaron (ver nota de dominio más arriba — sin confirmar todavía).
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

## Comandos

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # completar si hace falta

python manage.py migrate
python manage.py runserver
python manage.py test -v 2           # suite completa
python manage.py test alumnos        # solo una app
python manage.py test alumnos.tests.AlumnoTests.test_creacion_basica_y_str
                                     # un solo test (ruta punteada app.tests.Clase.metodo)
python manage.py createsuperuser     # acceso a /admin/
python manage.py crear_gimnasio --nombre "Gimnasio Central" --email dueno@gmail.com
                                     # imprime una contraseña provisoria; ver
                                     # --sin-password (solo cuando exista Google login)
python manage.py generar_pagos       # autogenera pendientes del mes + vence atrasados
python manage.py collectstatic       # solo hace falta simulando producción (DEBUG=False)

npm install                          # una vez, para compilar Tailwind
npm run build:css                    # compila static/css/input.css -> app.css
npm run watch:css                    # lo mismo, en watch mode durante desarrollo
```

## Canales de auditoría (fallas y problemas)

- **`logs/app.log`** (rotado, no versionado): logging estructurado
  configurado en `config/settings.py` (`LOGGING`). Captura consola + archivo;
  `django.request` a nivel `ERROR` asegura que un 500 no manejado quede
  registrado, no solo impreso en una consola efímera.
- **`ISSUES.md`**: registro humano de problemas, causa y resolución —
  complementa al log (que es de runtime, no de decisiones). Agregar una
  entrada ahí cada vez que se resuelve algo no obvio o se acepta un riesgo a
  propósito.
- **Tests** (`python manage.py test`): cada modelo tenant-owned nuevo debe
  tener al menos un test de aislamiento (que un gimnasio no vea datos de
  otro), siguiendo `tenants/tests.py::TenantIsolationTests` como referencia.

## Qué NO construir todavía

QR de ingreso, control de asistencia, app nativa, Mercado Pago/débitos
automáticos, chat interno, nutrición, métricas deportivas, IA de rutinas,
subdominios por gimnasio. Ver ROADMAP.md § "Fase 9" para la lista completa y
cuándo sí tiene sentido evaluarlas (solo con clientes pagos).
