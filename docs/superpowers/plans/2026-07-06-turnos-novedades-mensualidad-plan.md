# Turnos y reservas + lectura de novedades + Mensualidad (alias de cobro)

## Contexto

El dueño del gimnasio hoy no tiene forma de organizar quién entrena en qué horario, no sabe
si los alumnos leen los avisos, y el alumno no sabe a dónde transferir la cuota. Tres
features nuevas sobre la base multi-tenant existente (Fases 0–5 completas y deployadas):

- **A. Turnos**: el staff define días/horarios de atención, duración única del turno
  (15–180 min en pasos de 15) y vacantes (cupo default + excepciones por franja). Los
  turnos se **derivan** de esa config (no se persisten "turnos plantilla"). El alumno
  reserva un turno puntual con fecha (semana actual + siguiente), ve la ocupación
  ("8/12"), y puede cancelar. **Las reservas cierran una hora antes del inicio del
  turno.** Cada reserva ofrece un link "Agregar a Google Calendar" (URL de template de
  evento, sin OAuth ni API). Todo editable en configuración cuando el staff quiera.
- **B. Señal de lectura de novedades**: pasivo — el alumno ve badge "Nueva" en las no
  leídas y las marca leídas (POST, sin más interacción); el staff ve "Leída por X/Y" y
  quiénes.
- **C. Mensualidad**: el staff carga alias de transferencia (sin APIs de pago, decisión
  explícita); el alumno los ve junto a su cuota en el portal.

Decisiones de producto confirmadas con el usuario: duración única por gimnasio; cupo
default + excepciones por franja; reserva puntual por fecha (no horario fijo recurrente);
lectura visible para alumno Y staff; reservas cierran 1h antes; link a Google Calendar.

## Global Constraints (aplican a TODAS las tareas)

- Modelos de dominio → `TenantOwnedModel` (`core/models.py`); modelos hijo/join NO
  (patrón `RutinaAsignadaItem`: se scopean vía el padre, no llevan `gimnasio` propio).
- Vistas staff → `StaffRequiredMixin, TenantScopedMixin` (en ese orden, MRO importa).
  Vistas alumno → `AlumnoRequiredMixin` (Task 1).
- Forms con FK a un `TenantOwnedModel` → heredan `TenantScopedModelForm`
  (`core/forms.py`, recibe `gimnasio` como kwarg vía `TenantScopedMixin.get_form_kwargs`).
- Acciones de escritura sin form propio (ocultar, eliminar, marcar) → `View` POST-only +
  `SingleObjectMixin`, patrón `novedades/views.py::NovedadOcultarView`. `http_method_names
  = ["post"]`. GET a esas URLs debe dar 405.
- Templates en `templates/<app>/`, extienden `base.html` (bloques `title`, `main_class`,
  `content`). Reusar clases existentes: `.tarjeta`, `.boton`, `.badge--ok/--alerta/--riesgo`,
  `.tabla`, `.texto-suave`, `.acciones-lista`. CSS nuevo se define con `@apply` en
  `styles/input.css` (NUNCA clases utilitarias sueltas inline) y requiere correr
  `npm run build:css` para que `static/css/app.css` (el que sirve Django) se actualice.
- Cada modelo tenant-owned nuevo lleva un test de aislamiento: `for_gimnasio()` no cruza
  datos entre dos gimnasios (patrón `novedades/tests.py::NovedadTenantIsolationTests`).
- Cada vista de gestión nueva lleva tests: anónimo → redirect a login; alumno en vista
  staff → 403; staff en vista de alumno → 403; objeto de otro gimnasio → 404 (nunca 403 —
  el 404 viene de que el queryset scopeado no lo encuentra).
- Español rioplatense (voseo) en toda la UI: "Todavía no tenés...", "no configuró...".
- `TIME_ZONE = America/Argentina/Buenos_Aires`; usar SIEMPRE `timezone.localtime()` /
  `timezone.localdate()` para "ahora", nunca `datetime.now()` ni `date.today()` naive.
- Framework de tests: `django.test.TestCase` plano (no pytest, no factories). Patrón de
  setUp: crear `Gimnasio` + `User` + `Perfil` (+ `Alumno` si aplica) a mano.
- Cada tarea termina con `python manage.py test` en verde antes de dar el DONE.

---

# Task 1: `AlumnoRequiredMixin`

**Dónde:** `tenants/mixins.py` (junto a `StaffRequiredMixin`, mismo archivo).

**Qué implementar:** un mixin simétrico a `StaffRequiredMixin` para vistas exclusivas del
rol alumno:

```python
class AlumnoRequiredMixin(LoginRequiredMixin):
    """Simétrico a StaffRequiredMixin: 403 si no hay Perfil o el rol no es ALUMNO.

    A diferencia de StaffRequiredMixin, expone `self.alumno` (puede ser None si el
    Perfil de rol alumno todavía no está vinculado a una ficha de Alumno) para que las
    vistas GET puedan renderizar un estado vacío en vez de un 403."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            try:
                self.perfil = request.user.perfil
            except ObjectDoesNotExist:
                raise PermissionDenied("Tu usuario no tiene un Perfil asociado a un Gimnasio.")
            if self.perfil.rol != Perfil.Rol.ALUMNO:
                raise PermissionDenied("Esta sección es solo para alumnos.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def gimnasio(self):
        return self.perfil.gimnasio

    @property
    def alumno(self):
        try:
            return self.perfil.alumno
        except ObjectDoesNotExist:
            return None
```

Reglas de uso que este mixin habilita (documentarlas en el docstring, las vistas futuras
las van a seguir): vistas GET pueden mostrar un estado vacío cuando `self.alumno is None`;
vistas POST de escritura deben hacer `if self.alumno is None: raise PermissionDenied(...)`.

**Imports necesarios en `tenants/mixins.py`:** ya están `LoginRequiredMixin`,
`ObjectDoesNotExist`, `PermissionDenied`, `Perfil` (usados por `StaffRequiredMixin`) —
reusar los mismos, no re-importar.

**Tests (`tenants/tests.py`):** agregar una vista mínima de prueba no es necesario — se
puede testear este mixin indirectamente en tareas posteriores (Task 5, Task 8), pero como
esta tarea debe cerrar en verde por sí sola, agregá un smoke test directo que:
1. Cree un `TestCase` temporal usando una vista existente NO es posible (no hay ninguna
   vista de alumno todavía). En su lugar, escribí el test como una prueba unitaria del
   mixin usando `django.test.RequestFactory` + una `View` mínima definida inline en el
   test (`class _VistaDePrueba(AlumnoRequiredMixin, View): def get(self, request): return
   HttpResponse("ok")`), y verificá: usuario staff → 403; usuario sin Perfil → 403;
   anónimo → redirect (via `as_view()` llamada directamente, no `self.client`, para no
   necesitar urls.py); alumno con Perfil pero sin `Alumno` vinculado → 200 y
   `view.alumno is None` accesible tras `dispatch`.

**Alcance:** SOLO este mixin. No tocules ninguna vista existente ni crees ninguna app
nueva en esta tarea.

---

# Task 2: App `turnos` — scaffold, modelos y migración

**Dónde:** app nueva `turnos/` en la raíz del repo (mismo nivel que `alumnos/`, `pagos/`,
`novedades/`).

**Prerequisito:** Task 1 completa (`AlumnoRequiredMixin` existe en `tenants/mixins.py`,
no se usa en esta tarea todavía pero la app depende conceptualmente de `alumnos`).

