# Ranking de pesos + asistencia perfecta con premios (portal del alumno)

> Versión 2: reescrita tras una pasada adversarial contra el código (3 auditorías: cuotas, asistencia, perfil/ranking/reglas del repo). La v1 tenía errores de fondo; los corregidos están marcados con **[corr.]**.

## Context
El dueño del producto quiere motivar a los alumnos desde su portal:
1. **Ranking por ejercicio**: top 10 ejercicios más usados de la biblioteca de cada gimnasio; quién levanta más peso. Lo ven todos los alumnos del gimnasio (nombre + inicial del apellido).
2. **Premio por asistencia perfecta** en el ciclo de 28 días. Premio: descuento en la cuota, obsequio del dueño, o insignia visible para todos en el perfil.

## Decisiones del usuario (firmes)
- El peso lo fija el STAFF (`RutinaAsignadaItem.kilos`); el alumno no registra pesos. El ranking es de **kilos prescriptos**; la pantalla debe decirlo («según la rutina que le asignó el gimnasio»).
- Asistencia por niveles según lo que tenga el gimnasio: lector de ingreso; turnos con asistencia marcada por el staff; carga manual del staff.
- La frecuencia semanal la define el staff al armar la rutina del alumno (2, 3, 7 días…).
- T&C: se aceptan una vez al abrir el portal; no se vuelven a mostrar. Es el consentimiento para ranking y perfil visible.
- Foto de perfil del alumno: se agrega.
- Descuento: campo propio en `Cuota` (monto a cobrar = `monto - descuento`).
- **Tope de descuento 90%**: el 100% se resuelve anulando la cuota (acción existente).
- **El premio exige no tener deuda al cierre del ciclo.**
- **Asistencia perfecta = todas las sesiones, con tolerancia configurable por gimnasio** (default 0 faltas; ej. 1 para cubrir feriados).
- **Peso del ranking = máximo de las 4 semanas de la rutina vigente del alumno.**

## Hallazgos que corrigen la v1 (verificados en el código)
- **[corr.] `RutinaAsignada` NO tiene `dias_por_semana`** (solo la plantilla, y no se copia). La meta se deriva de `rutinas/progreso.py::sesiones_previstas` (pares (día, semana) con ejercicios) de la rutina vigente, no de `dias×4`.
- **[corr.] `Reserva` no sirve como asistencia**: no tiene estado, y cancelar o reconciliar BORRA la fila (`turnos/services.py:487-497`, `:552`). El nivel «turnos» necesita un modelo nuevo de asistencia.
- **[corr.] El toggle «día entrenado» del alumno no cuenta**: es autodeclarado, se puede marcar días después (`RutinaAsignadaDiaCompletado.completado_en` es cuándo tocó el botón). Con plata en juego queda fuera.
- **[corr.] «Tiene lector» no se deduce de `ConfiguracionIngreso`** (`get_or_create` la crea para cualquiera). Y casi seguro ningún gimnasio tiene lector hoy (`CLAUDE.md`: «sin hardware todavía»).
- **[corr.] Antipassback es solo una ventana de 2 min**: puede haber varios `PERMITIDO/ENTRADA` por día → contar DÍAS locales distintos, no filas (`timezone.localtime(momento).date()`). Eventos en diferido llegan hasta 7 días tarde.
- **[corr.] Varias `RutinaAsignada` con `activa=True` por alumno es lo normal**; la vigente se resuelve con `RutinaAsignada.vigente_de` (`rutinas/models.py:280`; hay versión anotada en bloque ~`:349-385`).
- **[corr.] Ciclo de pago ≠ semanas de rutina** (`fecha_inicio_ciclo` vs `RutinaAsignada.fecha_inicio`); y mover el ancla cambia el rango de ciclos pasados → hay que **persistir rango y resultado** del premio.
- **[corr.] `cobro_mp.py:257` escribe `monto=intento.monto` al acreditar**: con `intento.monto` neto, el descuento se aplicaría dos veces y `ingresos_por_mes` subcontaría. Es el cambio más delicado del proyecto.
- **[corr.] `kilos` es texto libre sin normalizar** (hay `"20"`, `"20.0"`, `"20,5"`, `"20 kg"` (la demo), `"20KG"`, `"15kg c/u"`, `"corporal"`, `"60s"`, `"10-12kg"`, `"50%"`, `""`). No existe parser reutilizable (`RE_KILOS` de `importaciones/parsing/pdf.py:176` es privada, anclada y no sirve).
- **[corr.] `Ejercicio` no tiene `nombre_normalizado`** y los snapshots solo hacen `.strip()` → agrupar con `normalizar_texto` (`importaciones/parsing/comun.py:68`).
- **[corr.] Suplantación**: si el staff entra como el alumno, no debe poder aceptar los T&C por él.
- **[corr.] La foto va por una vista autenticada del mismo gimnasio**, no pública: es la cara de una persona (patrón `tienda/views.py::FotoProductoView` modo privado `no-store`, y `tienda/fotos.py::preparar_foto` que corrige EXIF, achica y re-codifica a JPEG con nombre uuid).

