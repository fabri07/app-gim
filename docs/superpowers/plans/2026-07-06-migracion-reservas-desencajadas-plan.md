# Migración automática de reservas desencajadas (Parte A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cuando el staff cambia horarios/duración y una `Reserva` futura deja de encajar en la grilla, el sistema intenta mudarla a la franja más cercana del mismo día en vez de borrarla directamente; solo cancela si no hay ninguna alternativa viable.

**Architecture:** Un solo servicio nuevo (`turnos/services.py::reconciliar_reservas_desencajadas`) reemplaza a `eliminar_reservas_desencajadas`, devolviendo un dataclass con conteos (`migradas`/`canceladas`) en vez de un `int`. El único caller (`turnos/views.py::ReconciliaReservasMixin`) lee ese resultado y emite hasta dos mensajes independientes al staff.

**Tech Stack:** Django 5.2, `TestCase` (sin pytest ni factories), SQLite en tests.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-migracion-reservas-desencajadas-design.md`.
- Estrategia de reubicación: franja de ESE MISMO día con `hora_inicio` a menor distancia absoluta en minutos de la original; empate → la más temprana.
- Sin franja ese día, o la más cercana sin cupo, o el alumno ya con otra reserva ahí → cancelar (comportamiento actual), sin probar una segunda candidata.
- El límite `CIERRE_RESERVA` (1h) NO aplica a esta reubicación.
- Reservas ya pasadas no se tocan.
- Fuera de alcance: cualquier notificación al alumno (`Novedad` personal) y cualquier integración con Google Calendar — son specs separados (B y C).
- Comando para correr los tests de la app: `.venv/bin/python manage.py test turnos -v 2`. Comando para la suite completa antes de dar por cerrado el plan: `.venv/bin/python manage.py test`.
- No renombrar ni tocar `franjas_del_dia`, `vacantes_de_franja`, `es_franja_vigente`, `grilla_semanal` ni `crear_reserva` — se reusan tal cual están.
- **Importante sobre orden de ejecución:** `manage.py test` corre los *system checks* de Django ANTES de cualquier test, incluso pasando un label específico — y esos checks importan `ROOT_URLCONF` → `turnos/urls.py` → `turnos/views.py`. Por eso el rename de la función en `turnos/services.py` y la actualización del import en `turnos/views.py` tienen que quedar aplicados **juntos, antes de correr cualquier `manage.py test`** — no hay forma de validar uno sin el otro a mitad de camino. Esta es la razón por la que este plan es una sola task en vez de dos.

---

### Task 1: Motor de reconciliación (`turnos/services.py`) + actualizar el caller (`turnos/views.py`)

**Files:**
- Modify: `turnos/services.py:367-385` (reemplaza toda la función `eliminar_reservas_desencajadas`) y `turnos/services.py:12-16` (docstring de módulo)
- Modify: `turnos/views.py:39-48` (import de `turnos.services`) y `turnos/views.py:51-70` (clase `ReconciliaReservasMixin`)
- Test: `turnos/tests.py:39-56` (bloque de import de `turnos.services`), `turnos/tests.py:733-833` (reemplaza la clase `EliminarReservasDesencajadasTests`), `turnos/tests.py:1296-1376` (reemplaza la clase `ConfiguracionTurnosReconciliacionTests`)

**Interfaces:**
- Consumes: `franjas_del_dia(gimnasio, dia_semana) -> list[tuple[time, time]]`, `vacantes_de_franja(gimnasio, dia_semana, hora_inicio, default) -> int`, `es_franja_vigente(gimnasio, fecha, hora_inicio) -> bool`, `obtener_configuracion(gimnasio)`, `_ahora_local() -> datetime` (todas ya existen en `turnos/services.py`).
- Produces: `ResultadoReconciliacion` (dataclass frozen con `migradas: int`, `canceladas: int`) y `reconciliar_reservas_desencajadas(gimnasio) -> ResultadoReconciliacion`, exportados desde `turnos/services.py` y consumidos por `turnos/views.py::ReconciliaReservasMixin._reconciliar()` en este mismo task.

- [ ] **Step 1: Reescribir los tests (servicio + vista) para el nuevo comportamiento (RED)**

En `turnos/tests.py`, en el bloque de import de `turnos.services` (línea ~39-56), reemplazar:

```python
from turnos.services import (
    CIERRE_RESERVA,
    Franja,
    ReservaDuplicada,
    TurnoCerrado,
    TurnoInexistente,
    TurnoLleno,
    cancelar_reserva,
    crear_reserva,
    eliminar_reservas_desencajadas,
    es_franja_vigente,
    franjas_de_rango,
    franjas_del_dia,
    grilla_semanal,
    reservas_por_franja,
    url_google_calendar,
    vacantes_de_franja,
)
```

por:

```python
from turnos.services import (
    CIERRE_RESERVA,
    Franja,
    ReservaDuplicada,
    TurnoCerrado,
    TurnoInexistente,
    TurnoLleno,
    cancelar_reserva,
    crear_reserva,
    es_franja_vigente,
    franjas_de_rango,
    franjas_del_dia,
    grilla_semanal,
    reconciliar_reservas_desencajadas,
    reservas_por_franja,
    url_google_calendar,
    vacantes_de_franja,
)
```

Luego reemplazar toda la clase `EliminarReservasDesencajadasTests` (líneas 733-833 actuales, desde `class EliminarReservasDesencajadasTests(TestCase):` hasta la línea en blanco antes de `class UrlGoogleCalendarTests(TestCase):`) por:

```python
class ReconciliarReservasDesencajadasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.config = ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.fecha_pasada = (ahora - timedelta(days=2)).date()

    def test_migra_a_la_franja_mas_cercana_con_lugar(self):
        # 10:00 es franja válida con duración=60; deja de serlo con 45 (600'
        # no es múltiplo de 45'). 9:45 (585') sí lo es y queda a 15' de
        # distancia (vs. 30' de 10:30) -- se reprograma ahí.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        self.assertEqual(resultado.canceladas, 0)
        reserva = Reserva.objects.get(gimnasio=self.gimnasio, fecha=self.fecha_futura)
        self.assertEqual(reserva.hora_inicio, time(9, 45))

    def test_cancela_si_no_queda_ninguna_franja_ese_dia(self):
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancela_si_la_franja_mas_cercana_ya_esta_llena(self):
        self.config.vacantes_default = 1
        self.config.save()
        otro_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Bruno", apellido="Pérez"
        )
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=otro_alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(9, 45),
        )
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva.pk).exists())

    def test_cancela_si_el_alumno_ya_tiene_otra_reserva_en_la_franja_mas_cercana(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(9, 45),
        )
        reserva_desencajada = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 1)
        self.assertFalse(Reserva.objects.filter(pk=reserva_desencajada.pk).exists())
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                alumno=self.alumno,
                fecha=self.fecha_futura,
                hora_inicio=time(9, 45),
            ).exists()
        )

    def test_ante_empate_de_distancia_elige_la_franja_mas_temprana(self):
        # Bajo duracion_minutos=60 (config de setUp), 10:30 nunca es una
        # franja válida (630' no es múltiplo de 60') -- queda exactamente a
        # mitad de camino entre 10:00 (600') y 11:00 (660'), ambas a 30' de
        # distancia.
        reserva = Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 30),
        )

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 1)
        reserva.refresh_from_db()
        self.assertEqual(reserva.hora_inicio, time(10, 0))

    def test_no_toca_reserva_pasada_aunque_quede_desencajada(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_pasada, hora_inicio=time(10, 0)
            ).exists()
        )

    def test_no_toca_reserva_futura_que_sigue_encajando(self):
        # 03:00 = 180', que sigue siendo múltiplo de 45 -> sigue siendo franja.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(3, 0),
        )
        self.config.duracion_minutos = 45
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(3, 0)
            ).exists()
        )

    def test_reducir_el_cupo_no_toca_ninguna_reserva(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )
        self.config.vacantes_default = 1
        self.config.save()

        resultado = reconciliar_reservas_desencajadas(self.gimnasio)

        self.assertEqual(resultado.migradas, 0)
        self.assertEqual(resultado.canceladas, 0)
        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio, fecha=self.fecha_futura, hora_inicio=time(10, 0)
            ).exists()
        )