**Qué hacer:**

1. `python manage.py startapp turnos` (o crear los archivos a mano si el comando no está
   disponible en el entorno): `turnos/__init__.py`, `turnos/apps.py` (`TurnosConfig`,
   `name = "turnos"`, patrón `novedades/apps.py`), `turnos/models.py`, `turnos/admin.py`,
   `turnos/tests.py`, `turnos/migrations/__init__.py`. NO crear todavía
   `services.py`/`forms.py`/`views.py`/`urls.py` (son de tareas siguientes) — pero si
   `startapp` genera `views.py`/`admin.py` vacíos, dejalos, no hace falta borrarlos.

2. `config/settings.py`: agregar `"turnos",` a `INSTALLED_APPS`, al final de la lista de
   apps de dominio (después de `"novedades"`), con un comentario breve de una línea
   describiendo qué contiene (patrón de los comentarios ya existentes en esa lista).

3. `config/urls.py`: agregar `path("turnos/", include("turnos.urls")),` después de la
   línea de `novedades`. **Nota:** `turnos/urls.py` no existe todavía en esta tarea — vas
   a necesitar crear un `turnos/urls.py` mínimo con `app_name = "turnos"` y
   `urlpatterns = []` para que el `include()` no rompa (las URLs reales se agregan en las
   Tasks 4/5/6).

4. Modelos en `turnos/models.py`:

```python
from django.core.validators import MinValueValidator
from django.db import models

from core.models import TenantOwnedModel


class DiaSemana(models.IntegerChoices):
    LUNES = 0, "Lunes"
    MARTES = 1, "Martes"
    MIERCOLES = 2, "Miércoles"
    JUEVES = 3, "Jueves"
    VIERNES = 4, "Viernes"
    SABADO = 5, "Sábado"
    DOMINGO = 6, "Domingo"
    # Alineado con date.weekday() (0=Lunes) -- NO con isoweekday().


DURACION_CHOICES = [(m, f"{m} minutos") for m in range(15, 181, 15)]


class ConfiguracionTurnos(TenantOwnedModel):
    duracion_minutos = models.PositiveSmallIntegerField(
        choices=DURACION_CHOICES, default=60
    )
    vacantes_default = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = "configuración de turnos"
        verbose_name_plural = "configuraciones de turnos"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio"], name="config_turnos_unica_por_gimnasio"
            )
        ]

    def __str__(self):
        return f"Configuración de turnos de {self.gimnasio}"


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config de un gimnasio: garantiza que la fila exista
    (para poder tomar select_for_update() sobre ella más adelante)."""
    config, _ = ConfiguracionTurnos.objects.get_or_create(gimnasio=gimnasio)
    return config


class HorarioAtencion(TenantOwnedModel):
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_desde = models.TimeField()
    hora_hasta = models.TimeField()

    class Meta:
        verbose_name = "horario de atención"
        verbose_name_plural = "horarios de atención"
        ordering = ["dia_semana", "hora_desde"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hora_desde__lt=models.F("hora_hasta")),
                name="horario_desde_antes_de_hasta",
            )
        ]

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora_desde}-{self.hora_hasta}"


class CupoExcepcion(TenantOwnedModel):
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField()
    vacantes = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "excepción de cupo"
        verbose_name_plural = "excepciones de cupo"
        ordering = ["dia_semana", "hora_inicio"]
        unique_together = ("gimnasio", "dia_semana", "hora_inicio")

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora_inicio} -> {self.vacantes}"


class Reserva(TenantOwnedModel):
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="reservas"
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()

    class Meta:
        verbose_name = "reserva"
        verbose_name_plural = "reservas"
        ordering = ["fecha", "hora_inicio"]
        unique_together = ("gimnasio", "alumno", "fecha", "hora_inicio")
        indexes = [models.Index(fields=["gimnasio", "fecha"])]

    def __str__(self):
        return f"{self.alumno} - {self.fecha} {self.hora_inicio}"
```

Notas de diseño a respetar (no las re-derives distinto):
- `Reserva.alumno` es `CASCADE`, no `PROTECT` como `PagoMensual.alumno` — una reserva no
  es historial contable, no debe bloquear el borrado de una ficha de alumno.
- Cancelar una reserva = borrar la fila (no hay soft-delete acá, a diferencia de
  `Novedad.activa`).
- `CupoExcepcion.vacantes` permite `0` a propósito (franja bloqueada para clases
  especiales).

5. `python manage.py makemigrations turnos` → debe generar `turnos/migrations/0001_initial.py`.

6. `turnos/admin.py`: registrar los 4 modelos con `@admin.register(...)` y
   `list_display` razonable, patrón `novedades/admin.py` / `pagos/admin.py`.

**Tests (`turnos/tests.py`):**
- Creación + `__str__` de cada uno de los 4 modelos.
- `obtener_configuracion(gimnasio)` crea con los defaults (`duracion_minutos=60`,
  `vacantes_default=10`) la primera vez, y la segunda llamada devuelve la MISMA fila
  (no crea una segunda) — assert por `pk` igual y por `ConfiguracionTurnos.objects.count()`.
  == 1.
- Unicidad de `ConfiguracionTurnos` por gimnasio: crear una segunda para el mismo
  gimnasio directamente con `ConfiguracionTurnos.objects.create(...)` debe levantar
  `IntegrityError`.
- Unicidad de `Reserva` por `(gimnasio, alumno, fecha, hora_inicio)`: mismo patrón,
  `IntegrityError` en el duplicado.
- Aislamiento (`for_gimnasio`, patrón `novedades/tests.py::NovedadTenantIsolationTests`):
  crear `HorarioAtencion` y `Reserva` en gimnasio A y B, verificar que
  `Modelo.objects.for_gimnasio(gimnasio_a)` no incluye los de B.
- `CheckConstraint` de `HorarioAtencion` (hora_desde < hora_hasta): crear con
  `hora_desde=hora_hasta` debe levantar `IntegrityError` (en SQLite de test, Django
  valida constraints de check al hacer flush/commit — si el entorno de test no lo
  enforce por alguna razón de backend, dejá el test igual, documentando el intento; no
  te bloquees por esto, es un detalle de backend, no de lógica de negocio).

**Alcance:** SOLO scaffold + modelos + migración + admin + settings/urls (con
`turnos/urls.py` vacío). NO escribas `services.py`, `forms.py` ni `views.py` con lógica —
esas son las Tasks 3, 4 y 5.

---

# Task 3: Servicios de dominio de turnos (`turnos/services.py`)

**Prerequisito:** Task 2 completa (modelos `ConfiguracionTurnos`, `HorarioAtencion`,
`CupoExcepcion`, `Reserva`, `DiaSemana`, `obtener_configuracion` existen en
`turnos/models.py`).

**Qué implementar:** un módulo puro de lógica de dominio, sin dependencia de request/vistas,
100% testeable con `TestCase` normal. Docstring de módulo obligatorio explicando: todas
las fechas/horas se guardan y comparan como hora LOCAL de Argentina
(`TIME_ZONE=America/Argentina/Buenos_Aires`, todos los tenants son gimnasios argentinos —
no hace falta lógica de timezone por tenant); "ahora" se obtiene siempre con
`timezone.localtime()` / `timezone.localdate()`, nunca `datetime.now()`.

