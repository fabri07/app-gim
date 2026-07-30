# Portal de cuentas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el dueño de un gimnasio pueda dar acceso a sus alumnos, revocarlo y
resolver "no puedo entrar" sin depender del desarrollador, y sin que la app
guarde ninguna contraseña legible.

**Architecture:** El identificador del alumno pasa a ser su email o su teléfono
normalizado (entran tal cual en `auth.User.username`); la contraseña la genera
siempre la app y se muestra una sola vez fuera de `messages`. Dar de baja a un
alumno apaga su `User.is_active`. Un panel de accesos concentra la operación, con
suplantación reversible y auditada en lugar de un depósito de credenciales.

**Tech Stack:** Django 5.2, sin dependencias nuevas. Tailwind vía `@apply` en
`styles/input.css`. Tests con `SimpleTestCase` (lógica pura) y `TestCase`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md`.
- Todo modelo nuevo tenant-owned hereda de `core.models.TenantOwnedModel` y
  necesita **al menos un test de aislamiento** (que un gimnasio no vea datos de
  otro), siguiendo `tenants/tests.py::TenantIsolationTests`.
- Vistas de gestión: `StaffRequiredMixin` **primero** en el MRO, después
  `TenantScopedMixin`.
- Acceso a un objeto de otro gimnasio → **404**, no 403 (precedente del repo).
- Ningún servicio externo en la suite: si hace falta, desactivarlo con el flag
  `TESTING` de `config/settings.py`.
- Si se toca `styles/input.css`, correr `npm run build:css` **antes** de
  commitear (`static/css/app.css` se versiona).
- Correr la suite con `python manage.py test` (hoy 453 tests, ~7 s).
- Comentarios y mensajes de usuario en español rioplatense.

---

### Task 1: Normalización del identificador del alumno

**Files:**
- Create: `alumnos/identidad.py`
- Test: `alumnos/tests.py` (clase nueva `IdentidadTests`)

**Interfaces:**
- Produces:
  - `normalizar_email(valor: str) -> str` — lowercase y sin espacios; levanta
    `django.core.exceptions.ValidationError` si no es un email.
  - `normalizar_telefono(valor: str) -> str` — forma canónica `+54...`; levanta
    `ValidationError` si no quedan dígitos suficientes.
  - `normalizar_identificador(tipo: str, valor: str) -> str` — despacha según
    `tipo` (`"email"` o `"telefono"`).
  - `TIPO_EMAIL = "email"`, `TIPO_TELEFONO = "telefono"`,
    `TIPOS = [(TIPO_EMAIL, "Email"), (TIPO_TELEFONO, "Teléfono")]`

> **Por qué un módulo aparte y Django-free.** Es lógica pura, y el riesgo real
> del proyecto es que la normalización difiera entre el alta y el login: ahí el
> alumno no entra y no puede descubrirlo solo. Un módulo sin base de datos se
> testea con `SimpleTestCase` y permite una tabla exhaustiva de casos barata.
> Mismo precedente que `importaciones/parsing.py`.
>
> `ValidationError` de `django.core.exceptions` no viola el "Django-free" en el
> sentido que importa (no toca modelos, settings ni base): es solo el tipo de
> excepción que los forms saben mostrar.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `alumnos/tests.py`:

```python
class IdentidadTests(SimpleTestCase):
    """Tabla exhaustiva a propósito: si la normalización difiere entre el alta
    y el login, el alumno no entra nunca y no tiene forma de darse cuenta."""

    def test_email_se_normaliza(self):
        for entrada, esperado in [
            ("Juan@Ejemplo.com", "juan@ejemplo.com"),
            ("  juan@ejemplo.com  ", "juan@ejemplo.com"),
            ("JUAN.PEREZ@EJEMPLO.COM.AR", "juan.perez@ejemplo.com.ar"),
        ]:
            with self.subTest(entrada=entrada):
                self.assertEqual(identidad.normalizar_email(entrada), esperado)

    def test_email_invalido_levanta(self):
        for entrada in ["", "no-es-un-email", "juan@", "@ejemplo.com", "a b@c.com"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValidationError):
                    identidad.normalizar_email(entrada)

    def test_telefono_argentino_se_normaliza(self):
        for entrada, esperado in [
            ("1122334455", "+541122334455"),
            ("11 2233 4455", "+541122334455"),
            ("11-2233-4455", "+541122334455"),
            ("(011) 2233-4455", "+541122334455"),
            ("011 15 2233 4455", "+541122334455"),
            ("+54 11 2233 4455", "+541122334455"),
            ("+5491122334455", "+5491122334455"),
            ("0351 15 555 6677", "+543515556677"),
        ]:
            with self.subTest(entrada=entrada):
                self.assertEqual(identidad.normalizar_telefono(entrada), esperado)

    def test_telefono_invalido_levanta(self):
        for entrada in ["", "123", "no-es-un-telefono", "+"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValidationError):
                    identidad.normalizar_telefono(entrada)

    def test_normalizar_identificador_despacha_por_tipo(self):
        self.assertEqual(
            identidad.normalizar_identificador(identidad.TIPO_EMAIL, "A@B.com"),
            "a@b.com",
        )
        self.assertEqual(
            identidad.normalizar_identificador(identidad.TIPO_TELEFONO, "1122334455"),
            "+541122334455",
        )

    def test_el_identificador_entra_en_username(self):
        """`UnicodeUsernameValidator` acepta `@` y `+` (regex `^[\\w.@+-]+\\Z`).
        Este test es el que justifica no haber hecho un `User` custom: si algún
        día dejara de ser cierto, hay que enterarse acá."""
        validador = UnicodeUsernameValidator()
        validador("juan@ejemplo.com")
        validador("+541122334455")
