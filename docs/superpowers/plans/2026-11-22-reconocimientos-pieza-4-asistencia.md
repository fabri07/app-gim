# Reconocimientos — Pieza 4: Asistencia Implementation Plan (v2, revisado)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> Al aprobarse, la versión final de este archivo reemplaza a `docs/superpowers/plans/2026-11-22-reconocimientos-pieza-4-asistencia.md` (ya existe en la rama como borrador v1) y se commitea.

**Goal:** Saber, con datos confiables, **qué días fue cada alumno al gimnasio** dentro de su ciclo de 28 días y contra cuántas sesiones tenía que ir. Es la base de los premios por asistencia perfecta (Pieza 5): esta pieza NO da premios ni descuentos, solo registra y calcula. El staff marca asistencia desde la agenda de turnos, desde la ficha del alumno y con una pantalla «Pasar lista» para los gimnasios sin turnos ni lector.

**Architecture:** App nueva `asistencia/` con un modelo `Asistencia` (un día de un alumno, marcado por el staff) y un módulo de servicio que une DOS fuentes en un conjunto de fechas locales: los ingresos del lector (`ingreso.EventoIngreso` PERMITIDO + ENTRADA) y las marcas del staff. La unión (no «elegir una fuente») hace que un lector caído no le quite la asistencia a nadie. La meta del ciclo = cantidad de sesiones previstas (pares día/semana) de la rutina vigente al inicio del ciclo. Todo detrás de `reconocimientos_habilitado` (apagada en todos los gimnasios): con la bandera apagada, ningún botón ni pantalla nuevos existen.