Firmas y comportamiento exacto:

```python
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

CIERRE_RESERVA = timedelta(hours=1)

@dataclass(frozen=True)
class Franja:
    fecha: date
    hora_inicio: time
    hora_fin: time
    vacantes: int
    ocupadas: int
    reservada_por_mi: bool = False

    @property
    def llena(self) -> bool:
        return self.ocupadas >= self.vacantes

    @property
    def pasada(self) -> bool:
        """True si el instante de inicio ya pasó (vs. timezone.localtime())."""

    @property
    def reservable(self) -> bool:
        """False si llena, pasada, o si falta menos de CIERRE_RESERVA para el inicio."""


class ErrorDeReserva(Exception):
    """Base. Cada subclase lleva un mensaje en español listo para messages.error()."""

class TurnoInexistente(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ese horario no corresponde a ningún turno disponible.")

class TurnoCerrado(ErrorDeReserva):
    def __init__(self):
        super().__init__("Las reservas cierran una hora antes del turno (o el turno ya pasó).")

class TurnoLleno(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ese turno ya está completo.")

class ReservaDuplicada(ErrorDeReserva):
    def __init__(self):
        super().__init__("Ya tenés una reserva para ese turno.")


def franjas_de_rango(hora_desde: time, hora_hasta: time, duracion_minutos: int) -> list[tuple[time, time]]:
    """Corta [hora_desde, hora_hasta) en franjas de `duracion_minutos`, usando
    datetime.combine(date.min, ...) + timedelta para la aritmética (nunca sumar
    directo sobre `time`). Una franja se incluye SOLO si termina en o antes de
    hora_hasta (se descarta la franja incompleta al final del rango). No cruza
    medianoche -- se asume hora_desde < hora_hasta dentro del mismo día (ya lo
    garantiza el CheckConstraint del modelo)."""

def franjas_del_dia(gimnasio, dia_semana: int) -> list[tuple[time, time]]:
    """Junta las franjas de TODOS los HorarioAtencion del gimnasio para ese
    dia_semana (puede haber varios rangos, ej. 8-12 y 16-21), aplicando
    franjas_de_rango a cada uno con la duracion de ConfiguracionTurnos. Deduplica
    por hora_inicio (si dos rangos solapados generan el mismo horario, aparece una
    sola vez). Sin HorarioAtencion ese día -> []."""

def vacantes_de_franja(gimnasio, dia_semana: int, hora_inicio: time, default: int) -> int:
    """Busca CupoExcepcion(gimnasio, dia_semana, hora_inicio); si existe, devuelve
    su `vacantes` (puede ser 0). Si no existe, devuelve `default`
    (vacantes_default de la config). No valida que hora_inicio sea una franja
    generada -- eso es responsabilidad de quien la llama; una excepción "huérfana"
    (de una duración vieja) simplemente no se usa si esa hora ya no aparece en
    franjas_del_dia."""

def es_franja_vigente(gimnasio, fecha: date, hora_inicio: time) -> bool:
    """True si (fecha.weekday(), hora_inicio) aparece en franjas_del_dia() para
    ese día de esa fecha."""

def grilla_semanal(gimnasio, desde: date, dias: int = 14, alumno=None) -> dict[date, list[Franja]]:
    """Para cada fecha en [desde, desde+dias), arma la lista de Franja del día
    (franjas_del_dia + vacantes_de_franja + conteo real de Reserva de esa
    fecha/hora, en UNA sola query agregada tipo
    Reserva.objects.for_gimnasio(gimnasio).filter(fecha__range=(desde, hasta))
    .values('fecha','hora_inicio').annotate(n=Count('id')) y un dict de apoyo
    para reservada_por_mi si `alumno` no es None)."""

def reservas_por_franja(gimnasio, desde: date, hasta: date) -> dict[tuple[date, time], list]:
    """Para la agenda staff: Reserva.objects.for_gimnasio(gimnasio)
    .filter(fecha__range=(desde,hasta)).select_related('alumno'), agrupadas en un
    dict por (fecha, hora_inicio)."""

def crear_reserva(gimnasio, alumno, fecha: date, hora_inicio: time) -> "Reserva":
    """
    with transaction.atomic():
        config = ConfiguracionTurnos.objects.select_for_update().get(gimnasio=gimnasio)
        1. TurnoInexistente si not es_franja_vigente(gimnasio, fecha, hora_inicio)
        2. TurnoCerrado si el inicio (datetime naive combinando fecha+hora_inicio,
           comparado con timezone.localtime()) está a menos de CIERRE_RESERVA o ya pasó
        3. ReservaDuplicada si ya existe Reserva(gimnasio, alumno, fecha, hora_inicio)
        4. TurnoLleno si count de Reserva de esa franja >= vacantes_de_franja(...)
        5. crear y devolver la Reserva; atrapar IntegrityError del unique_together
           como ReservaDuplicada (carrera entre el chequeo 3 y el create)
    """

def cancelar_reserva(reserva) -> None:
    """Borra la reserva. Solo permitido si la franja NO pasó todavía (se puede
    cancelar hasta el instante de inicio -- el límite de 1h de CIERRE_RESERVA NO
    aplica a cancelar, solo a crear). Si ya pasó, levantar TurnoCerrado (el
    caller decide qué hacer -- en la vista, se traduce a un message.error sin
    borrar)."""

def url_google_calendar(reserva, gimnasio) -> str:
    """Arma la URL de template de Google Calendar:
    https://calendar.google.com/calendar/render?action=TEMPLATE&text=...&dates=
    YYYYMMDDTHHMMSS/YYYYMMDDTHHMMSS&ctz=America/Argentina/Buenos_Aires&details=...
    - text: f"Entrenamiento en {gimnasio.nombre}"
    - dates: inicio = combine(reserva.fecha, reserva.hora_inicio); fin = inicio +
      timedelta(minutes=obtener_configuracion(gimnasio).duracion_minutos), ambos
      formateados sin separadores (%Y%m%dT%H%M%S)
    - Construir el query string con urllib.parse.urlencode (nunca concatenar
      strings a mano, para escapar correctamente espacios/acentos)."""

def eliminar_reservas_desencajadas(gimnasio) -> int:
    """Borra las Reserva futuras (fecha/hora_inicio >= ahora local) del gimnasio
    cuya (dia_semana, hora_inicio) ya NO aparece en franjas_del_dia() con la
    config vigente. Las reservas ya pasadas NO se tocan (quedan como historial).
    Reducir el cupo (sin cambiar horarios/duración) NO debe borrar ninguna --
    solo horarios/duración cambian qué franjas existen. Devuelve la cantidad
    borrada."""
```

**Casos borde ya decididos (no los reabras, seguí estas decisiones):**
- Franja incompleta al final del rango → se descarta, no se genera parcial.
- Excepción de cupo huérfana (duración cambió y esa hora ya no es franja) → se ignora
  silenciosamente, NO se borra (puede revivir si vuelven a esa duración).
- Se puede reservar hasta exactamente `CIERRE_RESERVA` (1h) antes del inicio inclusive
  (es decir, a las 17:00 se puede reservar el turno de las 18:00; a las 17:00:01 ya no).
- Se puede cancelar en cualquier momento hasta que el turno empiece (no hay cierre para
  cancelar).

