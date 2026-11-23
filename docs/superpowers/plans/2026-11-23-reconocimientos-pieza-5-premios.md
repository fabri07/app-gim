# Reconocimientos — Pieza 5: Premios + secuencia a producción (v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (método ya elegido por el usuario; revisión final con **sonnet**). Steps use checkbox (`- [ ]`) syntax.
> Al aprobarse, este archivo se copia a `docs/superpowers/plans/2026-11-23-reconocimientos-pieza-5-premios.md` y se commitea en la rama de la pieza 5.

## Context
Las piezas 1-4 (perfil/términos/foto, ranking, descuento en la cuota, asistencia) están construidas y con PR abierto (#4, #5, #6, #7), todo detrás de `Gimnasio.reconocimientos_habilitado` (apagada en todos los gimnasios). Falta lo que le da sentido al sistema: **el premio por asistencia perfecta**. Pedido del usuario (23-11): los premios pueden ser descuentos en la mensualidad, combos para pagar la anualidad o precios especiales por 3 o 6 meses; los **menores no se exponen por asistencia pero SÍ pueden recibir premios**; y que yo decida la mejor secuencia para llevar los PR a producción.

**Respuesta del usuario a la única pregunta abierta:** los combos y precios por varios meses son **descuento aplicado ciclo a ciclo** (1, 3, 6 o 13 cuotas seguidas). El **pago adelantado real** (un solo pago que cubra N ciclos) queda FUERA: es otra pieza que toca el núcleo de pagos. Una «anualidad en un pago» se ofrece como texto/obsequio que el gimnasio cobra a mano.

## Parte A — Secuencia a producción (mi decisión)

**Hechos que la fijan (verificados):**
- `main` despliega solo y el `buildCommand` de Render corre `migrate`: **cada merge a main es un deploy con migraciones sobre Neon**.
- Topología: #4 (p1, base main) ← #5 (p2) ← #7 (p4); #6 (p3, base main) es **independiente** de la pila. Pieza 5 necesita **p3 (descuentos) y p4 (asistencia) y p2 (app `reconocimientos`)**.
- #6 y #7 tocan en común 4 archivos: `ARQUITECTURA.md`, `ISSUES.md`, `templates/alumnos/alumno_detail.html`, `tenants/exportacion.py` (conflictos previsibles, de texto).
- El `CheckConstraint cuota_descuento_valido` y el `Case/When` de acreditar por MP **solo se probaron en SQLite**. `docker` existe en la Mac; `psql` no.
- Todo está apagado por bandera: el efecto en producción de las piezas 1, 2, 4 es **ninguno visible** (solo columnas/tablas nuevas con `db_default`). La excepción es #6: el staff ya puede cargar un descuento a mano en «Confirmar pago»/«Corregir monto», sin bandera — es plata y por eso va primero y sola.

**Orden:**
0. **Ensayo local, sin push** (rama temporal `integracion-reconocimientos` desde `main`): mergear #6, #4, #5, #7 en ese orden, resolver los conflictos, suite completa, `makemigrations --check`, y **suites de `pagos` y `asistencia` contra Postgres 16 en docker** (constraint y Case/When). Reporto números reales. *La pieza 5 se construye sobre esta rama de integración.*
1. **#6 (descuento) — primero y solo.** Merge con commit de merge (**no squash**: squashear rompe las ramas apiladas). Esperar deploy verde; probar en `gimnasio-verificacion-r2` (nunca en Vida Plena): cargar un descuento a una cuota de prueba y ver monto neto en listado y en el link de MP.
2. **#4 → #5 → #7**, de a uno. Tras cada merge: `gh pr edit --base main` del siguiente, merge de `main` adentro de la rama, suite sobre ese resultado, deploy verde. Humo post-deploy con la bandera apagada: login de staff y de alumno, home, agenda, ficha, «Mi perfil» (404), ranking (404).
3. **Pieza 5 → PR #8** contra `main`, cuando 1-2 estén en producción.
4. **Prender la bandera NO es parte de la secuencia.** Requiere: textos legales aprobados por el dueño/abogado, QA en navegador (celular 390 px, claro/oscuro), primero `gimnasio-verificacion-r2`, y Vida Plena solo cuando su dueño lo pida. Recién ahí vuelve el checkbox a «Mi gimnasio».

**Compuertas:** pido tu confirmación explícita **antes de cada merge a main** (es un deploy a producción). El ensayo del paso 0 y toda la construcción son locales.

## Parte B — Pieza 5: Premios

**Goal:** que el staff, con un clic y a la vista, premie a quien completó **todas** las sesiones de su ciclo de 28 días, con el premio que el gimnasio definió (descuento en % o en pesos sobre las próximas 1/3/6/13 cuotas, u obsequio/combo a mano) y que el alumno lo vea.

**Architecture:** tres modelos en `reconocimientos`: `Premio` (catálogo del gimnasio), `PremioOtorgado` (el premio dado a un alumno por un ciclo, con todo congelado) y `PremioAplicacion` (una fila por cuota que recibió el descuento: es el contador y el candado de idempotencia). Un servicio decide **quién califica** (en lote, queries constantes), **otorga** (idempotente, el staff confirma: nada automático con plata) y **aplica pendientes** (reusa `pagos.descuentos.aplicar_descuento` de la pieza 3). El cron diario `generar_pagos` llama a `aplicar_pendientes` después de emitir cuotas, así un descuento prometido llega a cuotas que todavía no existían.

**Tech Stack:** Django 5.2 CBV + templates, `hx-boost`; sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-11-21-reconocimientos-design.md` § Pieza 5 (la spec original tenía una sola config `PremioAsistencia`; este plan la reemplaza por catálogo, por el pedido del 23-11).

**Rama:** `worktree-reconocimientos-pieza-5` desde `integracion-reconocimientos` (merge local de #6, #4, #5, #7). Cuando la Parte A termine, se rebasa/mergea `main` adentro antes del PR.

### Global Constraints
- Todo visible solo con `reconocimientos_habilitado`: apagada → 404 en pantallas, links ocultos. **Excepción deliberada:** `aplicar_pendientes` corre igual con la bandera apagada, para no perder un descuento ya prometido si el gimnasio apaga la bandera después.
- **El staff confirma, nada automático con plata.** Único código que escribe descuento: `aplicar_descuento` (tope 90%, nunca en cuota pagada/anulada, cancela links de MP vivos).
- **Menores de 18:** pueden recibir premios; **nunca** aparecen para compañeros (ni cuadro de honor ni insignia visible); su insignia y su avance los ve solo él. El staff los ve marcados «Menor de 18».
- **Baja/inactivo:** no califica ni recibe nada. Fuente: `Alumno.Estado.ACTIVO`.
- Fechas con `timezone.localdate()`; tests con reloj congelado. Sin `fecha + columna` en el queryset (Postgres).
- **Sin N+1:** el costo de «Premios del ciclo» y del cuadro de honor es constante al crecer alumnos, rutinas y reservas (tests de escala). Cada campo de form con su línea de error; `{# #}` de una línea; tokens semánticos del modo oscuro; `hx-boost`: errores de uso = redirect + `messages`, nunca 4xx.
- Un `TenantOwnedModel` nuevo va a TRES lugares en el mismo commit (`tenants/demo.py::vaciar_gimnasio`, `_ensuciar` de `tenants/tests.py`, `HOJAS`/`EXCLUIDOS` de `tenants/exportacion.py`). `PremioAplicacion` **no** es tenant-owned (se scopea por su `PremioOtorgado`, mismo patrón que `NovedadLeida`) y no va a los tres.
- `varchar` de Postgres: `descuento_motivo` es `max_length=80` → el motivo se **trunca en Python** a 80.
- Dinero: `Decimal`, redondeo hacia abajo a centavos (como `descuento_maximo`).

### Decisiones propias (rulings, corregibles)
1. **Catálogo, no config única.** `Premio` por gimnasio, varias filas, `activo` en vez de borrar (patrón `MedioCobro`), una `por_defecto` (índice único parcial). El staff elige cuál dar en cada caso (viene preseleccionado el de por defecto).
2. **Tipos:** `PORCENTAJE` (1-90), `FIJO` (pesos > 0), `OBSEQUIO` (texto: obsequio o combo que se resuelve a mano). Para los dos descuentos, `cuotas` ∈ 1..13 (1 = la próxima; 3 = trimestre; 6 = semestre; 13 = anualidad de 28 días). `da_insignia` bool.
3. **Asistencia perfecta = `asistidas >= meta`**, sin tolerancia en v1 (la spec traía `faltas_toleradas`; el nombre del premio es «perfecta» y nadie la pidió; queda diferido).
4. **Ventana de candidatos:** ciclos **cerrados** (`fin < hoy`) con `fin >= hoy - MAX_DIAS_ATRAS` (35, el mismo tope con el que el staff puede cargar asistencia). Sin margen de 7 días de la spec: el staff decide cuándo confirmar, y esperar una semana haría que casi siempre el descuento cayera dos cuotas más tarde. Puede haber hasta 2 ciclos candidatos por alumno. Pasados los 35 días el premio se pierde (no se puede corregir asistencia más vieja).
5. **Sin deuda:** no califica quien tenga una cuota `PENDIENTE`/`VENCIDO` con `periodo_inicio <= fin del ciclo` (la del propio ciclo y las anteriores). Alumno sin cuotas emitidas no tiene deuda.
6. **Dónde cae el descuento:** a las próximas `cuotas` cuotas **impagas, con `descuento = 0`, sin `PremioAplicacion`, con `periodo_inicio > fin del ciclo premiado`**, en orden cronológico. Una cuota con descuento manual se saltea (no consume el premio). Si el alumno ya pagó la pre-emitida, el descuento va a la siguiente impaga. Cuota con monto 0: se saltea. `FIJO` se **recorta** al tope de la cuota.
7. **Un premio por (alumno, ciclo)** mientras esté `OTORGADO` (índice único parcial); si se anula por error, se puede volver a dar.
8. **Anular** un otorgado frena las aplicaciones futuras; lo ya aplicado queda (el staff lo edita en la cuota si quiere). Obsequio: el staff marca «Entregado».
9. **Insignia:** `insignia_hasta = periodo_fin + 28 días`. Visible para compañeros solo en el **Cuadro de honor** de la pantalla de ranking (nombre + inicial, **solo adultos que participan**); el alumno la ve siempre en «Mi perfil».
10. **Fuera de esta pieza** (anotado en `ISSUES.md`): mini-perfil de compañero con foto (la foto hacia compañeros pide su propia revisión de privacidad), pago adelantado real, tolerancia de faltas, ranking de asistencia.
11. **Aviso al alumno:** una `Novedad` personal (`alumno` seteado) al otorgar; no push (se verifica en T4 que crear una novedad personal no dispare nada masivo).

### Review Focus (cada línea tiene su test en la tarea que la posee)
1. Doble clic en «Dar premio» / dos pestañas: un solo `PremioOtorgado`, una sola `Novedad`, sin 500. → T3/T4
2. POST forjado: `premio_id` de otro gimnasio o inactivo, alumno no elegible o dado de baja entre listado y POST, ciclo fuera de ventana → mensaje, no escribe nada. → T3/T4
3. `FIJO` mayor que el tope o cuota en 0 → se recorta / se saltea, nunca viola `cuota_descuento_valido`. → T3
4. Cuota con link de MP vivo: queda `CANCELADO`; pago de MP en vuelo se acredita bien (pieza 3). → T3
5. Cron y confirmación a la vez: el descuento se aplica **una sola vez** (unicidad de `cuota` en `PremioAplicacion` + `solo_si_sin_descuento` bajo el lock). → T3
6. Un menor califica y recibe, pero no sale en el cuadro de honor ni en nada de compañeros. → T3/T5
7. Premio de 3 cuotas con la 2.ª ya pagada / anulada / con descuento manual: salta las que no corresponden. → T3
8. Borrar un alumno con premios sigue funcionando. → T2
9. Bandera apagada: pantallas 404, links ocultos, pero `aplicar_pendientes` sigue honrando lo prometido. → T3/T4
10. El cron de cobro no se rompe si premios falla (try/except con log) y su salida no cambia. → T3

### File Structure
- Create `reconocimientos/models.py`, `reconocimientos/migrations/0001_initial.py` (la app hoy no tiene migraciones), `reconocimientos/premios.py` (servicio), `reconocimientos/forms.py`, `reconocimientos/admin.py`, `reconocimientos/honor.py` (cuadro de honor + «personas visibles»), tests `reconocimientos/tests_*.py`
- Create `templates/reconocimientos/premios_lista.html`, `premio_form.html`, `premios_ciclo.html`, `partials/tarjeta_alumno.html`, `partials/cuadro_de_honor.html`
- Modify `reconocimientos/views.py`, `urls.py`; `pagos/descuentos.py` (kwarg `solo_si_sin_descuento`); `pagos/management/commands/generar_pagos.py` (hook); `tenants/demo.py`, `tenants/tests.py`, `tenants/exportacion.py`; `templates/base.html` (link «Premios» en la nav del staff, con bandera); `alumnos/views.py::MiPerfilView` + `templates/alumnos/mi_perfil.html`; `templates/reconocimientos/ranking.html` + `views.py::RankingView` (cuadro de honor); `templates/tenants/privacidad.html`; `ARQUITECTURA.md`, `CLAUDE.md` (fila de tabla), `ISSUES.md`

---

### Task 0: Integración y línea base (Parte A, paso 0)
- [ ] Crear `integracion-reconocimientos` desde `main`; mergear #6, #4, #5, #7 en ese orden; resolver los 4 conflictos previstos conservando ambas partes.
- [ ] Suite completa a un archivo del scratchpad (no `| tail`); anotar «Ran N tests, OK» en el ledger. `makemigrations --check` limpio.
- [ ] Postgres 16 en docker (`DATABASE_URL`): correr `pagos` y `asistencia`; anotar el resultado. Un rojo acá **no se arregla en silencio**: se reporta, porque cambia la compuerta de #6.
- [ ] Crear `worktree-reconocimientos-pieza-5` sobre esa rama.

### Task 1: Catálogo de premios (modelo + pantallas del staff)
**Files:** Create `reconocimientos/models.py` (`Premio`), migración, `forms.py` (`PremioForm`), `admin.py`, vistas `PremiosListView`/`PremioCreateView`/`PremioUpdateView`, URLs `reconocimientos:premios`, `premio_nuevo`, `premio_editar`, templates; modify `tenants/{demo,tests,exportacion}.py`, `templates/base.html`.
**Interfaces — Produces:** `Premio(TenantOwnedModel)`: `nombre` Char(80); `tipo` (`PORCENTAJE|FIJO|OBSEQUIO`); `valor` Decimal(10,2) null/blank; `cuotas` PositiveSmallInteger default 1 (1..13); `texto` Char(200) blank; `da_insignia` bool default True; `por_defecto` bool; `activo` bool default True. `UniqueConstraint(["gimnasio"], condition=Q(por_defecto=True), name="premio_un_solo_por_defecto")`.
- [ ] **Step 1: tests:** `clean()` — PORCENTAJE exige 1..90, FIJO exige > 0, OBSEQUIO exige `texto` y no usa valor/cuotas; `cuotas` fuera de 1..13 rechazado; marcar `por_defecto` desmarca el anterior (en transacción) y la base rechaza dos; no hay botón de borrar (se desactiva); la lista del staff solo muestra los del gimnasio; alumno → 403; sin bandera → 404; cada campo del form con su error; aislamiento; `vaciar_gimnasio`, `_ensuciar` y la hoja `premios` de la exportación cubren el modelo.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar; `PremioForm` hereda de `TenantScopedModelForm`. **Step 4:** `python manage.py test reconocimientos tenants` verde. **Step 5:** commit `feat(reconocimientos): catálogo de premios`.

### Task 2: Modelos `PremioOtorgado` y `PremioAplicacion`
**Files:** modify `reconocimientos/models.py`, migración `0002`, `admin.py`, `pagos/descuentos.py`, `tenants/{demo,tests,exportacion}.py`; tests `reconocimientos/tests_modelos.py`.
**Interfaces — Produces:**
- `PremioOtorgado(TenantOwnedModel)`: `alumno` FK CASCADE `related_name="premios"`; `premio` FK `Premio` SET_NULL null; congelados: `nombre` Char(80), `tipo`, `valor`, `cuotas`, `texto`, `da_insignia`; `periodo_inicio`, `periodo_fin`, `meta`, `asistidas`; `estado` (`OTORGADO|ANULADO`); `entregado_en` DateTime null; `insignia_hasta` Date null; `otorgado_por` FK `tenants.Perfil` SET_NULL; `creado`. `UniqueConstraint(["gimnasio","alumno","periodo_inicio"], condition=Q(estado="otorgado"), name="premio_otorgado_unico_por_ciclo")`. Propiedad `restantes` (= `cuotas` − aplicaciones, ≥ 0).
- `PremioAplicacion(TimeStampedModel)`: `otorgado` FK CASCADE `related_name="aplicaciones"`; `cuota` FK `pagos.Cuota` CASCADE; `descuento` Decimal; `UniqueConstraint(["cuota"], name="premio_aplicacion_una_por_cuota")`.
- `pagos.descuentos.aplicar_descuento(cuota_pk, gimnasio, descuento, motivo, ahora=None, *, solo_si_sin_descuento=False)`: con el flag, aplica **solo si el descuento leído bajo el lock es 0**.
- [ ] **Step 1: tests:** unicidad parcial (anulado + nuevo OK; dos otorgados no); un alumno no tiene dos aplicaciones sobre la misma cuota; **borrar el alumno borra sus premios y aplicaciones** (y `Alumno` sigue borrable desde `core/borrado.py`); borrar el `Perfil` de `otorgado_por` deja `None`; `aplicar_descuento(..., solo_si_sin_descuento=True)` no pisa un descuento manual y los tests existentes de la pieza 3 no cambian; los tres lugares.
- [ ] **Step 2-5:** rojo → implementar → verde (`reconocimientos tenants pagos`) → commit `feat(reconocimientos): premio otorgado y su aplicación`.

### Task 3: Servicio — candidatos, otorgar, aplicar pendientes (+ cron)
*Es el núcleo de plata: implementer con modelo más capaz, como la T3 de la pieza 3.*
**Files:** create `reconocimientos/premios.py`, tests `reconocimientos/tests_premios.py`; modify `pagos/management/commands/generar_pagos.py`.
**Interfaces — Produces:**
- `Candidato` (dataclass frozen: `alumno_id, nombre, apellido, es_menor, inicio, fin, meta, asistidas`).
- `candidatos(gimnasio, hoy=None, alumno_ids=None) -> list[Candidato]`: alumnos ACTIVO con ancla, ciclos cerrados dentro de la ventana (decisión 4), `meta` no `None` y `asistidas >= meta`, sin deuda (decisión 5), sin `PremioOtorgado` OTORGADO para ese ciclo. **Queries constantes (≈10)**: alumnos; por cada desplazamiento de ciclo, `dias_asistidos_por_alumno` (2); rutinas activas de esos alumnos ordenadas `(alumno, -fecha_inicio, -id)` y elegir en Python la vigente al inicio; ítems `(asignada_id, dia, semana)` distintos de las elegidas (meta = pares); cuotas impagas; otorgados existentes. `alumno_ids` para revalidar un solo alumno.
- `otorgar(gimnasio, alumno_id, inicio, premio_id, perfil) -> (PremioOtorgado | None, motivo)`: **revalida** con `candidatos(..., alumno_ids=[alumno_id])` y exige `Premio` activo del gimnasio; todo en `transaction.atomic()`; crea el otorgado con los datos congelados, la `Novedad` personal (título «¡Asistencia perfecta!», mensaje según el premio, `visible_hasta` +60 días) y llama a `aplicar_pendientes`. Doble clic (`IntegrityError` del índice parcial) → devuelve el existente **sin** segunda novedad.
- `aplicar_pendientes(hoy=None, alumno_ids=None) -> int`: otorgados OTORGADO de tipo descuento con `restantes > 0` (**una query con el conteo anotado**); cuotas candidatas en **una query** (decisión 6); asigna en Python por otorgado en orden de `periodo_inicio`; por cada asignación, `transaction.atomic()` con `aplicar_descuento(..., solo_si_sin_descuento=True)` + `PremioAplicacion.create` (si el `IntegrityError` salta, se deshace el descuento). Motivo = «Premio: {nombre}» truncado a 80. `PORCENTAJE`: `monto * valor / 100` hacia abajo a centavos; `FIJO`: `min(valor, descuento_maximo(monto))`; resultado ≤ 0 → se saltea la cuota.
- `anular(gimnasio, otorgado_pk)`, `marcar_entregado(gimnasio, otorgado_pk)`.
- **Hook:** en `generar_pagos.handle`, después de `generar_pagos_pendientes` y `marcar_vencidos`, `try: aplicar_pendientes() except Exception: logger.exception(...)`; **import tardío** (`pagos` no importa `reconocimientos` a nivel módulo); el mensaje existente no cambia (la línea nueva solo si aplicó algo).
- [ ] **Step 1: tests (reloj congelado):** califica con `asistidas == meta` y con más; no con `meta − 1`; sin meta (sin rutina) no; INACTIVO no; ancla `None` no; **menor sí califica** y `es_menor` True; deuda del propio ciclo o anterior excluye, la pre-emitida del ciclo siguiente no; bordes de ventana (`fin == hoy` no; `hoy − 35` sí; `hoy − 36` no); dos ciclos candidatos; otorgado vigente lo saca de la lista; **`assertNumQueries` igual con 3 y con 40 alumnos**; `otorgar` — revalida (alumno que dejó de calificar, `premio_id` ajeno/inactivo, ciclo fuera de ventana → `(None, motivo)` sin escribir), congela datos (editar el `Premio` después no cambia el otorgado), una `Novedad` personal, doble clic = un otorgado y una novedad; `aplicar_pendientes` — 1 cuota / 3 cuotas / 13 con las cuotas apareciendo de a una entre corridas (vía `generar_pagos_pendientes`), idempotente corriendo dos veces, **salta** cuota pagada, anulada, con descuento manual y con monto 0, `FIJO` recortado al 90%, `PORCENTAJE` redondea hacia abajo, link de MP vivo → `CANCELADO` y neto correcto, dos otorgados del mismo alumno toman cuotas distintas en orden, ANULADO no aplica, bandera apagada **igual aplica**, alumno dado de baja no recibe; comando `generar_pagos` — misma salida de siempre y **no falla si `aplicar_pendientes` revienta** (mock) y deja el log.
- [ ] **Step 2:** rojo. **Step 3:** implementar. **Step 4:** verde (`reconocimientos pagos`). **Step 5:** commit `feat(reconocimientos): otorgar premios y aplicarlos a las cuotas`.

### Task 4: Pantalla «Premios del ciclo» (staff)
**Files:** modify `reconocimientos/views.py`, `urls.py`; create `templates/reconocimientos/premios_ciclo.html`; tests `reconocimientos/tests_premios_ciclo.py`.
**Interfaces — Produces:** `reconocimientos:premios_ciclo` (GET lista; POST `accion` ∈ `dar|anular|entregado`). La lista: candidatos con selector de premio (preseleccionado el `por_defecto`), botón «Dar premio» por fila y el cartel «Menor de 18» (el staff lo ve completo), más «Otorgados recientemente» (últimos 60 días) con estado, cuántas cuotas faltan aplicar, «Entregado» (obsequio) y «Anular». Sin premios activos en el catálogo: cartel que lleva a crearlos.
- [ ] **Step 1: tests:** el staff ve candidatos y otorgados; «Dar premio» crea y avisa («Premio dado a X»), idempotente; POST forjado (premio ajeno, alumno no elegible, ciclo viejo) → mensaje y 302, nada escrito; anular y entregado cambian estado y no tocan otro gimnasio (404/ignorado); alumno → 403; anónimo → login; sin bandera → 404; costo de queries constante con 5 y 60 candidatos; **crear la Novedad personal no manda ningún push masivo** (verificar las señales de `novedades`/`notificaciones`); menores aparecen para el staff y se premian; link «Premios» en la nav del staff solo con bandera.
- [ ] **Step 2-5:** rojo → implementar → verde (`reconocimientos`) → commit `feat(reconocimientos): pantalla de premios del ciclo`.

### Task 5: Lado del alumno — avance, premios y cuadro de honor
**Files:** create `reconocimientos/honor.py`, `templates/reconocimientos/partials/{tarjeta_alumno,cuadro_de_honor}.html`; modify `alumnos/views.py::MiPerfilView`, `templates/alumnos/mi_perfil.html`, `reconocimientos/views.py::RankingView`, `templates/reconocimientos/ranking.html`; tests `reconocimientos/tests_alumno.py`.
**Interfaces — Produces:**
- `honor.alumnos_visibles(gimnasio) -> QuerySet[Alumno]`: ACTIVO + `aparece_en_ranking` + mayor de 18 (`fecha_corte_mayoria`) + términos vigentes aceptados. **Test de consistencia** que pinta una matriz (menor, sin fecha de nacimiento, baja, sin términos, opt-out) y exige que `honor` y `ranking.participantes` coincidan en las condiciones de persona.
- `honor.cuadro_de_honor(gimnasio, hoy=None) -> list[str]`: nombres públicos (mismo `_nombre_publico` del ranking) de visibles con un otorgado OTORGADO, `da_insignia` y `insignia_hasta >= hoy`. **Una query.**
- «Mi perfil»: tarjeta «Tu asistencia» (`resumen_del_ciclo`: «5 de 12 sesiones», faltan N; sin meta/sin ancla → texto, sin error) y «Tus premios» (nombre, «se aplicará en tu próxima cuota» / «aplicado en N de M cuotas» / obsequio «retiralo en el gimnasio» o «entregado»), más su insignia vigente. Visible para menores.
- Ranking: sección «Cuadro de honor» arriba; los menores no ven la pantalla (cartel existente).
- [ ] **Step 1: tests:** el alumno ve su avance y premios (también un menor); **un menor no sale en el cuadro de honor de nadie y un adulto sin términos / con opt-out / dado de baja tampoco**; insignia vencida no sale; el nombre es «Nombre I.»; sin bandera 404; suplantando el staff ve lo mismo sin escribir; queries constantes con 5 y 50 otorgados.
- [ ] **Step 2-5:** rojo → implementar → verde (`reconocimientos alumnos`) → commit `feat(reconocimientos): avance, premios y cuadro de honor del alumno`.

### Task 6: Textos legales, documentación y cierre
- [ ] `templates/tenants/privacidad.html` (sección `ranking-premios-foto`): una frase que diga que la asistencia perfecta puede mostrarse a compañeros adultos como insignia/cuadro de honor con nombre e inicial, y que los **menores no** se muestran. **Es propuesta: queda marcada en `ISSUES.md` para el dueño/abogado.** Sin subir `VERSION_TERMINOS` (la bandera nunca estuvo prendida: nadie aceptó todavía); si para entonces alguien aceptó, se sube.
- [ ] `ARQUITECTURA.md` (sección «Reconocimientos»: catálogo, ventana de 35 días, dónde cae el descuento, por qué el cron aplica con la bandera apagada, dependencias `pagos`→`reconocimientos` solo por import tardío), fila en la tabla de `CLAUDE.md`, `ISSUES.md` (rulings, lo diferido, el texto legal pendiente).
- [ ] Suite completa verde (≥ línea base de T0 + tests nuevos), `makemigrations --check` limpio, `git status -sb` sin untracked. Commit `docs(reconocimientos): pieza 5`.
- [ ] Revisión final del branch completo con **sonnet**; una ola de arreglos; push y PR #8 **solo con tu confirmación**.

## Segunda mirada (pasadas adversariales, verificadas contra el código)

### Pasada 1 — corrección
- `bulk_create(ignore_conflicts=True)` **no devuelve PKs** en Postgres → el hook no depende de las cuotas recién creadas: `aplicar_pendientes` re-consulta por su cuenta (y por eso corre aunque el generador no cree nada).
- `Cuota.descuento_motivo` es `varchar(80)` y `Premio.nombre` llega a 80: «Premio: » + nombre pasa de 80 → truncado en Python (SQLite no lo detecta, Postgres sí).
- `FIJO` sobre una cuota chica violaría `cuota_descuento_valido` (`descuento <= monto*0.90`) → recorte con `descuento_maximo`; cuota en 0 → no se puede descuento > 0 → se saltea.
- Reservar el índice parcial `condition=Q(...)` con `UniqueConstraint` funciona en SQLite y Postgres (Django 5.2); se prueba en T0 contra Postgres.
- El generador de cuotas filtra `gimnasio__activo=True`; `aplicar_pendientes` no necesita ese filtro (solo mira cuotas impagas existentes), pero **sí** exige alumno ACTIVO.
- `meta_del_ciclo` de la pieza 4 es por alumno (varias queries): usarla en la pantalla sería un N+1. → T3 trae una versión en lote (rutinas de todos en una query, ítems en otra). Queda el test de escala.
- Dos ciclos candidatos por alumno (ciclo cerrado hace 3 días + el anterior hace 31): `dias_asistidos_por_alumno` toma una ventana por alumno → se llama una vez por desplazamiento de ciclo (4 queries, constante).

### Pasada 2 — no-regresión, armonía y omisiones
- **No romper el cobro:** el único cambio en `pagos` es un kwarg con default `False` (los tests de la pieza 3 no se tocan) y un bloque con `try/except` + import tardío en el comando. La salida del comando y el workflow `generar-pagos.yml` no cambian. Test de que un fallo de premios no tumba la emisión de cuotas.
- **Dirección de dependencias:** `reconocimientos` (última en `INSTALLED_APPS`) lee `pagos`, `asistencia`, `novedades`, `alumnos`; `pagos` solo la importa tarde, dentro de `handle`. Mismo patrón que `pagos`→`tienda`.
- **Borrado de alumnos:** todo lo nuevo es `CASCADE` (alumno, otorgado→aplicación, cuota→aplicación) → ningún `PROTECT` nuevo; test explícito en T2 y revisar `core/borrado.py` por si lista dependencias.
- **Guardianes existentes** (`test_ensuciar_cubre_todos_los_modelos_tenant_owned`, `HOJAS`/`EXCLUIDOS`): `Premio` y `PremioOtorgado` entran juntos a los tres lugares en T1/T2; `PremioAplicacion` no es tenant-owned a propósito. Si algún test cuenta hojas del ZIP, se actualiza.
- **Privacidad:** el cuadro de honor sale de **una** definición de «persona visible» con test de consistencia contra `participantes()`; un menor, un opt-out, un sin-términos o una baja no aparecen. El nombre es el mismo `_nombre_publico` (nombre + inicial). La foto sigue sin mostrarse a compañeros.
- **Plata en vuelo:** aplicar descuento a una cuota con link de MP vivo cancela el link (reuso de `aplicar_descuento`); el pago ya iniciado se acredita con la lógica de la pieza 3 (`F("descuento")+cobrado`). Ya hay tests de eso en `pagos`; T3 agrega el caso desde el premio.
- **Omisión cerrada — deshacer errores:** anular un otorgado y volver a dar (índice parcial) y editar la cuota a mano si hubo que revertir lo ya aplicado.
- **Omisión cerrada — apagar la bandera después:** los descuentos prometidos siguen aplicándose (decisión explícita, documentada).
- **Nav del staff ya está cargada** (`base.html` advierte sobre nav de 9-10 links): «Premios» entra solo con bandera; se mira en 390 px en el QA.
- **Qué NO se puede romper:**

| Área | Riesgo | Cómo se cuida |
|---|---|---|
| Cobro y MP | Descuento doble, link con monto viejo | `aplicar_descuento` es el único escritor; `solo_si_sin_descuento` bajo lock; unicidad por cuota |
| `generar_pagos` (cron) | Un error de premios corta la emisión de cuotas | `try/except` + log, import tardío, test con mock que revienta |
| Analítica de ingresos | Subcuenta | Ya usa `Cuota.NETO`; sin cambios |
| Borrado de alumnos / demo / exportación | FK PROTECT o modelo sin los 3 lugares | `CASCADE`; checklist por modelo |
| Privacidad de menores | Exposición por asistencia | Un solo filtro de visibles + test de consistencia |
| Alumno y portal | Pantallas nuevas fuera de bandera | 404 sin bandera; links ocultos |

## Verificación end-to-end
- Tests por tarea; suite completa en la rama y **otra vez sobre el merge a `main`**; `makemigrations --check`; Postgres en docker para `pagos`/`asistencia`/`reconocimientos`.
- Verificar que cada test de regresión **falla sin el fix**.
- **QA en navegador** (local con `seed_qa`, bandera prendida, celular 390 px, claro y oscuro): crear 3 premios (% de 1 cuota, fijo de 3 cuotas, obsequio); marcar asistencia completa de dos alumnos (uno menor); «Premios del ciclo» → dar; ver el descuento en el listado de pagos y en el link de MP; «Mi perfil» del alumno con su premio; cuadro de honor sin el menor; bandera apagada → nada visible.
- Humo post-deploy de cada merge de la Parte A con la bandera apagada.

## Abierto
- Texto legal definitivo (dueño/abogado) y qué dice exactamente sobre el cuadro de honor.
- Mini-perfil con foto de compañero, tolerancia de faltas y pago adelantado real: quedan como piezas futuras si el uso lo pide.