```

Y a los imports de `alumnos/tests.py`:

```python
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from alumnos import identidad
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test alumnos.tests.IdentidadTests -v 2
```
Esperado: `ModuleNotFoundError: No module named 'alumnos.identidad'`.

- [ ] **Step 3: Escribir `alumnos/identidad.py`**

```python
"""Normalización del identificador con el que entra un alumno.

Django-free a propósito (salvo el tipo de excepción): es lógica pura y el
riesgo real es que la normalización difiera entre el alta y el login — ahí el
alumno no entra nunca y no puede darse cuenta solo. Sin base de datos se puede
testear con `SimpleTestCase` y una tabla exhaustiva de casos.

Mismo precedente que `importaciones/parsing.py`.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

TIPO_EMAIL = "email"
TIPO_TELEFONO = "telefono"
TIPOS = [(TIPO_EMAIL, "Email"), (TIPO_TELEFONO, "Teléfono")]

# Largo de un número argentino sin prefijo de país: área + abonado.
_LARGO_NACIONAL = 10


def normalizar_email(valor):
    """Devuelve el email en minúsculas y sin espacios.

    El lowercase NO es cosmético: `User.objects.get(username=...)` es
    case-sensitive en Postgres, así que sin esto `Juan@x.com` y `juan@x.com`
    serían dos cuentas distintas y el alumno no podría entrar.
    """
    valor = (valor or "").strip().lower()
    validate_email(valor)
    return valor


def normalizar_telefono(valor):
    """Devuelve el teléfono argentino en forma canónica `+54...`.

    Reglas, en este orden: se descarta todo lo que no sea dígito (salvo un `+`
    inicial), se saca el `0` del prefijo de área y el `15` que se intercala
    antes del abonado. Ambos son convenciones de discado nacional que no van en
    la forma internacional.
    """
    crudo = (valor or "").strip()
    tenia_mas = crudo.startswith("+")
    digitos = re.sub(r"\D", "", crudo)

    if not digitos:
        raise ValidationError("Escribí un número de teléfono.")

    if tenia_mas or digitos.startswith("54"):
        digitos = digitos.removeprefix("54")
    else:
        # Discado nacional: 0 de área y 15 antes del abonado.
        digitos = digitos.removeprefix("0")
        for largo_area in (2, 3, 4):
            resto = digitos[largo_area:]
            if resto.startswith("15") and len(digitos) == _LARGO_NACIONAL + 2:
                digitos = digitos[:largo_area] + resto[2:]
                break

    if len(digitos) < _LARGO_NACIONAL:
        raise ValidationError(
            "El teléfono quedó demasiado corto. Escribilo con característica, "
            "por ejemplo 11 2233-4455."
        )
    return f"+54{digitos}"


def normalizar_identificador(tipo, valor):
    if tipo == TIPO_EMAIL:
        return normalizar_email(valor)
    if tipo == TIPO_TELEFONO:
        return normalizar_telefono(valor)
    raise ValidationError("Elegí si el identificador es un email o un teléfono.")
```

- [ ] **Step 4: Correr el test y verificar que pasa**

```bash
python manage.py test alumnos.tests.IdentidadTests -v 2
```
Esperado: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add alumnos/identidad.py alumnos/tests.py
git commit -m "feat(alumnos): normalizar email/teléfono como identificador de acceso"
```

---

### Task 2: Servicios de acceso del alumno

**Files:**
- Create: `alumnos/services.py`
- Test: `alumnos/tests.py` (clase nueva `ServiciosAccesoTests`)

**Interfaces:**
- Consumes: `alumnos.identidad.normalizar_identificador`,
  `tenants.services.generar_password` (ya existe, creada para `crear_gimnasio`).
- Produces:
  - `crear_acceso(alumno, tipo, identificador) -> str` — crea `User` + `Perfil`
    ALUMNO, los vincula, y devuelve la contraseña generada en claro.
  - `regenerar_password(alumno) -> str` — devuelve la contraseña nueva.
  - `IdentificadorEnUso(Exception)` — el identificador ya existe en la
    plataforma.

> **Se reusa `tenants.services.generar_password`, no se duplica.** El orden de
> dependencias del proyecto es `core -> tenants -> dominio`, y `alumnos` ya
> importa de `tenants` (`alumnos/views.py` usa `tenants.models.Perfil`).

- [ ] **Step 1: Escribir el test que falla**

```python
class ServiciosAccesoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )

    def test_crear_acceso_devuelve_la_password_y_deja_entrar(self):
        password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "Juan@Ejemplo.com"
        )
        self.alumno.refresh_from_db()

        self.assertIsNotNone(self.alumno.perfil)
        self.assertEqual(self.alumno.perfil.rol, Perfil.Rol.ALUMNO)
        self.assertEqual(self.alumno.perfil.gimnasio, self.gimnasio)
        self.assertEqual(self.alumno.perfil.usuario.username, "juan@ejemplo.com")
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=password)
        )

    def test_crear_acceso_con_telefono_normaliza_el_username(self):
        servicios.crear_acceso(
            self.alumno, identidad.TIPO_TELEFONO, "011 15 2233 4455"
        )
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.perfil.usuario.username, "+541122334455")

    def test_crear_acceso_guarda_el_email_en_el_user(self):
        """Lo necesita el password reset del Frente C:
        `PasswordResetForm.get_users()` busca por `User.email`."""
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.perfil.usuario.email, "juan@ejemplo.com")

    def test_identificador_repetido_no_crea_nada(self):
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")

        with self.assertRaises(servicios.IdentificadorEnUso):
            servicios.crear_acceso(otro, identidad.TIPO_EMAIL, "juan@ejemplo.com")

        otro.refresh_from_db()
        self.assertIsNone(otro.perfil)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_regenerar_password_cambia_la_vieja(self):
        vieja = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        self.alumno.refresh_from_db()
        nueva = servicios.regenerar_password(self.alumno)

        self.assertNotEqual(vieja, nueva)
        self.assertFalse(
            self.client.login(username="juan@ejemplo.com", password=vieja)
        )
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=nueva)
        )

    def test_la_password_generada_pasa_los_validadores(self):
        password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        validate_password(password)  # no debe levantar
```

Imports a agregar en `alumnos/tests.py`:

```python
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from alumnos import services as servicios
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test alumnos.tests.ServiciosAccesoTests -v 2
```
Esperado: `ModuleNotFoundError: No module named 'alumnos.services'`.

- [ ] **Step 3: Escribir `alumnos/services.py`**

```python
"""Lógica de negocio del acceso de un alumno.

Vive acá y no en las vistas por el mismo criterio que `turnos/services.py`: el
alta de un acceso toca tres modelos (`User`, `Perfil`, `Alumno`) y tiene que ser
atómica.

La contraseña NUNCA la elige el staff: la genera la app. Un dueño de gimnasio no
va a inventar cincuenta contraseñas razonables, y las que inventaría serían
peores que las generadas.
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from alumnos.identidad import normalizar_identificador
from tenants.models import Perfil
from tenants.services import generar_password


class IdentificadorEnUso(Exception):
    """El email/teléfono ya está tomado en la plataforma.

    `User.username` es único GLOBAL (no hay namespacing por gimnasio), así que
    esto puede pasar con la misma persona entrenando en dos gimnasios o con un
    mail familiar compartido. Ver el riesgo aceptado en el spec.
    """


@transaction.atomic
def crear_acceso(alumno, tipo, identificador):
    """Crea el login del alumno y devuelve la contraseña en claro.

    Es la ÚNICA vez que la contraseña existe en texto plano: quien llama tiene
    que mostrarla en el momento. No se guarda en ningún lado ni se puede
    recuperar después.
    """
    username = normalizar_identificador(tipo, identificador)

    User = get_user_model()
    if User.objects.filter(username=username).exists():
        raise IdentificadorEnUso(username)

    password = generar_password()
    usuario = User.objects.create_user(
        username=username,
        password=password,
        # `email` se puebla solo si el identificador ES un email. Lo necesita
        # el password reset del Frente C, que busca por `User.email`.
        email=username if "@" in username else "",
    )
    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=alumno.gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno.perfil = perfil
    alumno.save(update_fields=["perfil"])
    return password


@transaction.atomic
def regenerar_password(alumno):
    """Nueva contraseña al azar. Devuelve la nueva en claro.

    Efecto colateral deseado y gratis: esto EXPULSA al alumno de sus sesiones
    vivas. `auth.get_user()` compara `HASH_SESSION_KEY` contra
    `user.get_session_auth_hash()`, que deriva del hash de la contraseña.
    """
    usuario = alumno.perfil.usuario
    password = generar_password()
    usuario.set_password(password)
    usuario.save(update_fields=["password"])
    return password
```

- [ ] **Step 4: Correr el test y verificar que pasa**

```bash
python manage.py test alumnos.tests.ServiciosAccesoTests -v 2
```
Esperado: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add alumnos/services.py alumnos/tests.py
git commit -m "feat(alumnos): servicios de alta y regeneración de acceso"
```

---

### Task 3: Vistas y form del acceso — contraseña fuera de `messages`