```

Finalmente, reemplazar toda la clase `ConfiguracionTurnosReconciliacionTests` (líneas 1296-1376 actuales, desde `class ConfiguracionTurnosReconciliacionTests(TestCase):` hasta la línea en blanco antes de `class TurnosAlumnoViewsAccesoTests(TestCase):`) por:

```python
class ConfiguracionTurnosReconciliacionTests(TestCase):
    """Cambiar la configuración (vía la vista, no el service directo) debe
    disparar `reconciliar_reservas_desencajadas` y avisar al staff cuántas
    reservas futuras se reprogramaron y/o cancelaron -- pero nunca tocar
    reservas pasadas."""

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="Gimnasio de Prueba", slug="gimnasio-de-prueba"
        )
        self.staff = User.objects.create_user("staff-1", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        ConfiguracionTurnos.objects.create(
            gimnasio=self.gimnasio, duracion_minutos=60, vacantes_default=5
        )
        for dia in range(7):
            HorarioAtencion.objects.create(
                gimnasio=self.gimnasio,
                dia_semana=dia,
                hora_desde=time(0, 0),
                hora_hasta=time(23, 0),
            )
        ahora = timezone.localtime()
        self.fecha_futura = (ahora + timedelta(days=2)).date()
        self.fecha_pasada = (ahora - timedelta(days=2)).date()
        self.client.login(username="staff-1", password="clave-123456")

    def test_cambiar_duracion_reprograma_reserva_futura_desencajada_y_avisa(self):
        # 10:00 es franja válida con duración=60; deja de serlo con 45
        # (600' no es múltiplo de 45') -- pero 9:45 (585') sí lo es y queda
        # libre, así que se reprograma en vez de cancelarse.
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        reserva = Reserva.objects.get(gimnasio=self.gimnasio, fecha=self.fecha_futura)
        self.assertEqual(reserva.hora_inicio, time(9, 45))
        self.assertContains(
            response,
            "Se reprogramaron 1 reserva(s) futura(s) a un nuevo horario.",
        )

    def test_cambiar_duracion_cancela_si_no_hay_franja_alternativa_y_avisa(self):
        # Sin ningún HorarioAtencion ese día en la config nueva, no hay a
        # dónde reprogramar -- se cancela como antes.
        HorarioAtencion.objects.filter(
            gimnasio=self.gimnasio, dia_semana=self.fecha_futura.weekday()
        ).delete()
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_futura,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        self.assertFalse(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                fecha=self.fecha_futura,
                hora_inicio=time(10, 0),
            ).exists()
        )
        self.assertContains(
            response,
            "Se cancelaron 1 reserva(s) futura(s) que ya no encajan en la nueva grilla.",
        )

    def test_reserva_pasada_no_se_toca_ni_genera_aviso(self):
        Reserva.objects.create(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            fecha=self.fecha_pasada,
            hora_inicio=time(10, 0),
        )

        response = self.client.post(
            reverse("turnos:configuracion"),
            {"duracion_minutos": 45, "vacantes_default": 5},
            follow=True,
        )

        self.assertTrue(
            Reserva.objects.filter(
                gimnasio=self.gimnasio,
                fecha=self.fecha_pasada,
                hora_inicio=time(10, 0),
            ).exists()
        )
        self.assertNotContains(response, "Se cancelaron")
        self.assertNotContains(response, "Se reprogramaron")
