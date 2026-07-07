# Aviso proactivo al alumno vía Novedad personal (Parte B)

## Contexto

Es la **Parte B** del trabajo de "migración automática de reservas
desencajadas" (ver el spec de la Parte A,
`2026-07-06-migracion-reservas-desencajadas-design.md`). La Parte A
(`reconciliar_reservas_desencajadas`, `turnos/services.py`) ya está
implementada: cuando el staff cambia la grilla de horarios, cada `Reserva`
futura desencajada se **migra** a la franja más cercana del mismo día o se
**cancela** si no hay alternativa. Hoy el alumno solo se entera al volver a
mirar "Mis Turnos", y el staff ve un mensaje agregado ("Se reprogramaron N
reservas").

B cierra ese hueco: cada reserva migrada o cancelada genera una `Novedad`
**personal** (dirigida a ese alumno) que aparece en su portal con el badge
"Nueva" y su "visto" (reusa `NovedadLeida`, ya existente). Hoy `Novedad` es
siempre un broadcast a todo el gimnasio; se le agrega un destinatario opcional.

**Base:** B se construye stackeada sobre la rama de A (`migracion-reservas-
desencajadas`), que aún no está en `main`. Se rebasa a `main` cuando A mergee.

**Fuera de alcance:** la Parte C (Google Calendar, OAuth real) es su propio
spec posterior. B no toca Calendar.

## Decisiones de producto

1. Una novedad personal **por reserva afectada** (no un resumen agregado).
2. Se avisa **tanto la migración como la cancelación** (dos textos distintos).
3. Alumno **sin login** (`Alumno.perfil is None`): la reserva se migra/cancela
   igual, pero NO se crea Novedad (no tiene portal donde verla). Best-effort;
   el staff igual la ve en su conteo agregado.
4. `visible_hasta` de la novedad = **la fecha de la reserva afectada** (el
   aviso se autovence pasada esa fecha).
5. El staff **no** crea novedades personales desde la UI: son exclusivamente
   programáticas (generadas por la reconciliación). El listado de gestión y su
   conteo "X/Y leído" siguen siendo solo de broadcasts.

## Diseño

### 1. Modelo — `novedades/models.py`

- `Novedad`: FK opcional
  `alumno = FK("alumnos.Alumno", null=True, blank=True, on_delete=CASCADE,
  related_name="novedades_personales")`.
  - `alumno IS NULL` → broadcast al gimnasio (comportamiento actual).
  - `alumno` seteado → personal, solo la ve ese alumno.
  - `Novedad.clean()`: si `alumno` está seteado, validar
    `validar_gimnasio_de(self.gimnasio, alumno=self.alumno)` (mismo helper que
    ya usa `NovedadLeida.clean()`).
- `NovedadQuerySet.para_alumno(alumno)` →
  `.filter(Q(alumno__isnull=True) | Q(alumno=alumno))`. `visibles()` sin cambios.

### 2. Migración

`novedades/migrations/0003_novedad_alumno.py` — agrega el FK nullable.

### 3. Resultado de la reconciliación — `turnos/services.py`

Se extiende para exponer el detalle por reserva que B necesita:

```python
@dataclass(frozen=True)
class EventoReconciliacion:
    alumno: "Alumno"
    fecha: date
    hora_original: time
    hora_nueva: time | None   # None => cancelada; seteada => migrada

@dataclass(frozen=True)
class ResultadoReconciliacion:
    migradas: int
    canceladas: int
    eventos: tuple[EventoReconciliacion, ...] = ()
```

`migradas`/`canceladas` se mantienen → el caller del staff (`_reconciliar`) no
cambia.

### 4. Generar las Novedades — `turnos/services.py`

Dentro del mismo `transaction.atomic()` de `reconciliar_reservas_desencajadas`:
el loop acumula `eventos` y al final llama a `_generar_novedades_personales(
gimnasio, eventos)`. Por cada evento **solo si `evento.alumno.perfil_id is not
None`**, se crea una `Novedad(gimnasio, alumno, titulo, mensaje,
fecha_publicacion=hoy, visible_hasta=evento.fecha, activa=True)`, llamando
`full_clean()` antes de `save()` (porque `create()`/`save()` no invocan
`clean()`, y el helper es la única vía de creación de personales). Textos:

- **Migrada:** titulo `"Cambió el horario de tu turno"`; mensaje
  `f"Tu turno del {fecha:%d/%m} se movió de las {hora_original:%H:%M} a las
  {hora_nueva:%H:%M} porque el gimnasio actualizó su grilla de horarios."`
- **Cancelada:** titulo `"Se canceló uno de tus turnos"`; mensaje
  `f"Tu turno del {fecha:%d/%m} a las {hora_original:%H:%M} se canceló porque
  ya no hay un horario compatible en la nueva grilla. Podés reservar otro desde
  'Reservar turno'."`

Import de `Novedad` local a la función (patrón de imports tardíos del repo).

### 5. Scoping por audiencia — vistas

- `tenants/views.py::HomeView._portal_alumno` (rama con alumno): novedades pasan
  a `.visibles().para_alumno(alumno)[:5]`.
- Rama sin alumno + `_metricas_dashboard`: `.visibles().filter(alumno__isnull=True)[:5]`.
- `novedades/views.py::NovedadListView`: filtrar `alumno__isnull=True` en
  `get_queryset()` **y** en el cómputo de `ids_visibles` (misma audiencia en
  toda la pantalla staff; no ensucia el "X/Y leído").
- `novedades/views.py::NovedadMarcarLeidaView`: lookup con
  `.visibles().para_alumno(self.alumno)` → un alumno no puede marcar leída la
  personal de otro (404).

El template del portal no cambia (las personales entran por el mismo queryset).

### 6. Form

`NovedadForm` no cambia (el staff solo crea broadcasts).

## Tests

- Aislamiento: personal de un alumno del gym A no la ve otro alumno de A ni
  nadie de B.
- `para_alumno`: broadcasts + propias, excluye ajenas.
- Reconciliación genera la novedad correcta (migrada/cancelada, hora,
  `visible_hasta = fecha`).
- Alumno sin `Perfil`: migra/cancela sin crear Novedad.
- Portal: el afectado la ve y la marca leída; no puede marcar la de otro (404).
- Listado staff no contaminado: la personal no aparece ni altera "X/Y leído".
- Regresión A: la suite de reconciliación sigue verde.