**Files:**
- Modify: `alumnos/forms.py:42-101` (borrar `_PASSWORD_HELP_TEXT`,
  `CrearAccesoForm.password` y `CambiarPasswordAlumnoForm` entera)
- Modify: `alumnos/views.py:121-229` (`CrearAccesoView`, `CambiarPasswordAlumnoView`)
- Create: `templates/alumnos/acceso_credenciales.html`
- Modify: `templates/alumnos/acceso_form.html`
- Modify: `templates/alumnos/alumno_detail.html:48` (texto del botón)
- Test: `alumnos/tests.py`

**Interfaces:**
- Consumes: `alumnos.services.crear_acceso`, `regenerar_password`,
  `IdentificadorEnUso`; `alumnos.identidad.TIPOS`.
- Produces: `CrearAccesoForm` con campos `tipo` e `identificador` (sin campo de
  contraseña).

> **Por qué el POST no redirige.** Hoy la contraseña viaja por
> `messages.success`, o sea que Django la escribe en la **sesión**, que vive en
> la base de datos, hasta que se renderiza. Renderizar un 200 directo la deja
> solo en esa respuesta. Se rompe PRG a propósito: el guard
> `if alumno.perfil is not None` que ya existe convierte un F5 en un redirect
> inocuo a la ficha.

- [ ] **Step 1: Escribir el test que falla**

```python
class AccesoViewsTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        self.client.force_login(self.staff)

    def test_crear_acceso_muestra_la_password_sin_pasar_por_la_sesion(self):
        response = self.client.post(
            reverse("alumnos:acceso_crear", args=[self.alumno.pk]),
            {"tipo": "email", "identificador": "juan@ejemplo.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "juan@ejemplo.com")
        # La contraseña está en el HTML...
        password = response.context["password"]
        self.assertContains(response, password)
        # ...y NO en los mensajes (que se serializan en la sesión).
        self.assertEqual(list(response.context["messages"]), [])

    def test_identificador_repetido_es_error_de_form_no_500(self):
        User.objects.create_user("juan@ejemplo.com", password="otra-clave-larga")
        response = self.client.post(
            reverse("alumnos:acceso_crear", args=[self.alumno.pk]),
            {"tipo": "email", "identificador": "juan@ejemplo.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.perfil)

    def test_el_error_de_colision_no_revela_si_el_email_existe(self):
        """Con emails reales como usuario, un mensaje específico sería un
        primitivo de enumeración de usuarios de toda la plataforma."""
        User.objects.create_user("juan@ejemplo.com", password="otra-clave-larga")
        response = self.client.post(
            reverse("alumnos:acceso_crear", args=[self.alumno.pk]),
            {"tipo": "email", "identificador": "juan@ejemplo.com"},
        )
        texto = response.content.decode()
        self.assertNotIn("ya está registrado en la plataforma", texto)
        self.assertIn("No se puede usar ese dato", texto)

    def test_regenerar_password_muestra_la_nueva(self):
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        response = self.client.post(
            reverse("alumnos:acceso_regenerar", args=[self.alumno.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, response.context["password"])

    def test_aislamiento_no_se_crea_acceso_a_alumno_de_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        response = self.client.post(
            reverse("alumnos:acceso_crear", args=[ajeno.pk]),
            {"tipo": "email", "identificador": "ana@ejemplo.com"},
        )
        self.assertEqual(response.status_code, 404)
        ajeno.refresh_from_db()
        self.assertIsNone(ajeno.perfil)
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test alumnos.tests.AccesoViewsTests -v 2
```
Esperado: FAIL — `NoReverseMatch: 'acceso_regenerar'` y errores de form.

- [ ] **Step 3: Reemplazar los forms**

En `alumnos/forms.py`, borrar `_PASSWORD_HELP_TEXT` (líneas 42-47),
`CrearAccesoForm` (50-82) y `CambiarPasswordAlumnoForm` (85-101), y poner:

```python
class CrearAccesoForm(forms.Form):
    """Alta del login de un alumno.

    NO tiene campo de contraseña a propósito: la genera la app (ver
    `alumnos/services.py`). El staff solo elige con qué dato entra el alumno.
    """

    tipo = forms.ChoiceField(
        choices=identidad.TIPOS,
        label="El alumno va a entrar con su",
        initial=identidad.TIPO_EMAIL,
    )
    identificador = forms.CharField(
        max_length=150,
        label="Email o teléfono",
        help_text="Es el usuario con el que va a iniciar sesión.",
    )

    def clean(self):
        datos = super().clean()
        tipo, valor = datos.get("tipo"), datos.get("identificador")
        if not tipo or not valor:
            return datos
        try:
            datos["identificador"] = identidad.normalizar_identificador(tipo, valor)
        except DjangoValidationError as exc:
            self.add_error("identificador", exc.messages)
        return datos
```

Y en los imports de `alumnos/forms.py`, sacar `get_user_model` y
`validate_password` (ya no se usan) y agregar:

```python
from alumnos import identidad
```

- [ ] **Step 4: Reemplazar las vistas**

En `alumnos/views.py`, reemplazar `CrearAccesoView` y `CambiarPasswordAlumnoView`
completas (líneas 121-229) por:

```python
class CrearAccesoView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Alta del login del alumno: solo para alumnos SIN `Alumno.perfil`.

    El POST exitoso NO redirige: renderiza la contraseña en un 200. Ver el
    razonamiento en el plan (la contraseña no debe pasar por `messages`, que se
    serializa en la sesión).
    """

    model = Alumno
    template_name = "alumnos/acceso_form.html"

    def get(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        return self._render(request, alumno, CrearAccesoForm(initial=self._inicial(alumno)))

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        form = CrearAccesoForm(request.POST)
        if form.is_valid():
            try:
                password = servicios.crear_acceso(
                    alumno,
                    form.cleaned_data["tipo"],
                    form.cleaned_data["identificador"],
                )
            except servicios.IdentificadorEnUso:
                # Mensaje deliberadamente genérico: confirmar que ese email ya
                # existe convertiría este form en un enumerador de usuarios de
                # toda la plataforma.
                form.add_error(
                    "identificador",
                    "No se puede usar ese dato. Probá con el otro (si pusiste "
                    "el email, usá el teléfono, o al revés).",
                )
            else:
                return render(
                    request,
                    "alumnos/acceso_credenciales.html",
                    {
                        "alumno": alumno,
                        "usuario": alumno.perfil.usuario.username,
                        "password": password,
                        "modo": "crear",
                    },
                )
        return self._render(request, alumno, form)

    @staticmethod
    def _inicial(alumno):
        if alumno.email:
            return {"tipo": identidad.TIPO_EMAIL, "identificador": alumno.email}
        if alumno.telefono:
            return {"tipo": identidad.TIPO_TELEFONO, "identificador": alumno.telefono}
        return {}

    def _render(self, request, alumno, form):
        return render(
            request, self.template_name, {"form": form, "alumno": alumno}
        )


class RegenerarPasswordView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Nueva contraseña al azar para un alumno que YA tiene acceso.

    POST-only: muta estado. Expulsa al alumno de sus sesiones vivas (efecto de
    que cambie el hash de la contraseña).
    """

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is None:
            messages.error(request, "Este alumno todavía no tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        password = servicios.regenerar_password(alumno)
        return render(
            request,
            "alumnos/acceso_credenciales.html",
            {
                "alumno": alumno,
                "usuario": alumno.perfil.usuario.username,
                "password": password,
                "modo": "regenerar",
            },
        )
```

