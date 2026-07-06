# Migración automática de reservas desencajadas (Parte A)

## Contexto

Cuando el staff cambia `HorarioAtencion` o la `duracion_minutos` de
`ConfiguracionTurnos`, algunas `Reserva` futuras pueden dejar de corresponder
a ninguna franja de la nueva grilla ("quedan desencajadas"). Hoy
`eliminar_reservas_desencajadas()` (`turnos/services.py`) las borra
directamente, sin avisar al alumno ni intentar reubicarlas — el dueño del
producto pidió que, en vez de perder la reserva, el sistema intente mudarla
automáticamente al horario más parecido de ese mismo día.

Este spec es la **Parte A** de un trabajo más grande en tres partes,
decidido por tamaño/riesgo/dependencias:

- **A (este spec):** motor de migración automática.
- **B (spec separado, después):** aviso proactivo al alumno vía una
  `Novedad` personal (hoy `Novedad` es siempre para todo el gimnasio; se le
  agregaría un `alumno` opcional). Reusa `NovedadLeida` (ya existe) para el
  "visto".
- **C (spec separado, después, mayor riesgo):** integración real con Google
  Calendar (OAuth, guardar credenciales, crear/actualizar/borrar el evento).
  Depende de A y B para saber cuándo actualizar el evento.

A queda scopeado **solo como el motor de migración**: el staff sigue viendo
el resumen que ya existe (`ReconciliaReservasMixin`), y el alumno se entera
al volver a mirar "Mis Turnos". El aviso proactivo es responsabilidad de B.

## Decisiones de producto (ya validadas con el dueño)

1. **Estrategia de reubicación:** la franja de ESE MISMO día cuyo
   `hora_inicio` esté más cerca en minutos de la hora original. Empate → la
   más temprana.
2. **Sin alternativa viable** (no queda ninguna franja ese día, o la más
   cercana ya alcanzó su cupo): se cancela la reserva puntual, igual que el
   comportamiento actual — no se prueba una segunda franja "siguiente más
   cercana".
3. El límite de `CIERRE_RESERVA` (1h antes del turno, que aplica cuando un
   alumno *elige* reservar) **no aplica** a esta reubicación: es el sistema
   preservando una reserva que ya existía, no una reserva nueva.
4. Reservas ya pasadas no se tocan (comportamiento actual, sin cambios).

## Diseño

### Renombre

`eliminar_reservas_desencajadas` → **`reconciliar_reservas_desencajadas`**.
El nombre actual ya no describe la función una vez que puede migrar en vez
de solo borrar; el nuevo nombre además queda consistente con
`ReconciliaReservasMixin`/`_reconciliar()`, que ya existen en
`turnos/views.py` y son quienes la llaman.

### Tipo de retorno

Cambia de `int` a un dataclass nuevo, mismo patrón que `Franja`:

```python
@dataclass(frozen=True)
class ResultadoReconciliacion:
    migradas: int
    canceladas: int
```

### Algoritmo

Por cada `Reserva` futura del gimnasio (recorrida en el orden ya dado por
`Reserva.Meta.ordering = ["fecha", "hora_inicio"]`) cuya
`(dia_semana, hora_inicio)` ya no aparece en `franjas_del_dia()` con la
config vigente:

1. Recalcular `franjas_del_dia(gimnasio, fecha.weekday())`.
2. Si no hay ninguna franja ese día → cancelar (`reserva.delete()`,
   `canceladas += 1`).
3. Si hay: elegir la de `hora_inicio` con menor distancia absoluta en
   minutos a la hora original (`min()` con esa key; como la lista ya viene
   ordenada ascendente, el empate lo resuelve solo a favor de la más
   temprana).
4. Chequear cupo en esa franja nueva para esa fecha: `vacantes_de_franja(...)`
   vs. ocupación real (`count()` excluyendo la propia reserva) **y** que el
   alumno no tenga ya otra reserva en exactamente ese `(fecha, hora_nueva)`
   (evitar chocar contra el `unique_together` de `Reserva`).
5. Si hay lugar y no choca: `reserva.hora_inicio = nueva_hora;
   reserva.save(update_fields=["hora_inicio"])` → `migradas += 1`.
   Si no: cancelar → `canceladas += 1`.

Como cada `count()` de ocupación se hace fresco contra la DB en el momento
de procesar esa reserva, las migraciones ya aplicadas en la misma corrida
quedan reflejadas para las siguientes — sin necesidad de llevar un
diccionario en memoria (este servicio corre una vez por edición de
horario/config de un gimnasio, no es un hot path como `grilla_semanal`; no
se justifica esa optimización acá).

Todo el recorrido va dentro de un único `transaction.atomic()` (si algo
falla a mitad de camino, no deja cambios parciales).

### Caller (`turnos/views.py::ReconciliaReservasMixin`)

`_reconciliar()` pasa a leer el nuevo `ResultadoReconciliacion` y emite hasta
dos mensajes independientes (no uno combinado), cada uno solo si su conteo
es > 0 — sin mensaje si ambos son 0 (comportamiento actual):

- Si `migradas > 0`: `messages.info(request, f"Se reprogramaron {migradas}
  reserva(s) futura(s) a un nuevo horario.")`
- Si `canceladas > 0`: `messages.warning(request, f"Se cancelaron
  {canceladas} reserva(s) futura(s) que ya no encajan en la nueva grilla.")`
  (mensaje actual, sin cambios de texto).

## Impacto en tests existentes

Varios tests de `EliminarReservasDesencajadasTests` y
`ConfiguracionTurnosReconciliacionTests` (`turnos/tests.py`) asumían "toda
reserva desencajada se borra". Con el `HorarioAtencion` 00:00-23:00 y cupo
libre que usan esos `setUp`, sus escenarios en realidad SÍ tienen una franja
cercana con lugar disponible ese mismo día — bajo el nuevo comportamiento
migran en vez de cancelarse. Se reescriben para reflejar el comportamiento
pretendido (migran), y se agregan casos nuevos explícitos para los caminos
de cancelación:

- Sin ninguna franja ese día (horario removido por completo para ese día).
- Franja más cercana llena (cupo ya alcanzado por otros alumnos).
- El alumno ya tiene otra reserva en exactamente esa franja/fecha.
- Empate de distancia entre dos franjas candidatas → gana la más temprana.
- Reserva pasada sigue sin tocarse (sin cambios respecto al comportamiento
  actual).

## Fuera de alcance (anotado para specs futuros)

- **Aviso proactivo al alumno** (Parte B): no se genera ninguna `Novedad`
  ni notificación desde este spec. `ResultadoReconciliacion` hoy solo
  expone conteos agregados (`migradas`/`canceladas`) porque es lo único que
  necesita el consumidor actual (el mensaje al staff). Si B necesita el
  detalle por reserva (alumno, hora vieja, hora nueva) para armar el
  mensaje personalizado, es una decisión a tomar en el brainstorm de B —
  puede implicar extender o reemplazar este tipo de retorno.
- **Integración con Google Calendar** (Parte C): fuera de alcance total de
  este spec.
- No se intenta una "segunda franja candidata" si la más cercana no tiene
  lugar — decisión de producto explícita (ver sección de decisiones).