**Tests (`turnos/tests.py`, sin usar `self.client` — son tests de funciones puras con
setUp de modelos):**
- `franjas_de_rango`: 60' en 8:00–12:00 → 4 franjas exactas (8,9,10,11); 90' en
  8:00–12:30 → verificar el límite exacto (11:00+90min=12:30, ¿entra o no? calculalo con
  cuidado: la franja de 11:00 termina a las 12:30 que es == hora_hasta, así que SÍ entra
  por la regla `hora_fin <= hora_hasta`); un rango que no da para ninguna franja completa
  → `[]`.
- `franjas_del_dia`: día con dos rangos (8-12 y 16-21) → franjas de ambos combinadas;
  día sin `HorarioAtencion` → `[]`; dos rangos solapados no duplican horas.
- `vacantes_de_franja`: sin excepción → default; con excepción → su valor (incluido 0);
  excepción de una hora que ya no es franja de la config actual → simplemente no se
  usa/ignora en el flujo de `grilla_semanal`/`crear_reserva` (no testear la función
  aislada de "ignorar" porque ella no sabe qué es una franja vigente — testealo a nivel
  `crear_reserva`/`grilla_semanal`).
- `es_franja_vigente`: verdadero para una franja generada, falso para una hora que no
  cae en la grilla.
- `crear_reserva`: caso feliz crea la fila; `TurnoInexistente` si la hora no es franja;
  `TurnoCerrado` si falta menos de 1h o ya pasó (usar fechas relativas a
  `timezone.localtime()` en el test, no fechas hardcodeadas, para que el test no rompa
  con el tiempo); `TurnoLleno` cuando ya hay `vacantes_de_franja` reservas; no cuenta
  reservas de otro gimnasio para el cupo; `ReservaDuplicada` en el segundo intento del
  mismo alumno/fecha/hora.
- `cancelar_reserva`: borra si es futura; no borra (o levanta `TurnoCerrado`, documentá
  cuál elegiste) si ya pasó.
- `eliminar_reservas_desencajadas`: crea una reserva futura vigente, cambia la config
  (duración distinta) de forma que esa hora ya no sea franja, corre la función, verificá
  que se borró y que devuelve `1`; una reserva pasada con el mismo desencaje NO se borra;
  una reserva que sigue encajando no se toca.
- `url_google_calendar`: el string devuelto contiene `action=TEMPLATE`, las fechas en
  formato `%Y%m%dT%H%M%S` correctas (inicio = fecha+hora_inicio, fin = inicio + duración
  de la config), y `ctz=America%2FArgentina%2FBuenos_Aires` (urlencoded) — usar
  `urllib.parse.parse_qs` sobre la URL para parsear y comparar los valores en vez de
  comparar el string completo (más robusto al orden de los params).

**Alcance:** SOLO `turnos/services.py` + sus tests. NO toques `views.py`, `forms.py` ni
`urls.py` — la Task 4 los usa.

---

# Task 4: Configuración de turnos (staff) — forms, vistas, templates

**Prerequisito:** Tasks 2 y 3 completas (modelos + `turnos/services.py`).

**Interfaces de tareas previas que vas a usar:**
- `core.mixins.TenantScopedMixin`, `core.forms.TenantScopedModelForm`,
  `tenants.mixins.StaffRequiredMixin`.
- `turnos.models.{ConfiguracionTurnos, HorarioAtencion, CupoExcepcion, obtener_configuracion, DiaSemana}`.
- `turnos.services.{franjas_del_dia, eliminar_reservas_desencajadas}`.

**`turnos/forms.py`:**
- `ConfiguracionTurnosForm(forms.ModelForm)` — `Meta.model = ConfiguracionTurnos`,
  `fields = ["duracion_minutos", "vacantes_default"]`. Sin FK tenant-owned → ModelForm
  plano (patrón `tenants/forms.py::GimnasioForm`), NO `TenantScopedModelForm`.
- `HorarioAtencionForm(TenantScopedModelForm)` — `fields = ["dia_semana", "hora_desde",
  "hora_hasta"]`; `clean()`: validar `hora_desde < hora_hasta` (mensaje: "El horario de
  inicio debe ser anterior al de cierre.") y no-solapamiento contra
  `HorarioAtencion.objects.for_gimnasio(self.gimnasio).filter(dia_semana=cleaned['dia_semana'])`
  (excluir `self.instance.pk` si es edición — aunque esta tarea no crea vista de edición,
  dejalo preparado). Mensaje de solapamiento: "Ya existe un horario que se superpone ese día."
- `CupoExcepcionForm(TenantScopedModelForm)` — `fields = ["dia_semana", "hora_inicio",
  "vacantes"]`; `clean()`: llamar `franjas_del_dia(self.gimnasio, cleaned['dia_semana'])`
  y validar que `cleaned['hora_inicio']` esté entre las horas de inicio generadas; si no,
  error: "Ese horario no coincide con ninguna franja de turnos de ese día."

**`turnos/views.py`:**
- Mixin local `ReconciliaReservasMixin`: método `_reconciliar(self)` que llama
  `n = eliminar_reservas_desencajadas(self.gimnasio)` y si `n > 0`:
  `messages.warning(self.request, f"Se cancelaron {n} reserva(s) futura(s) que ya no encajan en la nueva grilla.")`.
  Llamarlo desde `form_valid` (después de guardar) y desde el `post` de las vistas de
  eliminar (después de borrar).
- `ConfiguracionTurnosView(StaffRequiredMixin, ReconciliaReservasMixin, UpdateView)`:
  `model = ConfiguracionTurnos`, `form_class = ConfiguracionTurnosForm`, template
  `turnos/configuracion_form.html`, `success_url = reverse_lazy("turnos:configuracion")`.
  `get_object(self, queryset=None)`: `return obtener_configuracion(self.request.user.perfil.gimnasio)`
  (patrón `tenants/views.py::GimnasioUpdateView`, sin pk en la URL). `get_context_data`:
  agregar `horarios = HorarioAtencion.objects.for_gimnasio(gimnasio)` y `excepciones =
  CupoExcepcion.objects.for_gimnasio(gimnasio)` y sus forms vacíos
  (`horario_form`, `cupo_form`) para renderizarlos en la misma página.
- `HorarioAtencionCreateView(StaffRequiredMixin, TenantScopedMixin,
  ReconciliaReservasMixin, CreateView)`: `model=HorarioAtencion`,
  `form_class=HorarioAtencionForm`, `success_url = reverse_lazy("turnos:configuracion")`,
  template `turnos/horario_form.html`. Llamar `self._reconciliar()` en `form_valid`
  (después de `super().form_valid(form)`).
- `HorarioAtencionEliminarView(StaffRequiredMixin, TenantScopedMixin,
  ReconciliaReservasMixin, SingleObjectMixin, View)` — POST-only
  (`http_method_names=["post"]`), `model=HorarioAtencion`, `get_queryset` scopeado (lo da
  `TenantScopedMixin`); en `post`: `self.get_object().delete()`, `self._reconciliar()`,
  redirect a `turnos:configuracion`.
- `CupoExcepcionCreateView` / `CupoExcepcionEliminarView` — mismo patrón que Horario pero
  SIN `ReconciliaReservasMixin` (los cupos no desencajan reservas, solo la vigencia de la
  franja lo hace).

**`turnos/urls.py`** (reemplazar el `urlpatterns = []` de la Task 2), `app_name = "turnos"`:
```
path("configuracion/", ConfiguracionTurnosView.as_view(), name="configuracion")
path("configuracion/horarios/nuevo/", HorarioAtencionCreateView.as_view(), name="horario_crear")
path("configuracion/horarios/<int:pk>/eliminar/", HorarioAtencionEliminarView.as_view(), name="horario_eliminar")
path("configuracion/cupos/nuevo/", CupoExcepcionCreateView.as_view(), name="cupo_crear")
path("configuracion/cupos/<int:pk>/eliminar/", CupoExcepcionEliminarView.as_view(), name="cupo_eliminar")
```
(Las URLs de agenda/mis_turnos/reservar/cancelar las agrega la Task 5/6 — no las crees acá.)

**Templates** (patrón exacto de `templates/novedades/novedad_form.html` y
`novedad_list.html` — mismas clases CSS, mismo layout de bloques):
- `templates/turnos/configuracion_form.html`: form de duración/vacantes_default +
  tabla de horarios (día, desde, hasta, botón eliminar POST) + tabla de excepciones (día,
  hora, vacantes, botón eliminar POST) + los dos forms de "agregar horario"/"agregar
  excepción" (pueden ir inline en la misma página o como forms separados que postean a
  `turnos:horario_crear`/`turnos:cupo_crear` con `success_url` de vuelta a esta página —
  elegí lo más simple: forms inline en esta misma plantilla).