## Anti-objetivos / fuera de alcance
Ranking de todos los ejercicios; ranking de lo realmente levantado; por peso corporal; débito automático; registrar pesos del alumno; que un descuento se aplique sin que nadie lo vea; premiar con asistencia autodeclarada.

## Orden de construcción (5 piezas, una rama/worktree cada una, todo detrás de bandera apagada)
Bandera: `Gimnasio.reconocimientos_habilitado` (decide el dueño: va en `GimnasioForm.Meta.fields` + `templates/tenants/gimnasio_form.html` con su línea de error; `default=False, db_default=False`; migración `tenants/0017`). Ranking, insignias y premios se ven solo con la bandera prendida.

### Pieza 1 — Perfil del alumno, T&C y foto
- `tenants.Perfil`: `terminos_aceptados_en` (null) y `terminos_version` (int). Constante `VERSION_TERMINOS`; solo se vuelve a pedir si se sube la versión.
- Middleware nuevo para rol alumno, **copiando el patrón de `plataforma/middleware.py`** (`process_view`, allowlist `URLS_SIN_BLOQUEO` evaluada antes de leer `request.user`, incluye login/logout/`politica_privacidad`/logo; responde **200 con cartel** en GET y redirige en POST, nunca 4xx por `hx-boost`; `add_never_cache_headers`). Va DESPUÉS de `PlataformaMiddleware` (la cuenta bloqueada ve primero su cartel). **Con suplantación activa (`suplantacion.esta_activa`) ni exige ni permite aceptar.**
- Texto de T&C: lo redacta el dueño; yo dejo borrador. Actualizar `templates/tenants/privacidad.html` (foto, ranking, premios) y su fecha de «Última actualización».
- `alumnos.Alumno.foto` (ImageField, `preparar_foto`, nombre uuid, **private**) + vista de servicio autenticada del mismo gimnasio. Form con `hx-boost="false"` (sube archivo). Reemplazar o borrar la foto borra el archivo viejo de R2; también al borrar el alumno y en `vaciar_gimnasio`.
- `Alumno.aparece_en_ranking` (bool, default True tras aceptar T&C; opt-out desde «Mi perfil»).
- Pantalla «Mi perfil» + ítem en `templates/partials/nav_alumno.html`.
- Demo: sembrar alumnos demo con T&C aceptados (`tenants/demo.py`; `restaurar_demo` los recrea cada 6 h).

### Pieza 2 — Ranking
- App nueva **`reconocimientos/`** (registrar en `INSTALLED_APPS` tras `ingreso`, con comentario de qué lee; `config/urls.py`; ítem «Ranking» en el nav; imports tardíos hacia `alumnos`/`rutinas`/`tenants`).
- `rutinas/kilos.py` (parser nuevo, con tests): acepta `20`, `20.0`, `20,5`, `20 kg`, `20KG`, `15kg c/u` (cuenta el número tal cual), rango `10-12kg` (máximo); descarta `corporal`, `%`, tiempos (`60s`, `2 min`), vacío. Comparar con `Decimal`.
- Cálculo (módulo de lectura, **una query agregada, sin N+1**): universo = alumnos `ACTIVO` con rutina vigente (la anotada en bloque), con T&C aceptados y `aparece_en_ranking`. Top 10 ejercicios = los presentes en más rutinas vigentes distintas, agrupados por `normalizar_texto(ejercicio_nombre_snapshot)`. Peso por alumno = máximo parseable de las 4 semanas. Empates comparten puesto. Cada alumno cuenta una vez por ejercicio. Cache corto (LocMem, ~10 min) si hace falta.
- Pantalla: un bloque por ejercicio con top N + «tu puesto»; nombre + inicial («Lucía M.»). Mínimo de participantes por ejercicio (ej. 3) para mostrarlo, así no se expone un alumno solo.
- Solo lectura: **sin modelo nuevo, sin los 3 lugares de `TenantOwnedModel`.**

