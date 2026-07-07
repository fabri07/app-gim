# Plan de implementación — Parte B: aviso proactivo vía Novedad personal

Spec: `docs/superpowers/specs/2026-07-07-aviso-alumno-novedad-personal-design.md`.
Se implementa con TDD (tests primero) en cada tarea.

## Tarea 1 — Modelo `Novedad` personal
- Test (`novedades/tests.py`): aislamiento de novedad personal (gym A vs. otro
  alumno de A vs. gym B); `NovedadQuerySet.para_alumno()` (broadcasts + propias,
  excluye ajenas); `Novedad.clean()` rechaza alumno de otro gimnasio.
- Código: FK `alumno` nullable + `related_name="novedades_personales"`;
  `NovedadQuerySet.para_alumno(alumno)`; `Novedad.clean()` con
  `validar_gimnasio_de`.
- Migración `0003_novedad_alumno`.

## Tarea 2 — Reconciliación genera novedades
- Test (`turnos/tests.py`): tras migrar → existe Novedad personal "Cambió el
  horario…" con la hora nueva y `visible_hasta = fecha`; tras cancelar →
  "Se canceló…"; alumno sin `Perfil` → migra/cancela sin crear Novedad;
  conteos `migradas`/`canceladas` sin cambios (regresión).
- Código: `EventoReconciliacion`, `eventos` en `ResultadoReconciliacion`,
  acumular en el loop, `_generar_novedades_personales` con `full_clean()`.

## Tarea 3 — Scoping de vistas por audiencia
- Test (`novedades/tests.py`, `tenants/tests.py`): portal muestra la personal al
  afectado y no a otro; `NovedadMarcarLeidaView` marca la propia y da 404 en la
  ajena; `NovedadListView` no lista personales ni las cuenta en "X/Y".
- Código: `_portal_alumno` (2 querysets) + `_metricas_dashboard`;
  `NovedadListView.get_queryset`/`ids_visibles`; `NovedadMarcarLeidaView`.

## Tarea 4 — Verificación + commit
- `python manage.py makemigrations novedades` (solo 0003), `test -v 2` verde,
  prueba manual runserver, commit en `aviso-alumno-novedad-personal`.