- `templates/turnos/horario_form.html` y `templates/turnos/cupo_form.html`: solo se
  necesitan si decidís que crear no es inline (fallback si el form falla la validación y
  Django necesita re-renderizar `CreateView` con errores) — usá el patrón mínimo de
  `novedad_form.html` (`{{ form.as_p }}` + botón).

**Tests (`turnos/tests.py`, con `self.client`):**
- Anónimo → redirect a login en `turnos:configuracion`. Alumno → 403.
- GET `configuracion` crea la fila con `obtener_configuracion` si no existía (200,
  contexto trae los defaults).
- POST cambia `duracion_minutos`/`vacantes_default` y persiste.
- Crear horario válido (200/302 + fila creada); `hora_desde >= hora_hasta` → error de
  form, no crea fila; horario solapado con uno existente el mismo día → error, no crea.
- Crear excepción con hora que no es franja de ese día → error, no crea.
- Eliminar horario de otro gimnasio → 404, no borra.
- Cambiar la duración de forma que una reserva futura quede desencajada → la reserva se
  borra y el response/messages incluye el conteo (podés chequear
  `response.wsgi_request._messages` o seguir el redirect con `follow=True` y
  `assertContains` del texto del mensaje).
- Una reserva pasada que quedaría "desencajada" con la nueva duración NO se borra.

**Alcance:** SOLO configuración (staff). NO implementes `MisTurnosView`, `ReservarView`,
`CancelarReservaView` ni `AgendaView` — son las Tasks 5 y 6.

---

# Task 5: Grilla y reservas del alumno

**Prerequisito:** Tasks 1, 2, 3, 4 completas.

**Interfaces de tareas previas que vas a usar:**
- `tenants.mixins.AlumnoRequiredMixin` (Task 1) — expone `self.gimnasio`, `self.alumno`
  (puede ser `None`).
- `turnos.services.{grilla_semanal, crear_reserva, cancelar_reserva, url_google_calendar,
  ErrorDeReserva, TurnoCerrado}`.
- `turnos.models.{Reserva, HorarioAtencion}`.
- `turnos/urls.py` ya tiene `app_name="turnos"` con las rutas de configuración (Task 4)
  — vas a AGREGAR rutas nuevas a ese mismo archivo, no reemplazarlo.

**`turnos/forms.py`:** agregar `ReservaForm(forms.Form)` — `fecha = forms.DateField()`,
`hora_inicio = forms.TimeField()`. Es solo parseo del POST; toda la validación de negocio
(vigencia, cupo, cierre, duplicado) vive en `services.crear_reserva` — el form NO
duplica esa lógica.

**`turnos/views.py`:**
- `MisTurnosView(AlumnoRequiredMixin, TemplateView)` — template
  `turnos/mis_turnos.html`. En `get_context_data`:
  - `hoy = timezone.localdate()`, `lunes_actual = hoy - timedelta(days=hoy.weekday())`.
  - `grilla = grilla_semanal(self.gimnasio, desde=lunes_actual, dias=14, alumno=self.alumno)`.
  - `mis_reservas`: si `self.alumno` es `None`, `[]`; si no,
    `Reserva.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno,
    fecha__gte=timezone.localdate())` — para cada una, precomputar en el template o en
    una lista de tuplas `(reserva, url_google_calendar(reserva, self.gimnasio))` (evitar
    llamar la función desde el template — Django permite llamar métodos sin args pero
    esta función toma 2 args, así que resolvela en la vista).
  - `sin_horarios = not HorarioAtencion.objects.for_gimnasio(self.gimnasio).exists()`.
- `ReservarView(AlumnoRequiredMixin, View)` — POST-only. En `post`:
  `if self.alumno is None: raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")`;
  parsear `ReservaForm(request.POST)`; si `is_valid()`, `try: crear_reserva(self.gimnasio,
  self.alumno, cd["fecha"], cd["hora_inicio"]) except ErrorDeReserva as e:
  messages.error(request, str(e)) else: messages.success(request, "¡Turno reservado!")`;
  si el form no es válido, `messages.error(request, "Datos de turno inválidos.")`; SIEMPRE
  `return redirect("turnos:mis_turnos")` al final (nunca 500 por un error de negocio).
- `CancelarReservaView(AlumnoRequiredMixin, SingleObjectMixin, View)` — POST-only.
  `model = Reserva`. `get_queryset(self)`: `return
  Reserva.objects.for_gimnasio(self.gimnasio).filter(alumno=self.alumno)` (si
  `self.alumno is None`, que devuelva `Reserva.objects.none()` para que dé 404 en vez de
  reventar). En `post`: `reserva = self.get_object()`; `try: cancelar_reserva(reserva)
  except TurnoCerrado: messages.error(...) else: messages.success(...)`; redirect a
  `turnos:mis_turnos`.

**`turnos/urls.py`** — agregar (mantener las 5 rutas de Task 4):
```
path("mis-turnos/", MisTurnosView.as_view(), name="mis_turnos")
path("reservar/", ReservarView.as_view(), name="reservar")
path("reservas/<int:pk>/cancelar/", CancelarReservaView.as_view(), name="cancelar")
```

**Template `templates/turnos/mis_turnos.html`:**
- Si `sin_horarios`: tarjeta única "El gimnasio todavía no configuró sus turnos.", nada más.
- Tarjeta "Mis reservas" (si hay alguna): tabla fecha/hora, por fila: link "Agregar a
  Google Calendar" (`target="_blank" rel="noopener"`, `href` = la URL precomputada) +
  form POST inline `turnos:cancelar` con botón "Cancelar" (patrón form "Ocultar" de
  `novedad_list.html`).
- Grilla: iterar `grilla.items()` (fecha → lista de `Franja`); un bloque/columna por
  día con el nombre del día + fecha; por cada `Franja`: `{{ franja.hora_inicio }} ·
  {{ franja.ocupadas }}/{{ franja.vacantes }}`, y:
  - si `franja.reservada_por_mi` → badge `badge--ok` "Reservado" (sin botón — se
    cancela desde "Mis reservas" arriba).
  - elif `franja.pasada` → texto atenuado (clase `.texto-suave`), sin acción.
  - elif `franja.llena` → badge `badge--riesgo` "Completo".
  - elif not `franja.reservable` (cerrada, < 1h) → badge `badge--alerta` "Reservas cerradas".
  - else → form POST a `turnos:reservar` con hidden `fecha`/`hora_inicio` + botón
    "Reservar".
  - Día sin franjas → "Cerrado" (`.texto-suave`).