En los imports de `alumnos/views.py`: sacar `CambiarPasswordAlumnoForm` y
`get_user_model` si quedan sin uso, y agregar:

```python
from alumnos import identidad, services as servicios
from alumnos.forms import CrearAccesoForm
```

- [ ] **Step 5: Crear `templates/alumnos/acceso_credenciales.html`**

```html
{% extends 'base.html' %}
{% block title %}Acceso de {{ alumno }} · App Gimnasios{% endblock %}

{% block content %}
<div class="tarjeta">
  <h1>
    {% if modo == "crear" %}Acceso creado{% else %}Contraseña nueva{% endif %}
  </h1>

  <p class="texto-suave">
    Copiá estos datos y pasáselos a {{ alumno.nombre }} por WhatsApp o en
    persona. <strong>No los vas a poder ver de nuevo:</strong> la app guarda la
    contraseña cifrada, ni vos ni nadie puede recuperarla. Si se pierde,
    generás una nueva desde el panel de accesos.
  </p>

  <table class="tabla-detalle">
    <tr><th>Usuario</th><td><code>{{ usuario }}</code></td></tr>
    <tr><th>Contraseña</th><td><code id="password">{{ password }}</code></td></tr>
  </table>

  <button type="button" class="boton" id="copiar">Copiar los dos datos</button>
  <a class="boton-secundario" href="{% url 'alumnos:detalle' alumno.pk %}">
    Volver a la ficha
  </a>
</div>

<script>
  document.getElementById("copiar").addEventListener("click", async (e) => {
    const texto = "Usuario: {{ usuario|escapejs }}\nContraseña: {{ password|escapejs }}";
    await navigator.clipboard.writeText(texto);
    e.target.textContent = "¡Copiado!";
  });
</script>
{% endblock %}
```

- [ ] **Step 6: Actualizar `templates/alumnos/acceso_form.html`**

Reemplazar el contenido por:

```html
{% extends 'base.html' %}
{% block title %}Crear acceso · {{ alumno }}{% endblock %}

{% block content %}
<div class="tarjeta">
  <h1>Crear acceso para {{ alumno }}</h1>
  <p class="texto-suave">
    La contraseña la genera la app y se muestra una sola vez en la pantalla
    siguiente.
  </p>
  <form method="post" novalidate>
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit" class="boton">Crear acceso</button>
    <a class="boton-secundario" href="{% url 'alumnos:detalle' alumno.pk %}">Cancelar</a>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 7: Actualizar las urls y la ficha**

En `alumnos/urls.py`, reemplazar el import de `CambiarPasswordAlumnoView` por
`RegenerarPasswordView` y la ruta `acceso_cambiar_password` por:

```python
    path(
        "<int:pk>/acceso/regenerar/",
        RegenerarPasswordView.as_view(),
        name="acceso_regenerar",
    ),
```

En `templates/alumnos/alumno_detail.html`, reemplazar la línea 48 por:

```html
      <form method="post" action="{% url 'alumnos:acceso_regenerar' alumno.pk %}">
        {% csrf_token %}
        <button type="submit" class="boton-secundario">Generar contraseña nueva</button>
      </form>
```

- [ ] **Step 8: Correr los tests**

```bash
python manage.py test alumnos -v 2
```
Esperado: PASS. Si algún test viejo referencia `acceso_cambiar_password` o
`CambiarPasswordAlumnoForm`, actualizarlo — la vista ya no existe.

- [ ] **Step 9: Commit**

```bash
git add alumnos/ templates/alumnos/
git commit -m "feat(alumnos): contraseña autogenerada y mostrada fuera de messages"
```

---

### Task 4: Dar de baja a un alumno le apaga el login

**Files:**
- Modify: `alumnos/views.py:99-118` (`AlumnoToggleEstadoView`)
- Test: `alumnos/tests.py`

**Interfaces:**
- Produces: nada nuevo; cambia el comportamiento de `alumnos:activar`.

> **No hace falta invalidar sesiones a mano.** `ModelBackend.get_user()` llama a
> `user_can_authenticate()` en **cada** request, así que poner `is_active=False`
> también mata la sesión viva: el request siguiente ya es anónimo.

- [ ] **Step 1: Escribir el test que falla**

```python
class RevocacionAccesoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        self.password = servicios.crear_acceso(
            self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com"
        )
        self.alumno.refresh_from_db()

    def test_dar_de_baja_impide_entrar(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("alumnos:activar", args=[self.alumno.pk]))

        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.estado, Alumno.Estado.INACTIVO)
        self.assertFalse(self.alumno.perfil.usuario.is_active)

        self.client.logout()
        self.assertFalse(
            self.client.login(username="juan@ejemplo.com", password=self.password)
        )

    def test_reactivar_devuelve_el_acceso(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("alumnos:activar", args=[self.alumno.pk]))
        self.client.post(reverse("alumnos:activar", args=[self.alumno.pk]))

        self.alumno.refresh_from_db()
        self.assertTrue(self.alumno.perfil.usuario.is_active)
        self.client.logout()
        self.assertTrue(
            self.client.login(username="juan@ejemplo.com", password=self.password)
        )

    def test_dar_de_baja_mata_la_sesion_viva(self):
        self.client.login(username="juan@ejemplo.com", password=self.password)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

        cliente_staff = Client()
        cliente_staff.force_login(self.staff)
        cliente_staff.post(reverse("alumnos:activar", args=[self.alumno.pk]))

        # El request siguiente del alumno ya rebota al login.
        self.assertEqual(self.client.get(reverse("home")).status_code, 302)

    def test_alumno_sin_acceso_no_rompe(self):
        sin_acceso = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse("alumnos:activar", args=[sin_acceso.pk]))
        self.assertEqual(response.status_code, 302)

    def test_aislamiento_no_se_puede_togglear_alumno_de_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse("alumnos:activar", args=[ajeno.pk]))
        self.assertEqual(response.status_code, 404)
```

Import a agregar: `from django.test import Client`.

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test alumnos.tests.RevocacionAccesoTests -v 2
```
Esperado: FAIL — `assertFalse(...is_active)` falla (sigue en `True`).

- [ ] **Step 3: Modificar `AlumnoToggleEstadoView.post`**

```python
    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        activo = alumno.estado == Alumno.Estado.ACTIVO
        with transaction.atomic():
            alumno.estado = (
                Alumno.Estado.INACTIVO if activo else Alumno.Estado.ACTIVO
            )
            alumno.save(update_fields=["estado"])
            # El acceso es un ESPEJO del estado del alumno, no un interruptor
            # aparte: un alumno dado de baja no debe poder seguir entrando.
            # Esto además corta su sesión viva, porque `get_user()` revalida
            # `is_active` en cada request.
            if alumno.perfil_id is not None:
                usuario = alumno.perfil.usuario
                usuario.is_active = not activo
                usuario.save(update_fields=["is_active"])
        messages.success(
            request, f"{alumno} ahora está {alumno.get_estado_display().lower()}."
        )
        return redirect("alumnos:detalle", pk=alumno.pk)
```

Verificar que `from django.db import transaction` esté en los imports de
`alumnos/views.py` (ya está, lo usa `crear_acceso`).

- [ ] **Step 4: Correr los tests**