### Pieza 3 — Descuento en la cuota (independiente, se puede mergear sola)
- `pagos.Cuota`: `descuento` (Decimal, default 0), `motivo` (CharField corto, blank), propiedad **`monto_a_cobrar`** (única fuente del neto) y anotación reutilizable `F("monto") - F("descuento")`. `CheckConstraint`: `descuento >= 0` y `descuento * 10 <= monto * 9` (tope 90%). Migración reversible (`db_default`, como `CobroOnlineCuota`); antes verificar que no haya montos negativos cargados (`ConfirmarPagoForm` hoy no valida `>= 0`: validarlo).
- Tocar TODOS estos lugares (auditados):
  - `pagos/cobro_mp.py`: `:248` (comparar contra el neto), **`:257` (acreditar: dejar `monto = intento.monto + descuento` o no pisar `monto`; test dedicado)**, `:512-518` (`_reusable`), `:543-544` (`sin_monto` sobre el neto), `:551/562/570`; `:237`/`:613` ya usan el monto congelado del intento (neto).
  - `pagos/cobro.py`: `CAMPOS_CONFIRMAR_PAGO`, `marcar_pagada`, `editar_pagada` (**el descuento NO se edita en cuotas pagadas**), `corregir_monto` ampliado o `corregir_descuento`: todo cambio del neto pasa por `_bloquear` + `cancelar_links_vivos`.
  - `pagos/forms.py` (`CuotaMontoForm` + `clean` tope 90%; `ConfirmarPagoForm` bloquea `descuento` si se cobró por MP), `pagos/views.py:383-418`, `pagos/admin.py`, `pagos/precios.py` (`aplicar_precio_a_impagas_sin_monto`: caso monto 0 con descuento).
  - Lecturas: `tenants/analitica.py:513` (`Sum(F("monto") - F("descuento"))`), `notificaciones/services.py:258-260` (+ chequeo `<= 0`), `tenants/exportacion.py:115-128` (columnas `descuento`, `motivo`, `monto_a_cobrar`).
  - Templates (mostrar neto; con descuento, bruto tachado + motivo): `pagos/pago_list.html:65`, `pago_monto.html`, `comprobante_alumno_form.html:11`, `partials/cuotas_impagas.html:26-30` (condición `> 0` y botón «Pagar $X»), `alumnos/alumno_detail.html:217`, `tenants/home.html:211`.
- Decidir con el dueño si `Cuota.observaciones` (texto libre existente) ya cubre `motivo`; por defecto campo propio.
- Toda llamada a MP lleva `token=` del gimnasio (`pagos/tests_token_del_gimnasio.py` lo vigila).

### Pieza 4 — Asistencia
- Servicio único **`dias_asistidos(alumno, inicio, fin)`** → conjunto de **fechas locales**, **unión** de fuentes (deduplica por día): (a) ingreso: días con `EventoIngreso` `PERMITIDO`+`ENTRADA` (base reutilizable: `ingreso/analitica.py::_ingresos`); (b) `Asistencia` marcada por el staff. **La unión (no «elegir una fuente») evita que un lector caído le quite el premio a todos.** Sin lector, solo cuenta (b).
- Modelo nuevo `Asistencia` (`TenantOwnedModel`: gimnasio, alumno FK CASCADE, fecha, `reserva_hora` opcional, `registrada_por`, `creado`), unique `(gimnasio, alumno, fecha)`. Cubre «turnos + el staff marca» y «carga manual» con el mismo dato. **Tres lugares obligatorios**: `tenants/demo.py::vaciar_gimnasio` (antes de `alumnos.delete()`), `_ensuciar` en `tenants/tests.py`, `HOJAS` en `tenants/exportacion.py`; más test de aislamiento.
- Staff: en `AgendaView` (turnos) un botón «asistió» por reserva del día (crea `Asistencia`), y una pantalla/acción «cargar asistencia» desde el alumno. Solo el staff escribe; `fecha` no puede ser futura; tope de antigüedad razonable.
- **Meta del ciclo** = cantidad de `sesiones_previstas` de la rutina vigente al **inicio del ciclo** (no `dias×4`); sin rutina vigente → el alumno no participa. Alumnos sin `fecha_inicio_ciclo` (importados) o `INACTIVO` quedan fuera.

### Pieza 5 — Premios
- `reconocimientos.PremioAsistencia` (config por gimnasio, una fila): `descuento_tipo` (ninguno/porcentaje/fijo), `descuento_valor` (tope 90% del monto al aplicarlo), `obsequio_texto`, `da_insignia` (bool), `faltas_toleradas` (default 0). Pantalla del dueño en «Mi gimnasio» o propia, con error por campo.
- `reconocimientos.PremioOtorgado` (`TenantOwnedModel`): alumno FK **CASCADE** (con PROTECT bloquearía borrar alumnos hoy borrables), `periodo_inicio`/`periodo_fin`/`meta`/`asistidas` **congelados**, `descuento_aplicado` y `obsequio` congelados, `cuota` FK SET_NULL, estado (`otorgado`/`aplicado`/`entregado`), `insignia_hasta`, `otorgado_por`. Unique `(gimnasio, alumno, periodo_inicio)` (idempotente). **Mismos tres lugares obligatorios**.
- **Cierre y confirmación**: pantalla del staff «Premios del ciclo» lista, para ciclos terminados hace ≥ 7 días y ≤ ~30 (margen por ingresos en diferido), los alumnos con `asistidas >= meta - faltas_toleradas`, **sin cuota impaga/vencida** de ese ciclo ni anteriores. El staff confirma (nada automático con plata). Al confirmar: crea `PremioOtorgado`, avisa al alumno con una `Novedad` personal (`novedades.Novedad` con `alumno`) y, si hay descuento, lo aplica.
- **Aplicar el descuento**: a la primera cuota impaga con `periodo_inicio > periodo_fin` del ciclo premiado. Si todavía no existe (se pre-emite 7 días antes), el premio queda `otorgado` y se aplica al emitirse (hook posterior al `bulk_create` de `generar_pagos_pendientes`, que no pasa por `save()`). Si hay link de MP vivo: se cancela con `cancelar_links_vivos` (el alumno genera otro por el neto).
- **Insignia**: visible para todos los alumnos del gimnasio hasta el fin del ciclo siguiente (`insignia_hasta`, mi propuesta). Como no existe perfil visible, se crea un **mini-perfil de compañero** (foto, nombre + inicial, insignias, puestos en el ranking) al tocar un nombre en el ranking o en «Cuadro de honor» (sección de la pantalla de ranking).

