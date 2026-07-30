# Portal de cuentas: identidad del alumno, revocación y suplantación

**Frente B** del plan de tres frentes (A = resiliencia de datos, ya cerrado en
código; C = seguridad de acceso, bloqueado por el dominio y el email).

## Problema

El alta de accesos de alumno existe desde Fase 3 (`alumnos/views.py::CrearAccesoView`),
pero le falta todo lo que la hace operable sin que el desarrollador sea el
helpdesk:

1. **El dueño tiene que inventar usuario y contraseña a mano.** El form pide los
   dos campos en texto libre. Un dueño de gimnasio no va a inventar contraseñas
   razonables cincuenta veces.
2. **La contraseña viaja por `messages`** (`alumnos/views.py:155` y `:213`), o
   sea que queda **escrita en la sesión**, que vive en la base de datos, hasta
   que se renderiza.
3. **Dar de baja a un alumno no le apaga el login.** `AlumnoToggleEstadoView`
   cambia `Alumno.estado` y nunca toca `User.is_active`: un alumno dado de baja
   sigue entrando al portal.
4. **No hay ninguna vista de conjunto.** El staff solo ve el acceso de un alumno
   entrando a su ficha, de a uno.
5. **Si un alumno no puede entrar, el staff no tiene forma de ver lo que ve él.**

## Decisiones

Tomadas con el dueño del producto. No reabrir sin motivo nuevo.

| Tema | Decisión |
|---|---|
| Usuario del alumno | Su **email o su teléfono**, a elección del staff |
| Contraseña | **Siempre autogenerada** por la app; el staff nunca inventa una |
| Mostrar la contraseña | Una sola vez, en pantalla, fuera de `messages` |
| "Acceso activo" | **Espejo de `Alumno.estado`**, no un interruptor aparte |
| Ver la cuenta del alumno | **Suplantación** ("entrar como este alumno"), reversible y auditada |
| Guardar contraseñas legibles | **Descartado** |

### Por qué se descartó guardar las contraseñas

El pedido original era que el staff tuviera "los usuarios y contraseñas de todos
los alumnos" visibles en una sección de la web. Se descartó: Django guarda
hashes, así que mostrarlas exige guardarlas en claro (o descifrables) en
paralelo. Eso convierte la cuenta del dueño en un **depósito de credenciales** —
y como la gente reusa contraseñas, robar esa cuenta dejaría de exponer "los datos
del gimnasio" para pasar a exponer los mails personales de cada alumno. Es el
único cambio del proyecto que **empeoraría** el escenario de hackeo que motivó
el pedido.

El objetivo real detrás del pedido —que el staff pueda entrar a cualquier cuenta
sin depender de la memoria del alumno— se cumple mejor con **suplantación +
regeneración**: control total, cero credenciales almacenadas.

### Por qué suplantación a mano y no `django-hijack`

No resuelve ninguno de los cuatro problemas difíciles de este caso: el permiso
por tenant hay que escribirlo igual (su hook por defecto es `superusers_only`),
la auditoría también, y **ni `fecha_activacion` ni `last_login` los arregla** —
llama a `login()` igual. Lo que quedaría son ~40 líneas de mecánica de sesión más
una toolbar con JS y templates propios que choca con la estrategia de `@apply` de
`styles/input.css`.

## Diseño

### Identidad del alumno

`alumnos/identidad.py`, **Django-free a propósito** (testeable con
`SimpleTestCase`, sin base de datos — mismo precedente que
`importaciones/parsing.py`).

- **Email**: `.strip().lower()` + `validate_email`. El lowercase **no es
  cosmético**: `User.objects.get(username=...)` es case-sensitive en Postgres,
  así que sin normalizar `Juan@x.com` y `juan@x.com` serían dos cuentas y el
  alumno no podría entrar. Se normaliza al crear **y** al autenticar.
- **Teléfono AR**: se queda con dígitos y un `+` inicial; saca el `0` de área;
  saca el `15` posterior al área; si quedan 10 dígitos, prefija `+54`.

**Hallazgo que simplifica todo:** `UnicodeUsernameValidator` **acepta `@` y
`+`** (su regex es `^[\w.@+-]+\Z`; lo único que rechaza es whitespace). Email y
teléfono entran tal cual en `auth.User.username`: **no hace falta un `User`
custom ni tocar `AUTH_USER_MODEL`**. El único límite real es `max_length=150`.

### Contraseña generada

Se reusa `tenants.services.generar_password` (ya existe, creada para
`crear_gimnasio`): alfabeto sin caracteres ambiguos (`0/O`, `1/l/I`) porque el
staff la dicta por WhatsApp o en persona. Se valida igual con los 4 validadores
de `config/settings.py`.