**CSS:** agregar a `styles/input.css` (`@layer components`): `.grilla-turnos` (grid
responsive, columnas = días), `.turno`, `.turno--pasado`, `.turno--lleno`, `.turno--mio`
(estas 3 solo aportan color/opacity vía `@apply` sobre utilities existentes). Correr
`npm run build:css` al terminar (verificar que `static/css/app.css` cambió).

**Tests (`turnos/tests.py`):**
- Staff → 403 en `mis_turnos`/`reservar`/`cancelar`. Anónimo → redirect login.
- Alumno sin ficha vinculada: GET `mis_turnos` → 200 con estado manejado (no 500); POST
  `reservar` → 403.
- Reservar una franja libre y vigente → crea `Reserva` con el `gimnasio` correcto,
  redirect, `messages` de éxito.
- Reservar llena / cerrada (<1h) / duplicada / fuera de grilla → NO crea fila, redirect
  con `messages.error`, verificar `Reserva.objects.count()` no cambió.
- La grilla muestra el texto de ocupación "X/Y" (`assertContains`) para al menos una
  franja con una reserva previa creada en el setUp.
- "Mis reservas" incluye el `href` con `calendar.google.com` para una reserva existente.
- Cancelar una reserva propia futura → la borra, redirect. Cancelar una reserva de OTRO
  alumno (mismo o distinto gimnasio) → 404, no borra. Cancelar una ya pasada → no borra,
  `messages.error`.
- Un alumno del gimnasio B no ve en su grilla la ocupación generada por reservas del
  gimnasio A (créalas en el setUp y verificá el conteo en el contexto).

**Alcance:** SOLO grilla/reserva/cancelación del alumno + su template + CSS. NO toques
la vista de agenda del staff (Task 6) ni el portal `tenants/views.py::_portal_alumno`
(eso es la Etapa D / Task 13).

---

# Task 6: Agenda del staff + nav

**Prerequisito:** Tasks 2–5 completas.

**Interfaces de tareas previas:** `turnos.services.{grilla_semanal,
reservas_por_franja}`, `turnos.models.Reserva`, `StaffRequiredMixin`.

**`turnos/views.py`:**
- `AgendaView(StaffRequiredMixin, TemplateView)` — template `turnos/agenda.html`. En
  `get_context_data`: mismo cálculo de `lunes_actual` que `MisTurnosView` (14 días);
  `grilla = grilla_semanal(gimnasio, desde=lunes_actual, dias=14)` (sin `alumno`, no hace
  falta `reservada_por_mi` acá); `reservas = reservas_por_franja(gimnasio, lunes_actual,
  lunes_actual + timedelta(days=13))` — un dict `(fecha, hora_inicio) -> [Reserva, ...]`
  para poder mostrar los nombres en el template sin N+1 queries.

**`turnos/urls.py`** — agregar `path("agenda/", AgendaView.as_view(), name="agenda")`.

**Template `templates/turnos/agenda.html`:** grilla similar a `mis_turnos.html` pero sin
botones de reservar/cancelar; cada franja con reservas muestra
`<details><summary>{{ ocupadas }}/{{ vacantes }}</summary><ul>{% for r in reservas de esa franja %}<li>{{ r.alumno }}</li>{% endfor %}</ul></details>`
(HTML nativo, sin JS ni vista extra para el detalle). Header con botón/link "Configuración"
→ `turnos:configuracion`.

**`templates/base.html`** (nav staff, líneas ~44-52): agregar
`<a href="{% url 'turnos:agenda' %}">Turnos</a>` en el bloque `{% if user.perfil.rol ==
"staff" %}`, en el orden que prefieras respecto a los links existentes (Alumnos,
Ejercicios, Rutinas, Pagos, Novedades, Mi gimnasio) — sugerido: entre "Pagos" y
"Novedades".

**Tests (`turnos/tests.py`):**
- Anónimo → redirect login; alumno → 403 en `turnos:agenda`.
- La agenda muestra el conteo "X/Y" y el nombre del alumno anotado (crear una `Reserva`
  en el setUp y `assertContains` su `alumno.nombre`/`apellido`).
- No muestra reservas de otro gimnasio (crear una reserva en gimnasio B, verificar que
  su alumno NO aparece en el response del staff de A).
- El nav de `base.html` para un usuario staff logueado contiene el link a
  `turnos:agenda` (test simple con `assertContains(response, reverse("turnos:agenda"))`
  sobre cualquier página que extienda `base.html`, ej. `home`).

**Alcance:** SOLO agenda + nav. Esto CIERRA la Feature A (turnos). No toques
`novedades/` ni `pagos/` — son las Tasks 7-12.

---

# Task 7: Modelo `NovedadLeida`

**Prerequisito:** ninguna de las anteriores (independiente de `turnos`, pero sí depende
del código base existente: `novedades/models.py`, `alumnos/models.py::Alumno`).

**Dónde:** `novedades/models.py`, agregar debajo de `Novedad`:

```python
class NovedadLeida(TimeStampedModel):
    """Registro de que un alumno leyó una novedad. NO es TenantOwnedModel -- se
    scopea a través de `novedad.gimnasio`, mismo patrón que RutinaAsignadaItem."""

    novedad = models.ForeignKey(Novedad, on_delete=models.CASCADE, related_name="lecturas")
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="novedades_leidas"
    )

    class Meta:
        verbose_name = "novedad leída"
        verbose_name_plural = "novedades leídas"
        unique_together = ("novedad", "alumno")

    def __str__(self):
        return f"{self.alumno} leyó '{self.novedad.titulo}'"
```

`TimeStampedModel` ya está importado en `novedades/models.py` (lo usa `Novedad` vía
`TenantOwnedModel`) — importalo directo de `core.models` si no está en el `from
core.models import ...` existente.

`python manage.py makemigrations novedades` → debe generar `novedades/migrations/0002_novedadleida.py`
(no debe tocar `Novedad`, solo agregar el modelo nuevo).

Registrar en `novedades/admin.py`: `@admin.register(NovedadLeida)` con
`list_display = ["novedad", "alumno", "creado"]`.

**Tests (`novedades/tests.py`):**
- Creación + `__str__`.
- Unicidad `(novedad, alumno)`: segundo `NovedadLeida.objects.create(...)` con el mismo
  par → `IntegrityError`.
- Borrar la `Novedad` borra en cascada sus `NovedadLeida` (crear, borrar la novedad,
  verificar `NovedadLeida.objects.count() == 0`).

**Alcance:** SOLO el modelo + migración + admin + estos 3 tests. NO toques
`novedades/views.py`, `novedades/urls.py` ni ninguna plantilla — eso es la Task 8.

---

# Task 8: Marcar novedad como leída (alumno) + badge en el portal

**Prerequisito:** Tasks 1 (`AlumnoRequiredMixin`) y 7 (`NovedadLeida`) completas.

**Interfaces previas:** `tenants.mixins.AlumnoRequiredMixin`,
`novedades.models.NovedadLeida`.

