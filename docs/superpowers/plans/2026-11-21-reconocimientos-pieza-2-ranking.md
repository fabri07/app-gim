# Reconocimientos — Pieza 2: Ranking de kilos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> Al aprobarse, este archivo se copia a `docs/superpowers/plans/2026-11-21-reconocimientos-pieza-2-ranking.md` (primer commit de la rama).

**Goal:** Pantalla «Ranking» en el portal del alumno: para los 10 ejercicios más usados del gimnasio, quién tiene prescripto más peso (nombre + inicial del apellido), solo con la bandera `reconocimientos_habilitado`.

**Architecture:** App nueva `reconocimientos/` SIN modelos. Un parser de kilos (`rutinas/kilos.py`) + un módulo de lectura (`reconocimientos/ranking.py`, 2 queries fijas, sin cache) + una vista de solo lectura. Rama `worktree-reconocimientos-pieza-2`, apilada sobre `worktree-reconocimientos-pieza-1` (PR #4, sin mergear: usa `reconocimientos_habilitado`, `Alumno.aparece_en_ranking`, `Perfil.terminos_version`, `VERSION_TERMINOS`).

**Tech Stack:** Django 5.2 (CBV + templates), `Decimal`, `importaciones.parsing.comun.normalizar_texto`.

**Spec:** `docs/superpowers/specs/2026-11-21-reconocimientos-design.md` § Pieza 2.

## Global Constraints
- Nada de modelo nuevo → sin los «tres lugares» de `TenantOwnedModel`.
- Visible solo con `gimnasio.reconocimientos_habilitado`; apagada → 404 (el ítem del nav tampoco aparece).
- Solo alumnos (`AlumnoRequiredMixin`, el staff recibe 403); alumno sin ficha → estado vacío; el middleware de términos ya frena al que no aceptó.
- Se muestra **nombre + inicial del apellido** («Lucía M.»), nunca apellido completo, email, teléfono ni foto (la foto queda fuera de esta pieza: cierra el pendiente de fotos de INACTIVOS).
- Dato mostrado = kilos **prescriptos por el staff**; la pantalla lo dice.
- Sin N+1: nº de queries constante aunque crezcan alumnos y ejercicios (test de escala).
- Fechas con `timezone.localdate()`. `{# #}` de una línea. Cada pantalla con tokens del modo oscuro (reusar clases existentes, `tarjeta`, `texto-suave`).
- Suite: baseline de la rama pieza-1 = 3928 OK; cualquier rojo es regresión.

## Decisiones propias (rulings, corregibles)
1. **Sin cache.** LocMem es por proceso (varios workers de gunicorn): un alumno que se da de baja del ranking seguiría apareciendo hasta 10 min en otros workers. Es privacidad; el costo es 2 queries por visita. Si algún día pesa, se mide y se cachea sin datos personales.
2. **Participantes** de un ejercicio = alumnos del universo con al menos un `kilos` parseable en ese ejercicio en su rutina vigente. Top 10 ejercicios = los de más participantes (desempate por nombre normalizado). Se muestran solo los que tienen **≥ 3 participantes** (`MINIMO_PARTICIPANTES = 3`) para no exponer a un alumno solo.
3. **Universo = «participantes»** (pedido del usuario: quien se dio de baja o ya no va al gimnasio deja de participar en CUALQUIER ranking). Alumnos `ACTIVO` del gimnasio (la baja los pasa a `INACTIVO`), con `aparece_en_ranking=True`, con `perfil` y `perfil.terminos_version >= VERSION_TERMINOS`, y con rutina vigente (misma regla que `RutinaAsignada.vigente_de`: activa, `fecha_inicio <= hoy`, gana `-fecha_inicio, -id`) **que además no esté abandonada**: `vigente_de` devuelve «el último plan» para siempre, así que un alumno que dejó de ir con su plan viejo seguiría figurando. Regla: la rutina vigente tiene que haber arrancado hace como máximo `SEMANAS_POR_CICLO*7 + DIAS_GRACIA_RANKING` días (`DIAS_GRACIA_RANKING = 14`, constante, mismo criterio de «no es un setting más» de `DIAS_AVISO_PLAN`). Pasado eso el alumno sale solo; si el staff le carga un plan nuevo, vuelve. Esta definición vive en UNA función (`reconocimientos/ranking.py::participantes`) que las piezas 4 y 5 (cuadro de honor, insignias) reutilizan: nadie arma su propio filtro.
4. **Peso de un alumno en un ejercicio** = máximo parseable de todos los items de ese ejercicio en las 4 semanas de su rutina vigente. Empates comparten puesto (1,1,3). Cada alumno cuenta una vez por ejercicio.
5. **Nombre del ejercicio mostrado** = el snapshot más frecuente entre los items agrupados (desempate alfabético); agrupa `normalizar_texto(ejercicio_nombre_snapshot)`.
6. El alumno que se dio de baja del ranking **sí ve** la pantalla pero no aparece ni tiene «tu puesto».
7. «Tu puesto»: si el alumno está en el universo y participa en el ejercicio pero queda fuera del top 10 de ese ejercicio, se muestra su fila debajo («…», «Tu puesto: 14»).
8. **El checkbox de la bandera NO vuelve a «Mi gimnasio» en esta pieza** (cambia lo dicho en la Pieza 1): los términos/privacidad siguen siendo un borrador legal que el dueño del producto no revisó, y el cartel promete también «premios» (Pieza 5). Se prende por `/admin/` o shell. Reabrir cuando el texto esté aprobado.
9. App registrada al final de `INSTALLED_APPS` (después de `tienda`): no tiene modelos y nadie la lee; lee `alumnos`, `rutinas`, `tenants`, `importaciones` (imports de módulo; sin ciclos porque nadie importa `reconocimientos` salvo `config/urls.py`).

## Review Focus (cada línea tiene su test en la tarea que la posee)
1. Un `kilos` basura o enorme (`"corporal"`, `"60s"`, `"50%"`, `"+10kg"`, `"2000"`, `""`) nunca rompe ni aparece como ganador. → T1
2. Alumno que se da de baja del ranking, dado de baja (`INACTIVO`), con plan abandonado (arrancó hace > 42 días y nadie le cargó otro) o sin términos aceptados: desaparece de inmediato; reactivado o con plan nuevo, vuelve. → T2
3. Rutina futura o archivada no cuenta; dos rutinas con la misma `fecha_inicio` → gana la de mayor `id`. → T2
4. Gimnasio A nunca ve ni cuenta alumnos del B (mismo ejercicio, mismos nombres). → T2
5. Dos alumnos con igual peso comparten puesto; el siguiente salta. → T2

## File Structure
- Create `rutinas/kilos.py`, `rutinas/tests_kilos.py`
- Create `reconocimientos/__init__.py`, `apps.py`, `ranking.py`, `views.py`, `urls.py`, `tests.py`
- Create `templates/reconocimientos/ranking.html`
- Modify `config/settings.py` (INSTALLED_APPS + comentario), `config/urls.py`, `templates/partials/nav_alumno.html`
- Modify docs: `ARQUITECTURA.md` (sección «Reconocimientos»), `ISSUES.md`, fila en `CLAUDE.md` si hace falta

---

### Task 1: Parser de kilos

**Files:** Create `rutinas/kilos.py`, `rutinas/tests_kilos.py`.

**Interfaces:**
- Produces: `parsear_kilos(texto: str | None) -> Decimal | None` y `MAXIMO_KILOS = Decimal("500")`.

- [ ] **Step 1: tests** (`rutinas/tests_kilos.py`, `SimpleTestCase`, tabla de casos con `subTest`):
  - Aceptan → valor: `"20"`→20, `"20.0"`→20, `"20,5"`→20.5, `"20 kg"`→20, `"20KG"`→20, `" 20kg "`→20, `"15kg c/u"`→15, `"10-12kg"`→12, `"10 - 12 kg"`→12, `"0,5kg"`→0.5.
  - Devuelven `None`: `""`, `None`, `"corporal"`, `"60s"`, `"2 min"`, `"50%"`, `"+10kg"`, `"0"`, `"0kg"`, `"2000"` (> `MAXIMO_KILOS`), `"20 libras"`, `"RPE 8"`, `"20kg 30kg"`.
  - Resultado siempre `Decimal` (no float): `assertIsInstance`.
- [ ] **Step 2:** correr `python manage.py test rutinas.tests_kilos` → falla (módulo no existe).
- [ ] **Step 3: implementar** `parsear_kilos` con un `re.fullmatch` sobre `texto.strip().lower()`: número (`\d+([.,]\d+)?`), opcional rango `-`/`–` con segundo número (gana el mayor), opcional unidad `kg|kgs|kilo|kilos`, y solo si hubo unidad un resto permitido `c/u|c/lado|por lado|cada uno` (o vacío). Coma decimal → punto. `None` si no matchea, si el valor es `<= 0` o `> MAXIMO_KILOS`. Docstring: explica por qué `kilos` es texto libre y qué descarta.
- [ ] **Step 4:** tests pasan.
- [ ] **Step 5: commit** `feat(reconocimientos): parser de kilos`.

---

### Task 2: App `reconocimientos` y cálculo del ranking

**Files:** Create `reconocimientos/{__init__,apps,ranking,tests}.py`; Modify `config/settings.py`.

**Interfaces:**
- Consumes: `parsear_kilos`, `normalizar_texto` (`importaciones/parsing/comun.py`), `VERSION_TERMINOS` (`tenants/terminos.py`), modelos `Alumno`, `RutinaAsignada`, `RutinaAsignadaItem`.
- Produces (dataclasses frozen): 
  - `FilaRanking(puesto: int, nombre: str, kilos: Decimal, es_propia: bool)`
  - `BloqueEjercicio(ejercicio: str, participantes: int, filas: list[FilaRanking], tu_fila: FilaRanking | None)`
  - `calcular_ranking(gimnasio, alumno=None) -> list[BloqueEjercicio]`; constantes `TOP_EJERCICIOS = 10`, `TOP_POR_EJERCICIO = 10`, `MINIMO_PARTICIPANTES = 3`.

- [ ] **Step 1: registrar la app**: `'reconocimientos'` al final de `INSTALLED_APPS` con comentario (qué lee, que no tiene modelos). `apps.py` con `ReconocimientosConfig`.
- [ ] **Step 2: tests** (`reconocimientos/tests.py`, helpers que crean gimnasio, alumno con perfil y términos aceptados, rutina asignada con items `RutinaAsignadaItem(ejercicio_nombre_snapshot, kilos, semana, dia, orden, series, repeticiones)`):
  - `test_ranking_basico`: 3 alumnos, «Sentadilla» 60/80/70 → filas ordenadas 80,70,60; puestos 1,2,3; `participantes == 3`.
  - `test_toma_el_maximo_de_las_cuatro_semanas`: un alumno con 50 (sem 1) y 70 (sem 4) → 70.
  - `test_empate_comparte_puesto`: 80,80,60 → puestos 1,1,3.
  - `test_agrupa_por_nombre_normalizado`: «Sentadilla», «sentadilla », «SENTADILLA» → un solo bloque; el nombre mostrado es el más frecuente.
  - `test_ignora_kilos_no_parseables`: `corporal`, `60s`, `""` no cuentan como participantes ni rompen.
  - `test_minimo_de_participantes`: 2 alumnos → no hay bloque; 3 → sí.
  - `test_top_10_ejercicios_por_participantes`: 12 ejercicios con distinta cantidad de participantes (≥3) → solo los 10 con más; desempate por nombre.
  - `test_baja_del_ranking_inactivo_y_sin_terminos_no_aparecen`: `aparece_en_ranking=False`, `estado=INACTIVO`, `perfil.terminos_version=0`, y alumno sin `perfil` → ninguno cuenta (ni sube `participantes`).
  - `test_alumno_dado_de_baja_sale_y_al_reactivarlo_vuelve`: pasar `estado` a INACTIVO (con `save(update_fields=["estado","fecha_baja","fecha_inicio_ciclo"])`, regla del repo) → desaparece; reactivar → reaparece.
  - `test_plan_abandonado_sale`: rutina vigente con `fecha_inicio = hoy - 43 días` → fuera; `hoy - 42` → dentro (borde); con un plan nuevo cargado hoy → dentro. Con reloj congelado y `timezone.localdate()`.
  - `test_participantes_es_la_unica_definicion`: `participantes(gimnasio)` devuelve los mismos alumnos que aparecen en `calcular_ranking` (lo reusan las piezas 4-5).
  - `test_rutina_futura_o_archivada_no_cuenta` y `test_misma_fecha_inicio_gana_el_mayor_id` (la vigente es la de mayor id; los kilos de la otra no cuentan).
  - `test_solo_la_rutina_vigente`: rutina vieja con 100 kg y vigente con 60 → 60.
  - `test_aislamiento_entre_gimnasios`: gimnasio B con los mismos ejercicios y nombres no aparece ni suma al A.
  - `test_nombre_con_inicial`: «Lucía Martínez» → `"Lucía M."`; apellido vacío → solo el nombre; nunca el apellido completo.
  - `test_tu_fila_fuera_del_top`: 12 alumnos, el alumno consultado es el 12.º → `tu_fila.puesto == 12`, `es_propia`; si está dentro del top, `tu_fila is None` y su fila trae `es_propia=True`.
  - `test_alumno_dado_de_baja_no_tiene_tu_fila`.
  - `test_cantidad_de_queries_constante`: `assertNumQueries(2)` (o el medido) con 5 alumnos/3 ejercicios y el mismo número con 40 alumnos/12 ejercicios.
  - `test_dia_local_de_la_vigencia`: con reloj congelado, una rutina que arranca «mañana» en UTC-3 no cuenta (`timezone.localdate()`).
- [ ] **Step 3: correr** → fallan.
- [ ] **Step 4: implementar `calcular_ranking`**:
  1. Query 1 = `participantes(gimnasio)` (función pública, devuelve queryset de `RutinaAsignada` vigentes): `RutinaAsignada.objects.for_gimnasio(gimnasio).filter(activa=True, fecha_inicio__lte=hoy, fecha_inicio__gte=hoy - timedelta(days=SEMANAS_POR_CICLO*7 + DIAS_GRACIA_RANKING), alumno__estado=ACTIVO, alumno__aparece_en_ranking=True, alumno__perfil__terminos_version__gte=VERSION_TERMINOS)` con `~Exists(` otra rutina activa del mismo alumno, ya empezada, que gane `(-fecha_inicio, -id)` `)` — misma forma que `RutinaAsignada.por_vencer_de` (SQL, nunca `fecha + columna`); luego `values_list("id", "alumno_id", "alumno__nombre", "alumno__apellido")`.
  2. Query 2 (items): `RutinaAsignadaItem.objects.filter(rutina_asignada_id__in=ids).exclude(kilos="").order_by().values_list("rutina_asignada_id", "ejercicio_nombre_snapshot", "kilos").distinct()`. El `order_by()` vacío es obligatorio: el `Meta.ordering = ["semana","dia","orden"]` se agregaría al `SELECT DISTINCT` y no colapsaría nada. El `distinct` reduce decenas de miles de filas (mismo ejercicio/kilos repetido por día y semana) a unas pocas por alumno. Test: con 30 items idénticos de un alumno el resultado se calcula igual y `assertNumQueries` no cambia.
  3. En Python: parsear, agrupar `(clave normalizada) → {alumno_id: max}` y `Counter` de nombres; elegir top por participantes; ordenar filas por kilos desc, ranking por competencia (1,1,3); recortar a `TOP_POR_EJERCICIO`; `tu_fila` si corresponde; nombre = `f"{nombre} {apellido[:1].upper()}."`.
  4. Si el gimnasio tiene la bandera apagada devolver `[]` (la vista ya da 404; defensa en profundidad).
  Comentario de módulo: por qué sin cache (ruling 1) y por qué se repite en Python la regla de `vigente_de` (si se toca una, tocar la otra).
- [ ] **Step 5:** tests pasan; `python manage.py check`.
- [ ] **Step 6: commit** `feat(reconocimientos): cálculo del ranking de kilos`.

---

### Task 3: Pantalla «Ranking» y navegación

**Files:** Create `reconocimientos/views.py`, `reconocimientos/urls.py`, `templates/reconocimientos/ranking.html`; Modify `config/urls.py` (`path("ranking/", include("reconocimientos.urls"))`), `templates/partials/nav_alumno.html`; tests en `reconocimientos/tests.py`.

**Interfaces:**
- Consumes: `calcular_ranking`.
- Produces: URL name `reconocimientos:ranking` (`/ranking/`), `RankingView(AlumnoRequiredMixin, TemplateView)`.

- [ ] **Step 1: tests de vista** (`TestCase` con `Client`):
  - alumno con bandera → 200, muestra «Sentadilla», «Lucía M.», la leyenda «según la rutina que le asignó el gimnasio», y NO contiene el apellido completo.
  - bandera apagada → 404; anónimo → redirect a login; staff → 403.
  - alumno sin ficha → 200 con estado vacío (sin 500).
  - sin ejercicios con suficientes participantes → mensaje «Todavía no hay suficientes alumnos con kilos cargados para armar el ranking».
  - alumno del gimnasio B no ve nombres del A.
  - la fila propia lleva `aria-current` / texto «Vos»; con «tu puesto» fuera del top aparece su puesto.
  - suplantando (staff entra como alumno): GET → 200 (solo lectura).
  - nav: con bandera aparece el link «Ranking» (`aria-current="page"` en la pantalla); sin bandera no aparece.
  - `assertNumQueries` acotado a la vista (constante con más datos).
- [ ] **Step 2: correr** → fallan.
- [ ] **Step 3: implementar**: `RankingView` — `_exigir_bandera` en `get` (no en `dispatch`, igual que `MiPerfilView`, por el anónimo), contexto `bloques`, `alumno`, `participa` (si el alumno está en el universo). Template extiende `base.html`, `{% block main_class %}contenido--alumno{% endblock %}`; un `<section class="tarjeta">` por ejercicio con `<ol>` (puesto, nombre, «80 kg»; la propia con «Vos»), leyenda de qué significa el dato y que el alumno puede dejar de aparecer desde «Mi perfil» (link a `alumnos:mi_perfil`), estado vacío. Formato de kilos: sin ceros de más (`80`, `20,5`) con coma decimal. Ítem «Ranking» en `nav_alumno.html` tras «Turnos», dentro de `{% if user.perfil.gimnasio.reconocimientos_habilitado %}`. Sin JS ni `hx-boost="false"` (solo links).
- [ ] **Step 4:** tests pasan.
- [ ] **Step 5: commit** `feat(reconocimientos): pantalla de ranking del alumno`.

---

### Task 4: Documentación y cierre

**Files:** Modify `ARQUITECTURA.md`, `ISSUES.md`, `CLAUDE.md` (fila de la tabla), `tenants/terminos.html`/`privacidad.html` solo si el texto describe algo distinto de lo construido.

- [ ] **Step 1:** ampliar la sección «Reconocimientos (ranking y premios)» de `ARQUITECTURA.md`: universo, regla de kilos, por qué sin cache, mínimo de 3, que el ranking es de kilos prescriptos, la app sin modelos, la bandera sigue fuera de «Mi gimnasio» y por qué.
- [ ] **Step 2:** entrada en `ISSUES.md` (decisión de no cachear; parser: `1.500`→1,5 por diseño, tope 500 kg; pendiente: restaurar el checkbox al aprobar los textos legales).
- [ ] **Step 3:** verificar que el texto de términos/privacidad no promete nada que el ranking no hace (mostrar foto en el ranking: hoy NO se muestra → ajustar la frase si lo dice) y mantener `VERSION_TERMINOS = 1` salvo que cambie el sentido (si se edita el sentido, subirla).
- [ ] **Step 4:** `git status -sb` sin untracked olvidados (`reconocimientos/`, templates, tests); suite completa `python manage.py test` → 3928 + nuevos, 0 fallos; `makemigrations --check` sin cambios.
- [ ] **Step 5: commit** `docs(reconocimientos): pieza 2`.

## Verificación end-to-end
- Tests por tarea + suite completa verde sobre la rama.
- QA en navegador (entorno `seed_qa.py` + R2 vacío, bandera prendida): sembrar 4 alumnos con rutinas y kilos variados («20 kg», «20,5», «corporal»); ver Ranking como `ana` en claro/oscuro y celular 390 px; dar de baja a `beto` desde «Mi perfil» y comprobar que desaparece al recargar; comprobar que el staff recibe 403.
- Revisar que ningún HTML del ranking contiene apellidos completos (grep del HTML renderizado).

## Segunda pasada (no-regresión / armonía / omisiones) — verificado contra el código
- **Cero migraciones y cero cambios a modelos**: el deploy en vivo no se toca. Se verifica con `makemigrations --check`.
- **Archivos existentes que se tocan, y solo con líneas aditivas**: `config/settings.py` (+1 app), `config/urls.py` (+1 ruta), `templates/partials/nav_alumno.html` (+1 ítem tras la bandera). Ningún test existente depende del largo del nav; los de `tenants/tests_nav_alumno.py` se corren completos.
- **Middlewares**: una app nueva no está en `APPS_SIN_BLOQUEO` de `plataforma/middleware.py`, así que un alumno de gimnasio congelado ve el cartel (lo correcto), y el middleware de términos la frena hasta aceptar. Un test lo fija con `/ranking/`.
- **`kilos` es texto libre con usos reales** (`rutinas/forms.py`: el staff escribe «20kg», «3 min», «corporal»): el parser descarta esos valores sin romper; no se toca el formulario ni se valida más estricto (rompería a los gimnasios que ya cargan así).
- **`1.500` se lee 1,5 y `15kg c/u` cuenta 15**: aceptado a propósito (tope 500 kg, sin multiplicar por mancuernas); anotado en `ISSUES.md`.
- **CSS**: se reutilizan clases existentes (`tarjeta`, `texto-suave`, `tabla`, `badge`). Si hiciera falta una clase nueva (p. ej. fila propia resaltada) va en `styles/input.css` con tokens semánticos del modo oscuro y `npm run build:css` ANTES de commitear (el service worker cachea `/static/`: verificar en el navegador con recarga forzada).
- **Pieza 1 sin mergear**: esta rama se apila sobre `worktree-reconocimientos-pieza-1`. El PR de la pieza 2 se abre contra esa rama, o contra `main` cuando la 1 se mergee (si se squashea, rebase `--onto main`). No se toca ningún archivo de la pieza 1 aparte de los tres aditivos de arriba.
- **Demo / exportador / `vaciar_gimnasio`**: sin modelo nuevo no hay nada que agregar; el test `_ensuciar` no cambia. La demo tiene la bandera apagada: el ítem de nav no aparece y el ranking da 404.
- **Privacidad**: el ranking sólo toca datos que el alumno ya aceptó compartir (términos + `aparece_en_ranking`); el texto de términos dice «nombre e inicial, foto si la cargaste, lugar en el ranking». Como el ranking no muestra foto en esta pieza, la frase sigue siendo cierta (muestra de menos, no de más): no se sube `VERSION_TERMINOS`.
- **Omisión que se cerró**: un alumno al que le vence el plan y nadie renueva ya no queda en el ranking para siempre (regla de plan abandonado). Eso lo pidió el usuario («dejan de participar en cualquier ranking»).

## Qué NO se puede romper
| Área | Riesgo | Cómo se cuida |
|---|---|---|
| Privacidad del ranking | Mostrar a quien se dio de baja / apellido completo | Sin cache; filtro en la query; tests de baja inmediata y de inicial |
| Portal del alumno | Nav o middleware de términos | Ítem solo con bandera; tests de nav existentes siguen verdes |
| Rendimiento | Muchos items por alumno | 2 queries fijas; test de escala |
| Pieza 1 (PR #4) | Esta rama la apila | No se tocan archivos de pieza 1 salvo `nav_alumno.html`, `settings.py`, `config/urls.py` |

## Abierto
- Revisión legal de términos/privacidad (bloquea prender la bandera en producción y restaurar el checkbox).
- Si el dueño quiere mostrar la foto en el ranking: sería una pieza aparte (hay que decidir qué pasa con la foto de un alumno inactivo).