**Se muestra una sola vez, sin pasar por `messages`.** El POST no redirige:
renderiza un 200 con la credencial y un botón de copiar. Rompe PRG a propósito;
el guard `if alumno.perfil is not None` que ya existe convierte un F5 en un
redirect inocuo.

### Revocación

`AlumnoToggleEstadoView` sincroniza `alumno.perfil.usuario.is_active` con el
nuevo `estado`, dentro de la transacción. **No hace falta invalidar sesiones a
mano**: `ModelBackend.get_user()` llama a `user_can_authenticate()` en **cada**
request, así que `is_active=False` mata también la sesión viva.

Sale gratis un segundo efecto: regenerar la contraseña **también expulsa** al
alumno, porque `auth.get_user()` compara `HASH_SESSION_KEY` contra
`get_session_auth_hash()`, que deriva del hash de la contraseña.

### Panel de accesos

`AccesoListView(StaffRequiredMixin, TenantScopedMixin, ListView)` sobre `Alumno`.
Columnas: alumno, **usuario exacto** (es la mitigación de un error de
normalización: el staff ve qué tipear), acceso activo, último ingreso. Acciones:
regenerar contraseña y entrar como el alumno.

Entrada **desde el listado de Alumnos, no desde el nav**: el nav ya tiene 8 ítems
y hubo un esfuerzo deliberado por acortarlo — mismo criterio que se usó con el
importador de Excel.

### Suplantación

Servicio en `tenants/suplantacion.py` (no lógica en la vista — patrón
`turnos/services.py`).

**Dos trampas verificadas en el código de Django**, cada una con test de
regresión:

1. **`login()` hace `session.flush()`** cuando cambia el usuario. La clave para
   volver se escribe **después** de `login()`, nunca antes.
2. **`login()` emite `user_logged_in`**, con dos receivers que corromperían
   datos: `alumnos/signals.py:19` estamparía `fecha_activacion` a un alumno que
   nunca entró (arruina la métrica de adopción) y `update_last_login` pisaría el
   "último ingreso" que el panel tiene que mostrar. **No usar
   `signal.disconnect()`** (estado global, no thread-safe): se marca
   `request._suplantacion_en_curso` y se restaura `last_login` con un `.update()`.

Reglas duras: solo staff; solo alumnos del propio gimnasio (404, no 403 —
precedente del repo); nunca a otro staff ni a un superusuario; **no anidable**;
POST + CSRF; expira a las 2 h; y **no se puede suplantar a un alumno dado de
baja**. `volver()` revalida fail-closed.

Auditoría en `RegistroSuplantacion(TenantOwnedModel)`, con `PROTECT` en ambas FK:
una fila de auditoría no puede desaparecer por cascade.

**Riesgo de privacidad a cerrar explícitamente:** durante una suplantación, el
staff podría vincular **su propia** cuenta de Google al calendario del alumno.
`ConectarCalendarioView` y `DesconectarCalendarioView` se bloquean mientras haya
suplantación activa.

## Fuera de alcance

- Login con Google, email transaccional, password reset, `django-axes` y el
  endurecimiento de settings: son el **Frente C**.
- Que el alumno cambie su propia contraseña: necesita el password reset del
  Frente C para ser útil.
- Invitación de staff desde el panel: superficie de escalada de privilegios que
  merece su propio diseño.

## Riesgos aceptados

1. **Colisión de identificador entre gimnasios.** La misma persona entrenando en
   dos gimnasios, o un mail familiar compartido, colisiona: `User.username` es
   único global. No tiene solución limpia — con una sola pantalla de login sin
   selección de gimnasio, el identificador **tiene que** ser globalmente único, y
   resolverlo con subdominios violaría el principio no negociable #6. Se hace
   visible: el form ofrece el otro canal. **El mensaje va genérico**, sin
   confirmar si el email existe en la plataforma: con usuarios inventados era
   irrelevante, con emails reales sería un primitivo de enumeración.
2. **Normalización de teléfonos.** Si difiere entre el alta y el login, el alumno
   no entra y no puede descubrirlo solo. Se mitiga con una tabla exhaustiva de
   casos y mostrando el username exacto en el panel.
3. **Suplantación sin cierre**: si el staff cierra la pestaña, `finalizada_en`
   queda `NULL`. El `creado` alcanza para auditar.

## Criterios de salida

- [x] El staff crea un acceso eligiendo email o teléfono, sin inventar contraseña.
- [x] La contraseña se ve una sola vez y no queda en la sesión.
- [x] Dar de baja a un alumno le impide entrar; reactivarlo lo revierte.
- [x] El panel de accesos lista solo alumnos del propio gimnasio.
- [x] El staff entra como un alumno y vuelve a su cuenta.
- [x] Suplantar no altera `fecha_activacion` ni `last_login`.
- [x] Suite en verde.