```

- [ ] **Step 2: Correr los tests para confirmar que fallan por la razón correcta**

Run: `.venv/bin/python manage.py test turnos.tests.ReconciliarReservasDesencajadasTests turnos.tests.ConfiguracionTurnosReconciliacionTests -v 2`
Expected: falla al arrancar (antes de listar ningún test individual) con
`ImportError: cannot import name 'reconciliar_reservas_desencajadas' from 'turnos.services'`.
Este error viene del import que se acaba de editar en `turnos/tests.py` — en
este punto `turnos/services.py` y `turnos/views.py` todavía NO se tocaron,
así que es el único punto de falla (no hay `ImportError` adicional desde
`turnos/views.py` todavía).

- [ ] **Step 3: Implementar `ResultadoReconciliacion` y `reconciliar_reservas_desencajadas` en `turnos/services.py` (GREEN, parte 1/2)**

Reemplazar la función completa `eliminar_reservas_desencajadas` (líneas 367-385, el final del archivo) por:

```python
@dataclass(frozen=True)
class ResultadoReconciliacion:
    migradas: int
    canceladas: int


def _franja_mas_cercana(franjas: list[tuple[time, time]], hora_original: time) -> time | None:
    """De una lista de franjas del día (ya ordenada ascendente por
    `hora_inicio`, ver `_franjas_de_horarios`), la `hora_inicio` con menor
    distancia absoluta en minutos a `hora_original`. Empate -> la más
    temprana (`min()` es estable y la lista ya viene ordenada). `None` si
    `franjas` está vacía.
    """
    if not franjas:
        return None

    def _minutos(hora: time) -> int:
        return hora.hour * 60 + hora.minute

    objetivo = _minutos(hora_original)
    return min(franjas, key=lambda franja: abs(_minutos(franja[0]) - objetivo))[0]