**Rama:** `worktree-reconocimientos-pieza-4` (ya creada), **apilada sobre `worktree-reconocimientos-pieza-2`** (que contiene la pieza 1): necesita `Gimnasio.reconocimientos_habilitado`. PR contra la rama de la pieza 2 (o contra `main` cuando #4 y #5 se mergeen).

**Tech Stack:** Django 5.2 (CBV + templates, `hx-boost` global).

**Spec:** `docs/superpowers/specs/2026-11-21-reconocimientos-design.md` § Pieza 4 (vive en la rama de la pieza 1).

## Global Constraints
- Todo lo nuevo visible solo con `gimnasio.reconocimientos_habilitado`: apagada → 404 en las pantallas y los links/botones no se renderizan (el HTML de la agenda y de la ficha queda idéntico al de hoy).
- **Solo el staff escribe asistencia.** El «día entrenado» que el alumno marca en su rutina NO cuenta (es autodeclarado y se puede marcar días después).
- Un día cuenta **una sola vez** aunque haya ingreso + marca + dos turnos.
- Fechas con `timezone.localdate()` / `timezone.localtime(...)` (TIME_ZONE = Buenos Aires, USE_TZ: un ingreso de las 22:30 locales es del día local, no del siguiente en UTC). Tests con reloj congelado.
- `fecha` de una marca: no futura y no más vieja que `MAX_DIAS_ATRAS = 35` (constante, no un setting).
- Solo se marca a alumnos `ACTIVO` del propio gimnasio. Un alumno sin ancla (`fecha_inicio_ciclo = None`) o sin rutina vigente al inicio del ciclo **no tiene meta** y no participa.
- Un `TenantOwnedModel` nuevo va a TRES lugares en el mismo commit: `tenants/demo.py::vaciar_gimnasio`, `_ensuciar` en `tenants/tests.py`, `HOJAS` de `tenants/exportacion.py`; más un test de aislamiento.
- `hx-boost` global: las acciones son POST con redirect y mensaje (`messages`), nunca 4xx para un error de uso; permisos sí dan 403/404.
- Sin N+1: costo de queries constante aunque crezcan alumnos, reservas o ingresos (tests de escala). Cada campo de formulario con su línea de error. `{# #}` de una línea.
- Baseline de la suite = el de la rama pieza-2 al empezar (se mide en T0); cualquier rojo es regresión.

## Decisiones propias (rulings, corregibles)
1. **App nueva `asistencia/`** y no dentro de `turnos`: la asistencia vale también para quien no usa turnos. Va en `INSTALLED_APPS` después de `ingreso` (lee `ingreso`, `rutinas`, `pagos`, `alumnos`, `turnos`). No hay otra app ni URL con ese nombre (verificado).
2. **Una fila por (gimnasio, alumno, día)** (`UniqueConstraint`). Marcar de nuevo el mismo día no duplica (y un doble click simultáneo no da 500: se atrapa `IntegrityError`). `origen` guarda cómo se creó (`turno` o `manual`); no afecta el conteo.
3. **Quitar una marca** la borra. Los ingresos del lector NO se pueden quitar desde la app (son datos de la puerta): si el lector marcó el día, cuenta y la pantalla lo muestra como «lector».
4. **Reserva ≠ asistencia.** Cancelar o reconciliar una reserva borra la fila (`turnos/services.py`), así que no sirve como registro. «Asistió» desde la agenda crea una `Asistencia` independiente; si después la reserva desaparece, la asistencia queda.
5. **Meta = `len(sesiones_previstas)` de la rutina vigente AL INICIO del ciclo** (misma regla de orden que `RutinaAsignada.vigente_de`: activa, `fecha_inicio <= fecha`, gana `-fecha_inicio, -id`; escrita una vez en `asistencia.services.rutina_vigente_en(alumno, fecha)`). Si el plan cambia a mitad del ciclo, la meta no se mueve. El ciclo es el de pago (`pagos.models.ciclo_vigente`, 28 días desde `fecha_inicio_ciclo`).
6. **Tope de antigüedad de 35 días** para marcar: el staff puede corregir el ciclo que recién cerró; más viejo no.
7. **«Pasar lista»**: lista de alumnos activos de un día con casillas, para el gimnasio sin turnos ni lector. Filtro por nombre (`?q=`), **paginada de a 200**, y el POST solo actúa sobre los ids de ESA página (campo oculto `ids`).
8. **Los menores de 18** no participan en rankings (regla del dueño). La asistencia SE REGISTRA igual para todos (dato operativo del staff); si los premios de asistencia perfecta cuentan como «ranking» para menores es una pregunta de la Pieza 5.
9. **Funciones por lote desde ya**: `dias_asistidos_por_alumno` resuelve N alumnos con ventanas distintas en 2 queries, para que la Pieza 5 no haga un N+1 al cerrar ciclos.

## Segunda pasada (verificada contra el código) — qué sumó
- **`DATA_UPLOAD_MAX_NUMBER_FIELDS` (1000 por defecto):** un «Pasar lista» con todos los alumnos de un gimnasio grande en una sola página rompería el POST con 400. → paginación de 200 y POST limitado a los ids de la página (T5).
- **Destildar solo en la página:** sin el campo `ids`, una casilla ausente se leería como «destildada» y borraría marcas de alumnos que ni se mostraron. → el servidor solo considera los ids enviados (T5).
- **Doble click / dos pestañas:** `get_or_create` con la unicidad puede dar `IntegrityError`; se atrapa y se trata como «ya estaba» (T3/T4/T5).
- **Zona horaria:** confirmado `TIME_ZONE = 'America/Argentina/Buenos_Aires'` y `USE_TZ = True`; la conversión a día local es obligatoria y tiene su test de borde (T2).
- **Guardianes existentes:** `test_ensuciar_cubre_todos_los_modelos_tenant_owned` exige una fila del modelo nuevo en `_ensuciar`, y `EXCLUIDOS`/`HOJAS` exigen cubrir el modelo: T1 los agrega juntos o la suite se pone roja.
- **Ficha del alumno:** `AlumnoDetailView` ya arma contexto de ingresos (`_control_de_ingreso`); la tarjeta nueva se arma con una función del servicio y se incluye con `{% include %}` solo con bandera.
- **Sin cambios en pantallas del alumno ni en `pagos`:** esta pieza solo importa la función pura `ciclo_vigente`.

## Tercera pasada (revisión pedida por el usuario) — qué sumó
- **`<details>` se cierra tras cada click (usabilidad real):** en la agenda cada franja es un `<details>` cerrado; con boost cada «Asistió» recarga la página y el staff tendría que volver a abrir la franja para marcar al siguiente. → con la bandera, las franjas de HOY y pasadas con reservas se renderizan `<details open>` (T3, con test).
- **Dirección de dependencias:** `asistencia` lee `turnos` (modelo `Reserva`) y `ingreso`; `turnos` NO debe importar `asistencia` a nivel de módulo. La agenda lo hace con **import tardío dentro de `get_context_data`** y el comentario de `INSTALLED_APPS` lo documenta (mismo patrón que `pagos`→`tienda`). `turnos` ya figura antes de `ingreso` en `INSTALLED_APPS`: sin ciclos.
- **Quién marcó, a la vista (es plata más adelante):** la asistencia perfecta terminará dando descuentos (Pieza 5) y hoy cualquier staff puede marcar (no hay sub-cuentas). → `registrada_por` y la fecha de carga se muestran en la tarjeta de la ficha («marcada por X el DD/MM»), y la confirmación del premio por el staff en la Pieza 5 sigue siendo el control. Test de que se ve (T4).
- **La meta es el ritmo semanal × 4, no una cuenta del calendario:** `len(sesiones_previstas)` = (días por semana del plan) × 4 aunque el ciclo de pago y el plan arranquen en fechas distintas; si el alumno cambia de plan a mitad de ciclo la meta queda la del plan vigente al inicio (decisión 5). Se documenta en `ARQUITECTURA.md` para que nadie «arregle» la desalineación sin hablarlo con el dueño.
- **Marcas por fecha, no por ciclo:** mover el ancla del ciclo de un alumno (`fecha_inicio_ciclo`) no pierde ni mueve ninguna marca; solo cambia la ventana contra la que se cuentan.
- **«Asistió» el mismo día antes de la clase:** se permite (fecha ≤ hoy): el staff marca cuando el alumno llega; no se bloquea por horario de la franja.
- **Verificado en el código:** el campo `usuario` de `Perfil` existe (`registrada_por__usuario__username` es una ruta válida para la hoja de exportación); `EventoIngreso.alumno` es nullable (eventos de desconocidos se ignoran por el filtro de alumno); las reservas pasadas siguen listadas en la agenda (`reservas_por_franja` no filtra por fecha).

## Review Focus (cada línea tiene su test en la tarea que la posee)
1. Un día con ingreso + marca + dos turnos = 1 día. → T2
2. Ingreso a las 22:30 hora Buenos Aires (01:30 UTC del día siguiente) cuenta en el día local. → T2
3. Ingreso DENEGADO, SALIDA, de otro alumno o de otro gimnasio no cuenta. → T2
4. El staff no puede marcar futuro, > 35 días atrás, alumno INACTIVO ni de otro gimnasio; un alumno recibe 403; sin bandera 404. → T3/T4/T5
5. Alumno sin ancla o sin rutina vigente al inicio: sin meta, sin error. → T2/T4
6. Cancelar la reserva después de marcar «asistió» no borra la asistencia. → T3
7. Borrar el alumno borra sus marcas; `vaciar_gimnasio`, exportación y aislamiento cubren el modelo nuevo. → T1
8. «Pasar lista» nunca borra marcas de alumnos que no estaban en la página enviada ni días del lector. → T5

## File Structure
- Create `asistencia/__init__.py`, `apps.py`, `models.py`, `services.py`, `views.py`, `urls.py`, `forms.py`, `admin.py`, `tests*.py`, migración `0001_initial`
- Create `templates/asistencia/pasar_lista.html`, `templates/asistencia/partials/tarjeta_ficha.html`
- Modify `config/settings.py` (INSTALLED_APPS), `config/urls.py`, `tenants/demo.py` (`vaciar_gimnasio`), `tenants/tests.py` (`_ensuciar`), `tenants/exportacion.py` (`HOJAS`)
- Modify `turnos/views.py` (`AgendaView` contexto) y `templates/turnos/agenda.html`; `templates/alumnos/alumno_detail.html` y `alumnos/views.py::AlumnoDetailView.get_context_data` (tarjeta «Asistencia»)
- Docs: `ARQUITECTURA.md` (sección «Reconocimientos»), `ISSUES.md`

---

### Task 0: Línea base
- [ ] Correr `python manage.py test` en la rama (redirigiendo a un archivo del scratchpad, no con `| tail`) y anotar «Ran N tests, OK» en el ledger.

---

### Task 1: App y modelo `Asistencia` (+ los tres lugares)

**Files:** Create `asistencia/{__init__,apps,models,admin}.py`, migración; modify `config/settings.py`, `tenants/demo.py`, `tenants/tests.py`, `tenants/exportacion.py`; tests `asistencia/tests_modelo.py`.

**Interfaces — Produces:** `Asistencia(TenantOwnedModel)`: `alumno` FK `alumnos.Alumno` CASCADE `related_name="asistencias"`; `fecha` DateField; `hora` TimeField null/blank (franja de la reserva, informativo); `origen` Char(10) choices `TURNO`/`MANUAL`; `registrada_por` FK `tenants.Perfil` null SET_NULL `related_name="+"`; `creado` auto_now_add. `UniqueConstraint(["gimnasio","alumno","fecha"], name="asistencia_unica_por_dia")`; `Index(["gimnasio","fecha"])`.

- [ ] **Step 1: tests**: crear/recuperar; la unicidad rechaza mismo alumno/día/gimnasio y permite otro día u otro gimnasio; borrar el alumno borra sus marcas; borrar el `Perfil` de `registrada_por` deja la marca con `None`; aislamiento (`for_gimnasio`); `vaciar_gimnasio` la borra; `_ensuciar` la crea y el test de cobertura sigue verde; la exportación incluye la hoja `asistencias`.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar app + migración aditiva (`makemigrations asistencia -n initial`), registro en `INSTALLED_APPS` tras `ingreso` con comentario de qué lee; `vaciar_gimnasio` borra `Asistencia` antes de `alumnos`; `_ensuciar` crea una; `Hoja("asistencias", "asistencia.Asistencia", "gimnasio", descripción, columnas: id, alumno_id, alumno_apellido, alumno_nombre, fecha, hora, origen, registrada_por (`registrada_por__usuario__username`), creado)`.
- [ ] **Step 4:** `python manage.py test asistencia tenants` verde; `makemigrations --check` limpio. **Step 5:** commit `feat(asistencia): modelo Asistencia`.

---

### Task 2: Servicio — días asistidos, meta y resumen del ciclo

**Files:** Create `asistencia/services.py`, `asistencia/tests_services.py`.

**Interfaces:**
- Consumes: `EventoIngreso`, `Asistencia`, `pagos.models.ciclo_vigente`, `rutinas.progreso.sesiones_previstas`, `RutinaAsignada`.
- Produces: `MAX_DIAS_ATRAS = 35`; `dias_asistidos_por_alumno(gimnasio, ventanas: dict[int, tuple[date, date]]) -> dict[int, set[date]]` (**2 queries**: ingresos PERMITIDO/ENTRADA del gimnasio en el rango total y marcas del staff; cada fecha pasada a día LOCAL y filtrada por la ventana propia de cada alumno); `dias_asistidos(alumno, inicio, fin) -> set[date]`; `rutina_vigente_en(alumno, fecha) -> RutinaAsignada | None`; `meta_del_ciclo(alumno, inicio) -> int | None`; `ResumenCiclo` (dataclass frozen: `inicio, fin, meta, dias, dias_lector` + propiedades `asistidos`, `faltan`); `resumen_del_ciclo(alumno, hoy=None) -> ResumenCiclo | None` (`None` si el alumno no está ACTIVO o `ciclo_vigente` da `None`).

- [ ] **Step 1: tests** (reloj congelado): ingreso + marca el mismo día + dos marcas «turno» = 1 día; ingreso a las 22:30 locales (UTC del día siguiente) cuenta en el día local y uno a las 00:10 locales en ese día; DENEGADO, SALIDA, otro alumno, otro gimnasio y marcas de otro gimnasio no cuentan; bordes inclusivos de la ventana; `dias_asistidos_por_alumno` con 3 alumnos de ventanas distintas devuelve lo suyo a cada uno y **`assertNumQueries(2)`** con 3 y con 30 alumnos; `meta_del_ciclo`: 3 días × 4 semanas = 12, semanas con un día menos cuentan pares reales, sin rutina → `None`, rutina que arranca DESPUÉS del inicio → `None`, dos rutinas el mismo día gana el mayor id, plan cambiado a mitad de ciclo no mueve la meta; `resumen_del_ciclo`: sin ancla / INACTIVO / ancla futura → `None`, ciclo normal con meta y asistidos, `faltan` nunca negativo, `dias_lector` solo días con ingreso.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (día local con `timezone.localtime(momento).date()` en Python; rango de `momento` = `[inicio 00:00 local, fin+1 00:00 local)` con `timezone.make_aware`). Comentario de módulo: por qué la unión y por qué el toggle del alumno no cuenta. **Step 4:** verde. **Step 5:** commit `feat(asistencia): días asistidos, meta y resumen del ciclo`.

---

### Task 3: Marcar «asistió» desde la agenda de turnos

**Files:** Create `asistencia/views.py` (`MarcarAsistenciaReservaView`), `asistencia/urls.py`; modify `config/urls.py` (`path("asistencia/", include("asistencia.urls"))`), `turnos/views.py::AgendaView.get_context_data`, `templates/turnos/agenda.html`; tests `asistencia/tests_agenda.py`.

**Interfaces — Produces:** URL `asistencia:marcar_reserva` (`POST /asistencia/reservas/<int:pk>/`, campo `accion` = `marcar|quitar`, `semana` para volver a la misma semana); `AgendaView` agrega `marcas` (set de `(alumno_id, fecha)`) y `asistencia_habilitada`.

- [ ] **Step 1: tests**: con bandera y staff, la agenda muestra «Asistió» en reservas de hoy y de días pasados (≤ 35) y NO en futuras; «Quitar» si ya hay marca; POST `marcar` crea `Asistencia(origen=TURNO, hora=reserva.hora_inicio, registrada_por=perfil)`, idempotente; `quitar` la borra; reserva futura o > 35 días → mensaje y no crea; alumno INACTIVO → mensaje; reserva de otro gimnasio → 404; usuario alumno → 403; anónimo → login; sin bandera → 404 y la agenda con HTML idéntico al de hoy; cancelar la reserva después NO borra la `Asistencia`; costo de queries de la agenda constante al crecer las reservas (una query extra); el redirect conserva `?semana=`; doble POST simultáneo (simulado con `IntegrityError`) no da 500; con bandera, las franjas de hoy y pasadas con reservas llevan `<details open>` y las futuras no. `AgendaView` importa `asistencia` solo dentro de `get_context_data` (se revisa en el diff, sin test).
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (vista POST con `StaffRequiredMixin` y `TenantScopedMixin`; errores de uso → `messages.warning` + redirect). Template: dentro del `<li>` de cada reserva un `<form method="post">` con `csrf_token`. **Step 4:** verde + `python manage.py test turnos asistencia`. **Step 5:** commit `feat(asistencia): marcar asistencia desde la agenda`.

---

### Task 4: Tarjeta «Asistencia» en la ficha del alumno

**Files:** Modify `alumnos/views.py::AlumnoDetailView.get_context_data`, `templates/alumnos/alumno_detail.html`; create `asistencia/forms.py` (`RegistrarAsistenciaForm`), vistas `RegistrarAsistenciaView` y `QuitarAsistenciaView`, URLs `asistencia:registrar` (`POST /asistencia/alumnos/<int:pk>/registrar/`) y `asistencia:quitar` (`POST /asistencia/<int:pk>/quitar/`); tests `asistencia/tests_ficha.py`.

- [ ] **Step 1: tests**: con bandera la ficha muestra la tarjeta con «X de Y sesiones» del ciclo vigente (días listados con «marcada por X el DD/MM» cuando la cargó el staff; los del lector marcados «lector» y sin botón de quitar); sin ancla → «Sin fecha de inicio del ciclo: no se cuenta la asistencia»; sin rutina vigente → «Sin plan al inicio del ciclo: no hay meta» (sin error); el form valida fecha obligatoria, no futura, ≤ 35 días, con su línea de error por campo (un error de formulario re-renderiza la ficha con 200, no 4xx); registrar crea `origen=MANUAL`, idempotente; quitar solo marcas del gimnasio (otra → 404); alumno INACTIVO → no se registra; sin bandera la ficha no muestra la tarjeta y las URLs dan 404; sin N+1 por día listado.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (tarjeta como `{% include %}`; el contexto lo arma `resumen_del_ciclo`, no la plantilla). **Step 4:** `python manage.py test asistencia alumnos`. **Step 5:** commit `feat(asistencia): tarjeta de asistencia en la ficha`.

---

### Task 5: Pantalla «Pasar lista»

**Files:** Create `asistencia/views.py::PasarListaView`, `templates/asistencia/pasar_lista.html`, URL `asistencia:pasar_lista` (`/asistencia/pasar-lista/`, GET con `?fecha=`, `?q=`, `?pagina=`; POST guarda); link «Pasar lista» en `templates/turnos/agenda.html` (con bandera); tests `asistencia/tests_pasar_lista.py`.

- [ ] **Step 1: tests**: GET muestra los alumnos ACTIVOS del gimnasio (no inactivos ni de otro gimnasio) con casilla tildada si ya hay marca ese día y «lector» (sin casilla) si entraron por el lector; `?q=` filtra por nombre/apellido; paginado de a 200; POST con casillas crea las marcas faltantes (`origen=MANUAL`) y borra las destildadas **solo entre los ids enviados en el campo oculto `ids`** y **solo si eran marcas manuales/turno** (nunca días del lector ni de alumnos fuera de la página); ids de otro gimnasio o INACTIVOS se ignoran; fecha futura o > 35 días → mensaje y no guarda; fecha por defecto hoy; queries constantes con 5 y con 60 alumnos; alumno → 403; sin bandera → 404.
- [ ] **Step 2:** correr → falla. **Step 3:** implementar (marcas y lector resueltos con `dias_asistidos_por_alumno` y una ventana de un solo día; redirect a la misma fecha/página con mensaje «N marcadas, M quitadas»). **Step 4:** verde. **Step 5:** commit `feat(asistencia): pantalla para pasar lista`.

---

### Task 6: Documentación y cierre
- [ ] `ARQUITECTURA.md` (sección «Reconocimientos»: asistencia, unión de fuentes, qué no cuenta, meta, 35 días, bandera) y `ISSUES.md` (decisiones y pendientes).
- [ ] Suite completa verde (≥ baseline + tests nuevos), `makemigrations --check` limpio, `git status -sb` sin untracked. Commit `docs(asistencia): pieza 4`.

## Verificación end-to-end
- Tests por tarea + suite completa sobre la rama y sobre el merge.
- QA en navegador (local `seed_qa`, R2 vacío, bandera prendida): reservar un turno como alumno; como staff, agenda → «Asistió» → la ficha muestra 1 día; «Pasar lista» con 3 alumnos; fecha futura y > 35 días; celular 390 px y modo oscuro; con la bandera apagada no aparece nada nuevo.

## Qué NO se puede romper
| Área | Riesgo | Cómo se cuida |
|---|---|---|
| Agenda de turnos | HTML nuevo rompe la grilla o suma queries por fila | Botones solo con bandera; una query extra; test de escala; sin bandera el HTML es el de hoy |
| Reservas / cancelación | Acoplar asistencia a la fila de `Reserva` | Modelo independiente; test «cancelar no borra la asistencia» |
| Control de ingreso | Cambiar eventos del lector | Solo se LEEN (`PERMITIDO` + `ENTRADA`); nada se escribe |
| Borrado de alumnos / demo / exportación | Modelo tenant-owned sin los tres lugares | Task 1 los cubre con los guardianes existentes |
| Ficha del alumno | Contexto más pesado | Tarjeta solo con bandera; función del servicio de costo fijo |
| Portal del alumno y plata | Ninguno | Esta pieza no toca pantallas del alumno ni `pagos` (solo importa `ciclo_vigente`, función pura) |

## Abierto
- ¿Los premios de asistencia perfecta cuentan como «ranking» para menores de 18 (Pieza 5)? Hasta que lo defina el dueño, el premio de asistencia no excluye a menores y los rankings (kilos) sí.
- Mostrarle al alumno su propio avance («5 de 12») es parte de la Pieza 5 (junto con el premio); esta pieza solo la ve el staff.