**`novedades/views.py`:** agregar

```python
class NovedadMarcarLeidaView(AlumnoRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        novedad = get_object_or_404(
            Novedad.objects.for_gimnasio(self.gimnasio).visibles(), pk=kwargs["pk"]
        )
        NovedadLeida.objects.get_or_create(novedad=novedad, alumno=self.alumno)
        return redirect("home")
```

(Import `AlumnoRequiredMixin` desde `tenants.mixins`; import `get_object_or_404` de
`django.shortcuts` si no está.)

**`novedades/urls.py`:** agregar
`path("<int:pk>/leida/", NovedadMarcarLeidaView.as_view(), name="marcar_leida")`.

**`tenants/views.py::HomeView._portal_alumno`** (líneas ~89-127): agregar al dict que
devuelve, en AMBAS ramas (con y sin `alumno`):
```python
"ids_novedades_leidas": (
    set(alumno.novedades_leidas.values_list("novedad_id", flat=True))
    if alumno is not None else set()
),
```
(No hace falta import nuevo — `alumno.novedades_leidas` es el `related_name` de
`NovedadLeida.alumno`.)

**`templates/tenants/home.html`** (bloque "Últimas novedades", líneas ~158-165):
reemplazar el loop actual por uno que muestre también `novedad.mensaje` y, si hay
`alumno` y `novedad.pk not in ids_novedades_leidas`, un badge `badge--alerta` "Nueva" +
un form POST inline (`action="{% url 'novedades:marcar_leida' novedad.pk %}"`) con botón
"Marcar como leída" (patrón exacto del form "Ocultar" de `novedad_list.html`: método
POST, `{% csrf_token %}`, sin necesidad de `hx-boost="false"` porque no sube archivos).
Si ya está leída o no hay `alumno`, sin badge ni botón.

**Tests:**
- `novedades/tests.py`: marcar leída crea la fila `NovedadLeida`; un segundo POST no
  duplica (`NovedadLeida.objects.count()` sigue en 1) y sigue devolviendo 302; staff →
  403; anónimo → redirect login; novedad de OTRO gimnasio → 404; novedad oculta
  (`activa=False`) o vencida (`visible_hasta` pasado) → 404 (no está en `.visibles()`);
  GET a la URL → 405.
- `tenants/tests.py::HomeViewAlumnoTests`: el contexto de `home` para un alumno trae
  `ids_novedades_leidas` correcto tras marcar una leída; el HTML muestra el badge "Nueva"
  solo para las no leídas (`assertContains`/`assertNotContains` con el texto "Nueva");
  un alumno sin ficha (`perfil.alumno` no vinculado) no ve botones "Marcar como leída"
  (no debería reventar tampoco).

**Alcance:** SOLO esta vista + el portal + el template del portal. NO toques
`NovedadListView` ni ninguna vista de staff — es la Task 9.

---

# Task 9: Conteo de lecturas para el staff

**Prerequisito:** Tasks 7 y 8 completas.

**`novedades/views.py::NovedadListView`:** cambiar `get_queryset` para anotar:
```python
def get_queryset(self):
    return super().get_queryset().annotate(lecturas_count=Count("lecturas", distinct=True))
```
En `get_context_data`, agregar (import tardío dentro del método, patrón
`tenants/views.py::HomeView._metricas_dashboard`):
```python
from alumnos.models import Alumno
context["alumnos_activos_count"] = Alumno.objects.for_gimnasio(self.gimnasio).filter(
    estado=Alumno.Estado.ACTIVO
).count()
```
(`self.gimnasio` ya existe vía `TenantScopedMixin`.)

Agregar vista nueva:
```python
class NovedadLecturasView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    model = Novedad
    template_name = "novedades/novedad_lecturas.html"
    context_object_name = "novedad"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["lecturas"] = self.object.lecturas.select_related("alumno")
        return context
```

**`novedades/urls.py`:** agregar
`path("<int:pk>/lecturas/", NovedadLecturasView.as_view(), name="lecturas")`.

**`templates/novedades/novedad_list.html`:** agregar columna "Leída por" con
`<a href="{% url 'novedades:lecturas' novedad.pk %}">{{ novedad.lecturas_count }}/{{ alumnos_activos_count }}</a>`
(ajustar el `colspan` del `{% empty %}` de la tabla, que hoy cuenta las columnas
existentes — sumale 1).

**`templates/novedades/novedad_lecturas.html`** (nuevo): tabla con columnas
Alumno / Fecha de lectura (`lectura.creado`), iterando `lecturas`; link "Volver" a
`novedades:listado`. Extiende `base.html`, usa `.tabla`.

**Tests (`novedades/tests.py`):**
- El contexto de `NovedadListView` trae `lecturas_count` correcto por novedad (crear 2
  `NovedadLeida` para una y 0 para otra, verificar los valores) y `alumnos_activos_count`
  correcto (contando solo `ACTIVO`, ignorando `INACTIVO`).
- `NovedadLecturasView` de una novedad de OTRO gimnasio → 404. Alumno → 403.
- El detalle lista los nombres de los alumnos que leyeron (`assertContains`).

**Alcance:** SOLO estas vistas/templates de novedades. Esto CIERRA la Feature B. No
toques `pagos/` — Tasks 10-12.

---

# Task 10: Modelo `MedioCobro`

**Prerequisito:** ninguna de las anteriores (independiente; solo depende de `pagos/models.py`
existente).

**Dónde:** `pagos/models.py`, agregar:

```python
class MedioCobro(TenantOwnedModel):
    """Alias/CBU al que los alumnos transfieren la cuota. Solo datos exhibidos en el
    portal -- sin integración de pagos (principio no negociable del proyecto: "sin
    Mercado Pago ni integraciones financieras en el MVP")."""

    alias = models.CharField(max_length=60)
    titular = models.CharField(max_length=80, blank=True)
    entidad = models.CharField(max_length=60, blank=True)  # banco o billetera virtual
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "medio de cobro"
        verbose_name_plural = "medios de cobro"
        ordering = ["alias"]

    def __str__(self):
        return self.alias
```

`python manage.py makemigrations pagos` → `pagos/migrations/0002_mediocobro.py` (no debe
tocar `PagoMensual`).

Registrar en `pagos/admin.py`: `@admin.register(MedioCobro)`,
`list_display = ["alias", "titular", "entidad", "activo"]`.

**Tests (`pagos/tests.py`):**
- Creación + `__str__` (devuelve el alias).
- Aislamiento `for_gimnasio` (patrón `PagoMensualModelTests`/`novedades`
  `NovedadTenantIsolationTests`): crear en dos gimnasios, verificar que
  `for_gimnasio(a)` no trae los de b.

**Alcance:** SOLO el modelo + migración + admin + estos tests. NO toques
`pagos/views.py`/`forms.py`/`urls.py` ni ninguna plantilla — Task 11.

---

# Task 11: CRUD de medios de cobro (staff)

**Prerequisito:** Task 10 completa.

**`pagos/forms.py`:** agregar
`MedioCobroForm(TenantScopedModelForm)` — `Meta.model = MedioCobro`,
`fields = ["alias", "titular", "entidad", "activo"]`.

**`pagos/views.py`:** agregar (todas `StaffRequiredMixin, TenantScopedMixin`):
- `MedioCobroListView(ListView)` — `model = MedioCobro`, template
  `pagos/medio_list.html`.