def reconciliar_reservas_desencajadas(gimnasio) -> ResultadoReconciliacion:
    """Para cada `Reserva` futura del gimnasio cuya `(dia_semana, hora_inicio)`
    ya no aparece en `franjas_del_dia()` con la config vigente (tras un
    cambio de horarios/duración), intenta mudarla a la franja de ese mismo
    día más cercana en horario a la original. Si ese día no queda ninguna
    franja, si la más cercana ya alcanzó su cupo, o si el alumno ya tiene
    otra reserva exactamente en esa franja/fecha, se cancela (se borra) --
    igual que el comportamiento anterior. Las reservas ya pasadas no se
    tocan (quedan como historial). Devuelve cuántas se migraron y cuántas
    se cancelaron.

    El límite de `CIERRE_RESERVA` NO aplica acá: es el sistema preservando
    una reserva que ya existía, no una reserva nueva.
    """
    config = obtener_configuracion(gimnasio)
    ahora = _ahora_local()
    migradas = 0
    canceladas = 0

    with transaction.atomic():
        for reserva in Reserva.objects.for_gimnasio(gimnasio):
            inicio = datetime.combine(reserva.fecha, reserva.hora_inicio)
            if inicio < ahora:
                continue
            if es_franja_vigente(gimnasio, reserva.fecha, reserva.hora_inicio):
                continue

            dia_semana = reserva.fecha.weekday()
            franjas = franjas_del_dia(gimnasio, dia_semana)
            nueva_hora = _franja_mas_cercana(franjas, reserva.hora_inicio)

            if nueva_hora is not None:
                vacantes = vacantes_de_franja(
                    gimnasio, dia_semana, nueva_hora, config.vacantes_default
                )
                ocupadas = (
                    Reserva.objects.for_gimnasio(gimnasio)
                    .filter(fecha=reserva.fecha, hora_inicio=nueva_hora)
                    .exclude(pk=reserva.pk)
                    .count()
                )
                ya_tiene_esa = (
                    Reserva.objects.for_gimnasio(gimnasio)
                    .filter(
                        fecha=reserva.fecha, hora_inicio=nueva_hora, alumno=reserva.alumno
                    )
                    .exclude(pk=reserva.pk)
                    .exists()
                )
                if ocupadas < vacantes and not ya_tiene_esa:
                    reserva.hora_inicio = nueva_hora
                    reserva.save(update_fields=["hora_inicio"])
                    migradas += 1
                    continue

            reserva.delete()
            canceladas += 1

    return ResultadoReconciliacion(migradas=migradas, canceladas=canceladas)