```bash
python manage.py test alumnos.tests.RevocacionAccesoTests -v 2
```
Esperado: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add alumnos/views.py alumnos/tests.py
git commit -m "fix(alumnos): dar de baja a un alumno le apaga el login"
```

---

### Task 5: Panel de accesos

**Files:**
- Modify: `alumnos/views.py` (agregar `AccesoListView`)
- Modify: `alumnos/urls.py`
- Create: `templates/alumnos/acceso_list.html`
- Modify: `templates/alumnos/alumno_list.html:8-10` (botón de entrada)
- Test: `alumnos/tests.py`

**Interfaces:**
- Produces: ruta `alumnos:accesos`.

> **Entrada desde el listado de Alumnos, no desde el nav.** El nav ya tiene 8
> ítems y hubo un esfuerzo deliberado por acortarlo de 10 a 8 — mismo criterio
> que se usó con el importador de Excel, que también cuelga de su listado.

- [ ] **Step 1: Escribir el test que falla**

```python
class PanelAccesosTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        servicios.crear_acceso(self.alumno, identidad.TIPO_EMAIL, "juan@ejemplo.com")
        self.client.force_login(self.staff)

    def test_lista_el_usuario_exacto(self):
        response = self.client.get(reverse("alumnos:accesos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "juan@ejemplo.com")

    def test_aislamiento_no_muestra_alumnos_de_otro_gimnasio(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        servicios.crear_acceso(ajeno, identidad.TIPO_EMAIL, "ana@ejemplo.com")

        response = self.client.get(reverse("alumnos:accesos"))
        self.assertNotContains(response, "ana@ejemplo.com")
        self.assertNotContains(response, "Gómez")

    def test_un_alumno_no_puede_ver_el_panel(self):
        self.client.logout()
        self.alumno.refresh_from_db()
        self.client.force_login(self.alumno.perfil.usuario)
        self.assertEqual(self.client.get(reverse("alumnos:accesos")).status_code, 403)
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test alumnos.tests.PanelAccesosTests -v 2
```
Esperado: `NoReverseMatch: 'accesos'`.

- [ ] **Step 3: Agregar la vista**

En `alumnos/views.py`:

```python
class AccesoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Vista de conjunto de los accesos del gimnasio.

    `select_related` evita un N+1 obvio: cada fila muestra el username y el
    último ingreso, que viven dos saltos más allá (`alumno.perfil.usuario`).
    """

    model = Alumno
    template_name = "alumnos/acceso_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        return (
            super().get_queryset().select_related("perfil__usuario").order_by(
                "apellido", "nombre"
            )
        )
```

- [ ] **Step 4: Agregar la ruta**

En `alumnos/urls.py`, importar `AccesoListView` y agregar antes de
`<int:pk>/`:

```python
    path("accesos/", AccesoListView.as_view(), name="accesos"),
```

> El orden importa: `accesos/` tiene que ir **antes** que cualquier patrón que
> pueda capturarlo. Con `<int:pk>/` no hay conflicto real (`accesos` no es un
> entero), pero mantener las rutas literales arriba evita el problema si algún
> día se agrega un `<slug:...>`.

- [ ] **Step 5: Crear `templates/alumnos/acceso_list.html`**

```html
{% extends 'base.html' %}
{% block title %}Accesos · App Gimnasios{% endblock %}
{% block main_class %}contenido--ancho{% endblock %}

{% block content %}
<div class="contenido--ancho">
  <div class="acciones-lista">
    <h1>Accesos</h1>
    <a class="boton-secundario" href="{% url 'alumnos:listado' %}">Volver a alumnos</a>
  </div>

  <p class="texto-suave">
    El usuario es el dato con el que cada alumno inicia sesión. Si alguno no
    puede entrar, generale una contraseña nueva; si querés ver la app como la
    ve él, usá «Entrar como».
  </p>

  <table class="tabla">
    <thead>
      <tr>
        <th>Alumno</th>
        <th>Usuario</th>
        <th>Acceso</th>
        <th>Último ingreso</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for alumno in alumnos %}
        <tr>
          <td><a href="{% url 'alumnos:detalle' alumno.pk %}">{{ alumno }}</a></td>
          <td>
            {% if alumno.perfil %}
              <code>{{ alumno.perfil.usuario.username }}</code>
            {% else %}
              <span class="texto-suave">Sin acceso</span>
            {% endif %}
          </td>
          <td>
            {% if not alumno.perfil %}
              <span class="texto-suave">—</span>
            {% elif alumno.perfil.usuario.is_active %}
              <span class="badge badge--ok">Activo</span>
            {% else %}
              <span class="badge badge--riesgo">Dado de baja</span>
            {% endif %}
          </td>
          <td>
            {% if alumno.perfil.usuario.last_login %}
              {{ alumno.perfil.usuario.last_login }}
            {% else %}
              <span class="texto-suave">Nunca entró</span>
            {% endif %}
          </td>
          <td>
            {% if alumno.perfil %}
              <div class="acciones-lista">
                <form method="post" action="{% url 'alumnos:acceso_regenerar' alumno.pk %}">
                  {% csrf_token %}
                  <button type="submit" class="boton-secundario">Contraseña nueva</button>
                </form>
                {% if alumno.perfil.usuario.is_active %}
                  <form method="post" action="{% url 'tenants:suplantar' alumno.pk %}">
                    {% csrf_token %}
                    <button type="submit" class="boton-secundario">Entrar como</button>
                  </form>
                {% endif %}
              </div>
            {% else %}
              <a class="boton" href="{% url 'alumnos:acceso_crear' alumno.pk %}">Crear acceso</a>
            {% endif %}
          </td>
        </tr>
      {% empty %}
        <tr><td colspan="5" class="texto-suave">Todavía no hay alumnos.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

> El botón «Entrar como» apunta a `tenants:suplantar`, que se crea en la Task 7.
> **Los tests de esta tarea van a fallar con `NoReverseMatch` hasta entonces.**
> Para no dejar la suite roja entre tareas, en esta tarea el bloque de «Entrar
> como» se deja comentado con `{# ... #}` y se descomenta en la Task 7 Step 6.

- [ ] **Step 6: Botón de entrada en el listado de alumnos**

En `templates/alumnos/alumno_list.html`, reemplazar la línea 9 por:

```html
    <div>
      <a class="boton" href="{% url 'alumnos:crear' %}">Nuevo alumno</a>
      <a class="boton-secundario" href="{% url 'alumnos:accesos' %}">Accesos</a>
    </div>
```

- [ ] **Step 7: Correr los tests**

```bash
python manage.py test alumnos -v 2
```
Esperado: PASS.

- [ ] **Step 8: Commit**

```bash
git add alumnos/ templates/alumnos/
git commit -m "feat(alumnos): panel de accesos del gimnasio"
```

---

### Task 6: Modelo de auditoría y servicio de suplantación

**Files:**
- Modify: `tenants/models.py` (agregar `RegistroSuplantacion`)
- Create: `tenants/migrations/00XX_registrosuplantacion.py` (vía `makemigrations`)
- Create: `tenants/suplantacion.py`
- Modify: `alumnos/signals.py:19-36` (guard de suplantación)
- Test: `tenants/tests.py` (clase nueva `SuplantacionServicioTests`)

**Interfaces:**
- Produces:
  - `tenants.models.RegistroSuplantacion`
  - `tenants.suplantacion.iniciar(request, alumno) -> None`
  - `tenants.suplantacion.volver(request) -> None`
  - `tenants.suplantacion.esta_activa(request) -> bool`
  - `tenants.suplantacion.CLAVE_SESION = "suplantacion"`
  - `tenants.suplantacion.MAX_DURACION = timedelta(hours=2)`

> **Las dos trampas de `login()`, verificadas en el código de Django.**
>
> 1. `django.contrib.auth.login()` hace `request.session.flush()` cuando cambia
>    el usuario. Escribir la clave de vuelta **antes** de `login()` la borra en
>    silencio y la suplantación queda sin retorno. Va **después**.
> 2. `login()` emite `user_logged_in` al final, y hay dos receivers que
>    corromperían datos: `alumnos/signals.py` estamparía `fecha_activacion` a un
>    alumno que nunca entró, y `update_last_login` (de `django.contrib.auth`)
>    pisaría el "último ingreso" que muestra el panel de la Task 5. **No usar
>    `signal.disconnect()`**: es mutación de estado global y no es thread-safe.

- [ ] **Step 1: Escribir el test que falla**

```python
class SuplantacionServicioTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()

    def _request_de(self, usuario):
        request = RequestFactory().post("/")
        request.user = usuario
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        return request

    def test_no_estampa_fecha_activacion(self):
        """El alumno NUNCA entró: suplantarlo no puede marcar que sí."""
        self.assertIsNone(self.alumno.fecha_activacion)
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)
        self.alumno.refresh_from_db()
        self.assertIsNone(self.alumno.fecha_activacion)

    def test_no_cambia_last_login_del_alumno(self):
        usuario = self.alumno.perfil.usuario
        self.assertIsNone(usuario.last_login)
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)
        usuario.refresh_from_db()
        self.assertIsNone(usuario.last_login)

    def test_la_clave_de_sesion_sobrevive_al_flush_de_login(self):
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        self.assertIn(suplantacion.CLAVE_SESION, request.session)
        self.assertEqual(
            request.session[suplantacion.CLAVE_SESION]["original_pk"], self.staff.pk
        )

    def test_registra_la_auditoria_con_el_gimnasio_correcto(self):
        suplantacion.iniciar(self._request_de(self.staff), self.alumno)
        registro = RegistroSuplantacion.objects.get()
        self.assertEqual(registro.gimnasio, self.gimnasio)
        self.assertEqual(registro.staff_usuario, self.staff)
        self.assertEqual(registro.alumno, self.alumno)
        self.assertIsNone(registro.finalizada_en)

    def test_no_se_puede_suplantar_a_un_staff(self):
        otro_staff = User.objects.create_user("staff2", password="clave-larga-456")
        perfil = Perfil.objects.create(
            usuario=otro_staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        falso_alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="X", apellido="Y", perfil=perfil
        )
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), falso_alumno)

    def test_no_se_puede_suplantar_a_un_superusuario(self):
        self.alumno.perfil.usuario.is_superuser = True
        self.alumno.perfil.usuario.save(update_fields=["is_superuser"])
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), self.alumno)

    def test_no_se_puede_suplantar_a_un_alumno_dado_de_baja(self):
        self.alumno.estado = Alumno.Estado.INACTIVO
        self.alumno.save(update_fields=["estado"])
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(self._request_de(self.staff), self.alumno)

    def test_no_es_anidable(self):
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        otro = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Ana", apellido="Gómez"
        )
        crear_acceso(otro, TIPO_EMAIL, "ana@ejemplo.com")
        otro.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            suplantacion.iniciar(request, otro)

    def test_volver_restaura_al_staff_y_cierra_el_registro(self):
        request = self._request_de(self.staff)
        suplantacion.iniciar(request, self.alumno)
        request.user = self.alumno.perfil.usuario

        suplantacion.volver(request)

        self.assertNotIn(suplantacion.CLAVE_SESION, request.session)
        self.assertIsNotNone(RegistroSuplantacion.objects.get().finalizada_en)
```

Imports a agregar en `tenants/tests.py`:

```python
from django.contrib.sessions.middleware import SessionMiddleware

from alumnos.identidad import TIPO_EMAIL
from alumnos.services import crear_acceso
from tenants import suplantacion
from tenants.models import RegistroSuplantacion
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test tenants.tests.SuplantacionServicioTests -v 2
```
Esperado: `ImportError: cannot import name 'suplantacion'`.

- [ ] **Step 3: Agregar el modelo de auditoría**

Al final de `tenants/models.py`:

```python
class RegistroSuplantacion(TenantOwnedModel):
    """Auditoría de "entrar como este alumno".

    SÍ es `TenantOwnedModel`: es dato operativo de un gimnasio y ningún staff
    debe ver las filas de otro.

    `PROTECT` en las dos FK a propósito: una fila de auditoría no puede
    desaparecer por un cascade. El costo aceptado es que borrar un `User` con
    historial de suplantación queda bloqueado — consistente con que un `Alumno`
    nunca se borra (solo cambia de `estado`).

    `creado` (de `TimeStampedModel`) hace de "iniciada_en": no se duplica.
    """

    staff_usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="suplantaciones_iniciadas",
    )
    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="suplantaciones_recibidas",
    )
    finalizada_en = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "registro de suplantación"
        verbose_name_plural = "registros de suplantación"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.staff_usuario} como {self.alumno} ({self.creado:%Y-%m-%d %H:%M})"
```

Verificar los imports de `tenants/models.py`: hacen falta
`from django.conf import settings` y `from core.models import TenantOwnedModel`.

- [ ] **Step 4: Generar la migración**

```bash
python manage.py makemigrations tenants
python manage.py migrate
```

- [ ] **Step 5: Escribir `tenants/suplantacion.py`**

```python
"""Suplantación de un alumno por parte del staff ("entrar como este alumno").

Existe para que el staff pueda resolver "no puedo entrar" y ver la app como la
ve su alumno, SIN que el sistema guarde ninguna contraseña legible. Ver el spec
`docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md`.

La lógica vive en un servicio y no en la vista, mismo criterio que
`turnos/services.py`.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model, login
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.utils import timezone

from tenants.models import Perfil, RegistroSuplantacion

CLAVE_SESION = "suplantacion"
MAX_DURACION = timedelta(hours=2)

# Ojo Frente C: cuando exista `tenants.backends.PerfilModelBackend` y/o
# django-axes, hay que apuntar esta constante al backend propio. `login()`
# necesita saber el backend porque no pasamos por `authenticate()`.
BACKEND = "django.contrib.auth.backends.ModelBackend"


def esta_activa(request):
    return CLAVE_SESION in request.session


def _cambiar_de_usuario(request, usuario):
    """`login()` conservando `last_login` y sin disparar `fecha_activacion`.

    Los dos cuidados son obligatorios, no cosméticos:

    - `request._suplantacion_en_curso` lo lee el receiver de
      `alumnos/signals.py`. El `request` sobrevive al flush de sesión, así que
      es un canal seguro.
    - `last_login` se restaura con un UPDATE directo (no `save()`, para no
      arrastrar otros campos) porque `update_last_login` está conectado por
      `django.contrib.auth` y lo pisaría. Desconectar la señal sería mutar
      estado global compartido entre requests.
    """
    User = get_user_model()
    ultimo_login = usuario.last_login
    request._suplantacion_en_curso = True
    login(request, usuario, backend=BACKEND)
    User.objects.filter(pk=usuario.pk).update(last_login=ultimo_login)


def iniciar(request, alumno):
    """Pasa la sesión del staff a la cuenta del alumno.

    `alumno` YA tiene que venir acotado al gimnasio del staff (la vista lo
    resuelve con `TenantScopedMixin`, que da 404 si es de otro gimnasio).
    """
    if esta_activa(request):
        raise PermissionDenied("Ya estás viendo la app como otra persona.")

    if alumno.perfil is None:
        raise PermissionDenied("Este alumno todavía no tiene acceso.")
    if alumno.perfil.rol != Perfil.Rol.ALUMNO:
        raise PermissionDenied("Solo se puede entrar como un alumno.")
    if alumno.estado != alumno.Estado.ACTIVO:
        raise PermissionDenied("Este alumno está dado de baja.")

    usuario = alumno.perfil.usuario
    # Cinturón y tiradores: aunque el rol diga ALUMNO, nunca escalar a una
    # cuenta con privilegios.
    if usuario.is_superuser or usuario.is_staff:
        raise PermissionDenied("No se puede entrar como una cuenta con privilegios.")

    staff_usuario = request.user
    registro = RegistroSuplantacion.objects.create(
        gimnasio=alumno.gimnasio,
        staff_usuario=staff_usuario,
        alumno=alumno,
        ip=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
    )

    _cambiar_de_usuario(request, usuario)

    # DESPUÉS de login(): `login()` hace session.flush() al cambiar de usuario,
    # así que escribir esto antes lo borraría en silencio.
    request.session[CLAVE_SESION] = {
        "original_pk": staff_usuario.pk,
        "original_nombre": staff_usuario.get_username(),
        "alumno_nombre": str(alumno),
        "inicio": timezone.now().isoformat(),
        "registro_pk": registro.pk,
    }


def volver(request):
    """Devuelve la sesión al staff original. Fail-closed."""
    datos = request.session.get(CLAVE_SESION)
    if not datos:
        raise PermissionDenied("No estás viendo la app como otra persona.")

    User = get_user_model()
    try:
        staff_usuario = User.objects.select_related("perfil__gimnasio").get(
            pk=datos["original_pk"]
        )
    except User.DoesNotExist:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no existe.")

    try:
        perfil = staff_usuario.perfil
    except ObjectDoesNotExist:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no tiene perfil.")

    # Revalidación completa: que la sesión diga que sos staff no alcanza.
    if not staff_usuario.is_active or perfil.rol != Perfil.Rol.STAFF:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no puede operar.")

    RegistroSuplantacion.objects.filter(
        pk=datos["registro_pk"], finalizada_en__isnull=True
    ).update(finalizada_en=timezone.now())

    # login() flushea la sesión, así que la clave se borra sola.
    _cambiar_de_usuario(request, staff_usuario)


def vencida(request):
    """True si la suplantación superó `MAX_DURACION`."""
    datos = request.session.get(CLAVE_SESION)
    if not datos:
        return False
    inicio = timezone.datetime.fromisoformat(datos["inicio"])
    return timezone.now() - inicio > MAX_DURACION
```

- [ ] **Step 6: Guard en `alumnos/signals.py`**

Reemplazar el cuerpo del receiver por:

```python
@receiver(user_logged_in)
def registrar_primera_activacion(sender, request, user, **kwargs):
    # Una suplantación del staff NO es una activación del alumno: marcarla
    # corrompería la métrica de adopción (ver `tenants/suplantacion.py`).
    if getattr(request, "_suplantacion_en_curso", False):
        return

    try:
        perfil = user.perfil
    except ObjectDoesNotExist:
        return
    ...
```

- [ ] **Step 7: Correr los tests**

```bash
python manage.py test tenants.tests.SuplantacionServicioTests -v 2
```
Esperado: PASS (9 tests).

- [ ] **Step 8: Commit**

```bash
git add tenants/ alumnos/signals.py
git commit -m "feat(tenants): servicio y auditoría de suplantación de alumno"
```

---

### Task 7: Vistas de suplantación, banner y bloqueo de Calendar

**Files:**
- Modify: `tenants/views.py` (agregar `SuplantarView`, `VolverDeSuplantacionView`)
- Modify: `tenants/urls.py`
- Modify: `templates/base.html` (banner)
- Modify: `styles/input.css` (`.banner-suplantacion`) + `npm run build:css`
- Modify: `calendario/views.py` (bloquear conectar/desconectar)
- Modify: `templates/alumnos/acceso_list.html` (descomentar «Entrar como»)
- Test: `tenants/tests.py` (clase nueva `SuplantacionVistasTests`)

**Interfaces:**
- Consumes: `tenants.suplantacion.iniciar/volver/esta_activa/vencida`.
- Produces: rutas `tenants:suplantar` (POST, `<int:pk>` de `Alumno`) y
  `tenants:suplantacion_volver` (POST).

> **`VolverDeSuplantacionView` NO lleva `StaffRequiredMixin`.** Mientras dura la
> suplantación el usuario de la sesión es el ALUMNO, así que exigir rol staff
> haría imposible volver — te dejaría atrapado en la cuenta del alumno.

- [ ] **Step 1: Escribir el test que falla**

```python
class SuplantacionVistasTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gim A", slug="gim-a")
        self.staff = User.objects.create_user("staff", password="clave-larga-123")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Pérez"
        )
        crear_acceso(self.alumno, TIPO_EMAIL, "juan@ejemplo.com")
        self.alumno.refresh_from_db()
        self.client.force_login(self.staff)

    def test_suplantar_y_volver(self):
        response = self.client.post(
            reverse("tenants:suplantar", args=[self.alumno.pk]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volver a mi cuenta")
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), self.alumno.perfil.usuario.pk
        )

        response = self.client.post(
            reverse("tenants:suplantacion_volver"), follow=True
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertNotContains(response, "Volver a mi cuenta")

    def test_mientras_suplanta_no_entra_a_vistas_de_staff(self):
        self.client.post(reverse("tenants:suplantar", args=[self.alumno.pk]))
        self.assertEqual(self.client.get(reverse("alumnos:listado")).status_code, 403)

    def test_aislamiento_alumno_de_otro_gimnasio_da_404(self):
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        ajeno = Alumno.objects.create(
            gimnasio=otro_gim, nombre="Ana", apellido="Gómez"
        )
        crear_acceso(ajeno, TIPO_EMAIL, "ana@ejemplo.com")
        ajeno.refresh_from_db()
        response = self.client.post(reverse("tenants:suplantar", args=[ajeno.pk]))
        self.assertEqual(response.status_code, 404)

    def test_get_no_esta_permitido(self):
        response = self.client.get(reverse("tenants:suplantar", args=[self.alumno.pk]))
        self.assertEqual(response.status_code, 405)

    def test_sesion_fabricada_hacia_staff_de_otro_gimnasio_es_rechazada(self):
        """Alguien que edita la cookie de sesión no debe poder saltar de tenant."""
        otro_gim = Gimnasio.objects.create(nombre="Gim B", slug="gim-b")
        staff_ajeno = User.objects.create_user("staff-b", password="clave-larga-789")
        Perfil.objects.create(
            usuario=staff_ajeno, gimnasio=otro_gim, rol=Perfil.Rol.STAFF
        )
        self.client.post(reverse("tenants:suplantar", args=[self.alumno.pk]))

        sesion = self.client.session
        sesion[suplantacion.CLAVE_SESION]["original_pk"] = staff_ajeno.pk
        sesion.save()

        self.client.post(reverse("tenants:suplantacion_volver"))
        self.assertNotEqual(
            int(self.client.session.get("_auth_user_id", 0)), staff_ajeno.pk
        )

    def test_no_se_puede_conectar_calendar_mientras_suplanta(self):
        """Si no, el staff vincularía SU cuenta de Google al calendario del
        alumno — fuga de privacidad real."""
        self.client.post(reverse("tenants:suplantar", args=[self.alumno.pk]))
        response = self.client.get(reverse("calendario:conectar"))
        self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test tenants.tests.SuplantacionVistasTests -v 2
```
Esperado: `NoReverseMatch: 'suplantar'`.

- [ ] **Step 3: Agregar las vistas**

En `tenants/views.py`:

```python
class SuplantarView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Entrar como un alumno. POST-only: cambia quién sos en la sesión.

    El queryset sale de `TenantScopedMixin`, así que un alumno de otro gimnasio
    da 404 sin llegar al servicio.
    """

    http_method_names = ["post"]

    def get_queryset(self):
        from alumnos.models import Alumno

        return Alumno.objects.for_gimnasio(self.gimnasio)

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        suplantacion.iniciar(request, alumno)
        messages.info(request, f"Estás viendo la app como {alumno}.")
        return redirect("home")


class VolverDeSuplantacionView(LoginRequiredMixin, View):
    """Volver a la cuenta del staff.

    NO usa `StaffRequiredMixin`: durante la suplantación el usuario de la
    sesión es el ALUMNO, así que exigir rol staff dejaría al staff atrapado.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        suplantacion.volver(request)
        messages.success(request, "Volviste a tu cuenta.")
        return redirect("home")
```

Imports a agregar en `tenants/views.py`:

```python
from django.shortcuts import redirect
from django.views import View
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants import suplantacion
```

- [ ] **Step 4: Agregar las rutas**

En `tenants/urls.py`. **Ojo:** este archivo hoy no tiene `app_name`, pero los
tests usan `tenants:suplantar`, así que hay que agregarlo. Eso rompería
`{% url 'home' %}`, `'login'`, etc. en todo el proyecto.

**Decisión: NO agregar `app_name`.** Las dos rutas nuevas van sin namespace,
igual que el resto del archivo:

```python
    path("suplantar/<int:pk>/", SuplantarView.as_view(), name="suplantar"),
    path(
        "suplantar/volver/",
        VolverDeSuplantacionView.as_view(),
        name="suplantacion_volver",
    ),
```

Y en los tests y templates, usar `reverse("suplantar")` y
`reverse("suplantacion_volver")` **sin** el prefijo `tenants:`. Corregir las
referencias del Step 1 y del template de la Task 5 en consecuencia.

- [ ] **Step 5: Banner en `templates/base.html`**

Justo después de la apertura de `<body>` (antes del `<header>`):

```html
    {% if request.session.suplantacion %}
      <div class="banner-suplantacion">
        <span>
          Estás viendo la app como
          <strong>{{ request.session.suplantacion.alumno_nombre }}</strong>.
        </span>
        <form method="post" action="{% url 'suplantacion_volver' %}">
          {% csrf_token %}
          <button type="submit" class="boton">Volver a mi cuenta</button>
        </form>
      </div>
    {% endif %}
```

`django.template.context_processors.request` ya está activo, así que
`request.session` se lee sin context processor nuevo.

- [ ] **Step 6: Estilo y «Entrar como»**

En `styles/input.css`, dentro de `@layer components`:

```css
  .banner-suplantacion {
    @apply flex flex-wrap items-center justify-between gap-3
           bg-amber-100 text-amber-900 px-4 py-2 text-sm
           border-b border-amber-300;
  }
```

Compilar:

```bash
npm run build:css
```

En `templates/alumnos/acceso_list.html`, descomentar el bloque de «Entrar como»
y apuntarlo a `{% url 'suplantar' alumno.pk %}`.

- [ ] **Step 7: Bloquear Calendar durante la suplantación**

En `calendario/views.py`, en `ConectarCalendarioView.get` y
`DesconectarCalendarioView.post`, como primera línea:

```python
        if suplantacion.esta_activa(request):
            raise PermissionDenied(
                "No se puede tocar la conexión de Google Calendar mientras "
                "ves la app como otra persona."
            )
```

Con `from tenants import suplantacion` y
`from django.core.exceptions import PermissionDenied` en los imports.

- [ ] **Step 8: Correr la suite completa**

```bash
python manage.py test
```
Esperado: PASS.

- [ ] **Step 9: Commit**

```bash
git add tenants/ templates/ styles/input.css static/css/app.css calendario/views.py
git commit -m "feat(tenants): entrar como alumno, con banner y vuelta a la cuenta"
```

---

### Task 8: Documentación

**Files:**
- Modify: `CLAUDE.md`
- Modify: `ISSUES.md`
- Modify: `docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md` (marcar
  criterios de salida)

- [ ] **Step 1: Sección nueva en `CLAUDE.md`**

Después de "Portal del alumno y acceso (Fase 3)", agregar "Accesos, revocación y
suplantación (Frente B)" documentando: identificador email/teléfono normalizado
en `alumnos/identidad.py`; contraseña siempre autogenerada y mostrada una sola
vez fuera de `messages`; `Alumno.estado` como espejo de `User.is_active`; el
panel `alumnos:accesos` colgado del listado y no del nav; y las **dos trampas de
`login()`** con el porqué de no usar `signal.disconnect()`.

- [ ] **Step 2: Entradas en `ISSUES.md`**

Una por riesgo aceptado: (a) colisión de identificador entre gimnasios y por qué
el mensaje va genérico; (b) `finalizada_en` en `NULL` si el staff cierra la
pestaña; (c) que se descartó guardar contraseñas legibles, con el razonamiento
completo, para que no se reabra.

- [ ] **Step 3: Marcar los criterios de salida del spec**

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md ISSUES.md docs/
git commit -m "docs: documentar el portal de cuentas (Frente B)"
```

---

## Self-Review

**Cobertura del spec:** identidad → Task 1; contraseña generada y mostrada una
vez → Tasks 2 y 3; revocación → Task 4; panel → Task 5; suplantación y auditoría
→ Tasks 6 y 7; bloqueo de Calendar → Task 7; riesgos documentados → Task 8. Sin
huecos.

**Dependencia circular de templates detectada y resuelta:** el template de la
Task 5 referencia una ruta que recién existe en la Task 7. Se deja comentado en
la Task 5 y se descomenta en la Task 7 Step 6, para que la suite no quede roja
entre tareas.

**Namespace corregido:** el borrador usaba `tenants:suplantar`, pero
`tenants/urls.py` **no tiene `app_name`** — agregarlo rompería `{% url 'home' %}`
y `'login'` en todo el proyecto. Las rutas nuevas van sin namespace.

**Consistencia de nombres:** `crear_acceso`, `regenerar_password`,
`IdentificadorEnUso`, `normalizar_identificador`, `TIPO_EMAIL`, `TIPO_TELEFONO`,
`CLAVE_SESION`, `iniciar`, `volver`, `esta_activa`, `vencida` se usan igual en
todas las tareas.

**Deuda anotada para el Frente C:** `tenants/suplantacion.BACKEND` apunta a
`ModelBackend`; cuando exista `PerfilModelBackend` (y django-axes por delante)
hay que actualizarla o `login()` va a elegir mal el backend.