## Qué NO se puede romper
| Área | Riesgo | Cómo se cuida |
|---|---|---|
| Cobro por MP | Descuento doble / link con monto viejo | `monto_a_cobrar` única fuente; `:257` con test; todo cambio de neto cancela links vivos |
| Analítica de ingresos | Subcuenta o infla | `Sum(monto - descuento)`; test con descuento |
| `generar_pagos` | `bulk_create` no llama `save()` | Hook explícito posterior; test de idempotencia |
| Borrado de alumnos | FK PROTECT nuevas bloquean | `on_delete=CASCADE` en modelos nuevos; test de borrado |
| `restaurar_demo` / exportador / aislamiento | Modelo tenant-owned sin los 3 lugares | Checklist por modelo; test `test_ensuciar_cubre_todos…` |
| Bloqueo por deuda | Acceso del alumno | El middleware de T&C va después del de plataforma |
| `hx-boost` | Click mudo / subida de foto | Cartel 200; `hx-boost="false"` en el form con archivo |
| Logo/ícono PWA | `Gimnasio.save()` cambia `modificado` | Escrituras de máquina con `update()`; la bandera por form es inofensiva |
| Privacidad | Dato nuevo sin aviso | T&C + `privacidad.html` actualizados antes de prender la bandera |

## Reglas del repo (CLAUDE.md)
Fechas con `timezone.localdate()`/`localtime`; sin N+1 (test de escala que crezca los alumnos y los ejercicios); `update()` no dispara `auto_now`; cada campo de form con su `{% if form.campo.errors %}`; `{# #}` de una línea; `npm run build:css` si se toca `styles/input.css`; pantallas nuevas con tokens semánticos del modo oscuro; la suite no sale a la red. Al terminar: sección nueva en `ARQUITECTURA.md` + fila en la tabla de `CLAUDE.md`, entrada en `ISSUES.md`, y verificar que no queden archivos nuevos sin trackear.

## Verificación
- Tests por pieza: parser de kilos (todos los formatos listados + límite de 30 caracteres); ranking (empates, baja, inactivo, sin T&C, opt-out, mínimo de participantes, varias rutinas activas, semanas mezcladas parseable/no parseable); T&C (una sola vez, suplantación, demo, allowlist, cartel 200); foto (EXIF, tamaño, acceso cruzado entre gimnasios → 404, borrado del archivo); asistencia (días distintos, día local UTC-3 con reloj congelado, diferido, unión de fuentes, ancla `None`, baja); meta (rutina cambiada a mitad de ciclo, sin rutina, ancla movida); premio (idempotencia, deuda al cierre, tolerancia 0/1, cuota aún no emitida, link MP vivo); descuento (tope 90%, constraint, acreditar por MP con descuento, `MONTO_CORREGIDO` no dispara falso, ingresos, exportación).
- Verificar que cada test de regresión falla sin el fix.
- `python manage.py test` completo (con `.env` real; baseline 1723 OK) y de nuevo sobre el merge, no solo en la rama.
- QA en navegador, celular 390 px: primer ingreso con T&C, foto, ranking, mini-perfil, flujo del staff de premios, cuota con descuento en listado y en el link de MP (en producción nunca se probó un pago real de MP: el primer gimnasio real que cobre es la prueba).

## Abierto (para el builder o el dueño)
- Texto legal de los T&C (lo redacta el dueño).
- Duración de la insignia (propuesta: hasta fin del ciclo siguiente) y de un premio no canjeado.
- Mínimo de participantes por ejercicio para mostrar el ranking (propuesta: 3).
- Pendiente antes de codear: **segunda pasada adversarial** sobre el plan de implementación detallado (no-regresión/armonía/omisiones), según la práctica del proyecto.