- `MedioCobroCreateView(CreateView)` — `form_class = MedioCobroForm`, template
  `pagos/medio_form.html`, `success_url = reverse_lazy("pagos:medios_listado")`.
- `MedioCobroUpdateView(UpdateView)` — igual, mismo template y `success_url` (es donde
  el staff pone `activo=False` para "borrar" — no hay `DeleteView`, patrón
  `Novedad.activa`).

**`pagos/urls.py`:** agregar, ANTES del patrón `<int:pk>/confirmar/` existente (para que
`medios/` no colisione con nada):
```
path("medios/", MedioCobroListView.as_view(), name="medios_listado")
path("medios/nuevo/", MedioCobroCreateView.as_view(), name="medios_crear")
path("medios/<int:pk>/editar/", MedioCobroUpdateView.as_view(), name="medios_editar")
```

**Templates** (patrón `novedad_list.html`/`novedad_form.html`):
- `templates/pagos/medio_list.html`: tabla alias/titular/entidad/badge activo-inactivo +
  link "Editar" por fila + link "Nuevo medio de cobro" arriba.
- `templates/pagos/medio_form.html`: `{{ form.as_p }}` + botón guardar.
- `templates/pagos/pago_list.html`: agregar un link/botón secundario "Medios de cobro" →
  `pagos:medios_listado` en el header de la página (junto al título; NO agregar entrada
  nueva en el nav global de `base.html` — vive dentro de la sección Pagos).

**Tests (`pagos/tests.py`):**
- Anónimo → redirect login; alumno → 403 en las 3 vistas.
- Crear un medio lo asocia al gimnasio del staff logueado (stampeado server-side, no
  enviable por el cliente).
- Editar un medio de OTRO gimnasio → 404.
- El listado no muestra medios de otro gimnasio.

**Alcance:** SOLO CRUD de medios de cobro. NO toques `tenants/views.py` ni
`templates/tenants/home.html` — Task 12.

---

# Task 12: Portal del alumno — monto y alias de cobro

**Prerequisito:** Task 10 completa (Task 11 no es estrictamente necesaria para esta —
podés crear los `MedioCobro` de prueba directo en el test — pero lo normal es que ya
esté hecha).

**`tenants/views.py::HomeView._portal_alumno`** (líneas ~89-127): agregar import tardío
`from pagos.models import MedioCobro` dentro del método (mismo patrón que el import de
`Novedad`). Agregar al dict de retorno, SOLO en la rama donde `alumno` no es `None`:
```python
"medios_cobro": MedioCobro.objects.for_gimnasio(perfil.gimnasio).filter(activo=True),
```
(En la rama sin `alumno`, no agregar esta clave — el template debe manejar su ausencia
con `{% if medios_cobro %}`, que en Jinja/Django templates es falsy tanto si la clave no
existe como si el queryset está vacío.)

**`templates/tenants/home.html`** (tarjeta "Tu cuota de este mes", líneas ~142-155):
dentro del bloque `{% if mensualidad_actual %}`, agregar: si
`mensualidad_actual.monto > 0`, mostrar `Monto: $ {{ mensualidad_actual.monto }}`. Si
`mensualidad_actual.estado != "pagado"` y `medios_cobro` no está vacío, agregar debajo:
"Podés transferir a:" + un `<ul>` con un `<li>` por medio: `<strong>{{ medio.alias
}}</strong>` + (si tiene) `· {{ medio.titular }}` + (si tiene) `· {{ medio.entidad }}`.
Si el estado ES "pagado", no mostrar la lista de alias (aunque existan medios activos).

**Tests (`tenants/tests.py::HomeViewAlumnoTests`):**
- Alumno con cuota `pendiente` y `monto > 0` ve el monto y los alias activos del
  gimnasio (`assertContains` sobre el alias).
- No ve medios `activo=False` ni medios de OTRO gimnasio.
- Cuota con estado `pagado` → no se muestra la lista de alias (aunque haya medios
  activos) — `assertNotContains` sobre el texto "transferir".
- Sin `mensualidad_actual` (mes sin pago generado) → sin cambios respecto al
  comportamiento actual (no debe reventar por `medios_cobro` ausente).

**Alcance:** SOLO estos dos archivos + tests. Esto CIERRA la Feature C.

---

# Task 13: Integración final, link "Reservar turno" y cierre

**Prerequisito:** TODAS las tareas anteriores (1–12) completas y con test verde
individualmente.

**Qué hacer:**

1. `templates/tenants/home.html`, rama alumno (dentro del `{% else %}` de
   `{% if not alumno %}`, junto con las tarjetas de rutina/cuota): agregar un link
   destacado arriba de "Tu rutina": `<a class="boton" href="{% url 'turnos:mis_turnos' %}">Reservar turno</a>`.
   Este link es visible siempre que el rol sea alumno (la vista `MisTurnosView` ya
   maneja el caso `alumno is None` con su propio estado vacío) — así que en realidad
   puede ir FUERA del `{% if not alumno %}`/`{% else %}`, visible para cualquier alumno
   logueado. Decidí la ubicación exacta viendo el archivo actual, pero debe aparecer en
   ambas ramas (con y sin ficha vinculada).

2. Verificar que `templates/base.html` ya tiene el link "Turnos" (Task 6) — si por algún
   motivo no quedó, agregalo ahora.

3. Confirmar que `npm run build:css` corrió después de los cambios de CSS de la Task 5
   (verificar que `git status` no muestra `static/css/app.css` desactualizado respecto a
   `styles/input.css` — si hay diferencia, correr el build).

4. Correr la suite COMPLETA: `python manage.py test -v 2` — todo verde (105 tests
   originales + todos los agregados en las Tasks 1-12).

5. Correr `python manage.py makemigrations --check --dry-run` — no debe reportar
   migraciones faltantes (si reporta algo, generá la migración faltante y agregala al
   commit de esta tarea).

6. Si durante la implementación de CUALQUIER tarea anterior surgió algo no obvio o un
   riesgo aceptado a propósito, agregá una entrada en `ISSUES.md` siguiendo el formato
   existente del archivo (fecha, causa, resolución) — revisá el archivo para el formato
   exacto antes de escribir.

**Tests:** no se agregan tests nuevos en esta tarea — es integración y verificación. El
"test" de esta tarea ES la corrida completa de la suite en verde.

**Alcance:** Esta es la última tarea. Al cerrarla, las tres features (turnos, lectura de
novedades, mensualidad) deben funcionar de punta a punta.

---

## Verificación end-to-end (manual, para hacer una vez que Task 13 esté verde)

1. `python manage.py test -v 2` (suite completa).
2. Con `runserver`: como staff → Turnos → configurar duración 60', horario lunes 8–12,
   cupo default 12 + una excepción puntual; ver la agenda. Como alumno → portal →
   "Reservar turno" → reservar un turno, ver "1/12", abrir el link "Agregar a Google
   Calendar" y confirmar fecha/hora correctas, cancelar; confirmar que un turno a menos
   de 1h muestra "Reservas cerradas". Staff cambia la duración a 90' → confirmar el
   mensaje de reservas canceladas. Publicar una novedad → el alumno la marca leída →
   staff ve "1/1" y el nombre en el detalle. Cargar un alias → el alumno con cuota
   pendiente lo ve; el staff confirma el pago → el alias deja de mostrarse.
3. `static/css/app.css` commiteado junto con `styles/input.css`.