```

También actualizar el docstring de módulo (líneas 12-16 de `turnos/services.py`), cambiando:

```python
Este módulo no valida permisos ni resuelve el gimnasio del request -- eso es
responsabilidad de las vistas (Task 4). Acá solo vive la lógica de negocio:
cortar horarios de atención en franjas de turno, calcular cupos, crear y
cancelar reservas, y limpiar reservas que quedaron "desencajadas" tras un
cambio de configuración.
```

por:

```python
Este módulo no valida permisos ni resuelve el gimnasio del request -- eso es
responsabilidad de las vistas (Task 4). Acá solo vive la lógica de negocio:
cortar horarios de atención en franjas de turno, calcular cupos, crear y
cancelar reservas, y reconciliar reservas que quedaron "desencajadas" tras
un cambio de configuración (reubicándolas si es posible, cancelándolas si
no).
```

**No correr `manage.py test` todavía** — `turnos/views.py` sigue importando
el nombre viejo en este punto; el siguiente step lo arregla antes de
verificar nada.

- [ ] **Step 4: Actualizar `ReconciliaReservasMixin` en `turnos/views.py` (GREEN, parte 2/2)**

Reemplazar el import de `turnos.services` (líneas ~39-48):

```python
from turnos.services import (
    ErrorDeReserva,
    TurnoCerrado,
    cancelar_reserva,
    crear_reserva,
    eliminar_reservas_desencajadas,
    grilla_semanal,
    reservas_por_franja,
    url_google_calendar,
)
```

por:

```python
from turnos.services import (
    ErrorDeReserva,
    TurnoCerrado,
    cancelar_reserva,
    crear_reserva,
    grilla_semanal,
    reconciliar_reservas_desencajadas,
    reservas_por_franja,
    url_google_calendar,
)
```

Y reemplazar la clase `ReconciliaReservasMixin` completa por:

```python
class ReconciliaReservasMixin:
    """Reubica o cancela las reservas futuras que quedaron "desencajadas"
    tras un cambio de horarios/duración (`reconciliar_reservas_desencajadas`)
    y avisa al staff. Cada mensaje SOLO aparece si de verdad hubo alguna
    migración/cancelación -- no ensuciar la pantalla con un aviso vacío en
    el caso común de que la grilla nueva siga cubriendo todas las reservas
    existentes.

    Requiere que la vista que lo use exponga `self.gimnasio` (lo da
    `TenantScopedMixin`, o -- para `ConfiguracionTurnosView`, que no lleva
    ese mixin -- una property propia)."""

    def _reconciliar(self):
        resultado = reconciliar_reservas_desencajadas(self.gimnasio)
        if resultado.migradas > 0:
            messages.info(
                self.request,
                f"Se reprogramaron {resultado.migradas} reserva(s) futura(s) a un nuevo horario.",
            )
        if resultado.canceladas > 0:
            messages.warning(
                self.request,
                f"Se cancelaron {resultado.canceladas} reserva(s) futura(s) que ya no encajan en la nueva grilla.",
            )
```

- [ ] **Step 5: Correr los tests nuevos/reescritos para confirmar que pasan**

Run: `.venv/bin/python manage.py test turnos.tests.ReconciliarReservasDesencajadasTests turnos.tests.ConfiguracionTurnosReconciliacionTests -v 2`
Expected: 11 tests (8 + 3), `OK`.

- [ ] **Step 6: Correr toda la app `turnos`**

Run: `.venv/bin/python manage.py test turnos -v 1`
Expected: `OK`, sin fallas.

- [ ] **Step 7: Correr la suite completa del proyecto**

Run: `.venv/bin/python manage.py test`
Expected: `OK` (240 tests previos + los nuevos/reescritos de este plan, sin regresiones en otras apps).

- [ ] **Step 8: Commit**

```bash
git add turnos/services.py turnos/views.py turnos/tests.py
git commit -m "feat(turnos): reubicar reservas desencajadas en vez de solo cancelarlas"
```
