# Sub-cuentas de staff: dueño vs empleado, por gimnasio

Continuación directa de un ítem que el Frente B (portal de cuentas) dejó fuera
de alcance a propósito: *"Invitación de staff desde el panel: superficie de
escalada de privilegios que merece su propio diseño"*
(`docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md`). Este es ese
diseño.

## Problema

El usuario está por empezar a vender el producto a gimnasios de su pueblo y
alrededores (venta directa, uno por uno — no autoservicio). Hoy:

1. **No existe forma de agregar un segundo usuario staff a un gimnasio ya
   creado.** `python manage.py crear_gimnasio` crea exactamente un `User` +
   `Perfil(rol=STAFF)` por gimnasio, en el alta inicial. No hay vista, ruta ni
   comando para sumar otro — confirmado, no es una omisión de documentación.
2. **Todo `Perfil(rol=STAFF)` tiene el mismo permiso total.** No hay noción de
   "empleado" con acceso reducido — `Perfil.rol` distingue `staff`/`alumno`,
   nada más (el docstring del modelo ya anota "separar dueño de entrenador
   queda para después").
3. **No hay forma de revocar el acceso de un empleado** que deja de trabajar
   en el gimnasio.

## Decisiones

Tomadas con el dueño del producto en brainstorming. No reabrir sin motivo nuevo.

| Tema | Decisión |
|---|---|
| Qué puede hacer un empleado | Todo lo operativo: alumnos, rutinas, ejercicios, pagos, novedades, turnos, calendario, importaciones, suplantación — **igual que un dueño** |
| Qué es exclusivo del dueño | Editar el gimnasio (marca/config) y gestionar cuentas de staff (agregar/desactivar empleados) |
| Identidad del empleado | Email o teléfono, mismo mecanismo que el alumno (reusa `alumnos/identidad.py`) |
| Contraseña | Autogenerada, mostrada una sola vez — mismo patrón que accesos de alumno (Frente B) |
| Revocación | Sí — el dueño puede desactivar el acceso de un empleado |
| Permisos intermedios | **No.** Todo-o-nada entre `dueño`/`empleado`, sin sub-permisos por app o acción |

### Por qué no hay permisos granulares por app

Se evaluó (y se descarta) un sistema de checkboxes por app/acción para cada
empleado. Con 2-3 personas de staff por gimnasio en el escenario real (un
gimnasio de barrio, no una cadena), la única distinción que el dueño pidió es
"esto es mío, lo operativo es de cualquiera que trabaje acá". Un sistema
granular es trabajo de diseño y mantenimiento que ningún cliente real pidió
todavía — mismo criterio que ya aplica el proyecto ("primero se cobra,
después se sofistica"). Si en el uso real aparece la necesidad de un tercer
nivel, se rediseña con casos concretos en mano.

## Diseño

### Modelo: `Perfil.nivel`

Nuevo campo en `tenants/models.py::Perfil`:

```python
class Nivel(models.TextChoices):
    DUENO = "dueno", "Dueño"
    EMPLEADO = "empleado", "Empleado"

nivel = models.CharField(max_length=10, choices=Nivel.choices, default=Nivel.DUENO)
```

Solo tiene sentido cuando `rol == STAFF` — para un `Perfil` de alumno queda
sin usar, mismo criterio que otros campos opcionales del proyecto (p.ej. la
ficha ampliada de `Alumno`). El `default=DUENO` es la pieza que evita
cualquier migración de datos: **todo el staff que ya existe hoy queda
automáticamente como dueño** sin backfill manual, porque hoy todos tienen
acceso total — cero regresión.

### Permisos: `DuenoRequiredMixin`

Nuevo mixin en `tenants/mixins.py`, junto a `StaffRequiredMixin`:

```python
class DuenoRequiredMixin(StaffRequiredMixin):
    """Como StaffRequiredMixin, pero además exige nivel=DUENO. Se aplica
    solo a configuración del gimnasio y gestión de staff."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            perfil = getattr(request.user, "perfil", None)
            if (
                perfil is not None
                and perfil.rol == Perfil.Rol.STAFF
                and perfil.nivel != Perfil.Nivel.DUENO
            ):
                raise PermissionDenied("Esta sección es solo para el dueño del gimnasio.")
        return super().dispatch(request, *args, **kwargs)
```

El chequeo de nivel va **antes** de delegar a `StaffRequiredMixin.dispatch()`
a propósito: si no hay `Perfil`, o el rol es `alumno`, o no está autenticado,
esos casos ya los resuelve `StaffRequiredMixin`/`LoginRequiredMixin` — acá
solo se agrega el caso nuevo (staff pero no dueño), sin duplicar el resto de
los chequeos.

Se aplica **solo** a `GimnasioUpdateView` (editar gimnasio) y a las vistas
nuevas de gestión de staff (abajo). **Ninguna otra vista del proyecto se
toca** — alumnos, rutinas, ejercicios, pagos, novedades, turnos, calendario,
importaciones y suplantación siguen usando `StaffRequiredMixin` tal cual
existe hoy. El radio de cambio real queda acotado a `tenants/`.

### Vistas nuevas (mismo patrón que el panel de accesos de alumnos)

- **`tenants:staff_listado`** — lista los `Perfil(rol=STAFF)` del gimnasio
  (columnas: nombre/usuario, nivel, activo, último ingreso). Colgada de la
  pantalla "Editar gimnasio", **no del nav** — mismo criterio ya usado con el
  panel de accesos de alumnos y el importador de Excel (el nav ya tiene 8
  ítems, esfuerzo activo por no sumar más).
- **`tenants:staff_crear`** — form con email o teléfono (reusa
  `alumnos/identidad.py` para normalizar). Crea `User` +
  `Perfil(rol=STAFF, nivel=EMPLEADO, gimnasio=<el del dueño logueado>)` vía
  una función nueva `tenants/services.py::crear_acceso_staff(gimnasio, tipo,
  identificador)`, que espeja `alumnos.services.crear_acceso`: mismo
  anti-doble-submit con `select_for_update()` (sobre `Gimnasio`, que ya existe
  y ya tiene `on_delete=PROTECT` desde `Perfil`) y la misma idea de traducir
  `IntegrityError` a una excepción propia. La contraseña se genera con
  `tenants.services.generar_password` (ya existe) y se muestra una sola vez,
  fuera de `messages`, con el mismo quiebre de PRG que ya usa
  `CrearAccesoView` de alumnos.
- **`tenants:staff_desactivar`** — POST-only. Pone
  `perfil.usuario.is_active = False`. No hace falta invalidar sesión a mano:
  mismo mecanismo que ya usa la revocación de alumnos
  (`ModelBackend.get_user()` revalida `is_active` en cada request).

**Nota de implementación:** `IdentificadorEnUso` (la excepción que traduce el
`IntegrityError` de un username duplicado) hoy vive en `alumnos/services.py`.
Para esta feature se define una excepción **propia y separada** en
`tenants/services.py` en vez de importar la de `alumnos` — son tres líneas,
evita una dependencia cruzada `tenants → alumnos` que hoy no existe en ese
sentido (hoy es `alumnos → tenants`, nunca al revés), y ninguna vista necesita
capturar ambos tipos de forma intercambiable.

### Reglas duras (guardrails)

1. **Un dueño no puede desactivarse a sí mismo.**
2. **No se puede desactivar al último dueño activo del gimnasio** — si un
   gimnasio tiene un solo dueño, `staff_desactivar` sobre ese `Perfil` falla
   (fail-closed). Evita que un gimnasio quede sin nadie que pueda gestionar
   staff o editar su propia configuración.
3. **Un empleado no puede acceder a `staff_listado`/`staff_crear`/
   `staff_desactivar`** — 403 vía `DuenoRequiredMixin`, ya cubierto por el
   punto de permisos arriba.

## Fuera de alcance

- **Permisos granulares** por app/acción para un empleado — ver "Por qué no
  hay permisos granulares" arriba.
- **Reactivar un empleado desactivado.** Para reactivarlo se crea un acceso
  nuevo (mismo identificador, si sigue libre). Se evalúa una vuelta de
  reactivación directa si aparece la necesidad real.
- **La colisión de identificador entre gimnasios** (la misma persona con
  cuentas en dos gimnasios distintos) — ya documentada como riesgo aceptado
  en el Frente B; esta feature no la resuelve ni la empeora, aplica igual a
  cuentas de staff.
- **Login con Google** — cuando exista (Frente C), aplica también a estas
  cuentas; no se diseña acá.

## Riesgos aceptados

1. **Mismo riesgo de colisión global de username que ya existe para
   alumnos** — `auth.User.username` es único a nivel global, no por gimnasio.
   Un email/teléfono ya usado por una cuenta (de alumno o de staff, de
   cualquier gimnasio) no se puede reusar para un nuevo empleado.
2. **Sin niveles intermedios.** Un empleado con acceso a "todo menos
   configuración y gestión de staff" puede resultar demasiado permiso para
   algunos gimnasios reales (por ejemplo, que un empleado no debería poder
   confirmar pagos). Se acepta para la primera versión; se ajusta con casos
   concretos si un cliente pago lo pide.

## Criterios de salida

- [ ] El dueño agrega un empleado eligiendo email o teléfono, sin inventar
      contraseña, y la ve una sola vez en pantalla.
- [ ] El empleado entra y opera alumnos/rutinas/ejercicios/pagos/novedades/
      turnos/calendario/importaciones/suplantación igual que un dueño.
- [ ] El empleado recibe 403 al intentar entrar a "Editar gimnasio" o al
      panel de staff.
- [ ] El dueño desactiva a un empleado y ese usuario deja de poder loguearse
      en el próximo request.
- [ ] Un dueño no puede desactivarse a sí mismo.
- [ ] Un gimnasio no puede quedar sin ningún dueño activo.
- [ ] El panel de staff lista solo cuentas del propio gimnasio (test de
      aislamiento por tenant).
- [ ] Suite en verde.
