# Control de ingreso Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un alumno entre al gimnasio por un molinete con un QR o con un lector facial, y que el gimnasio vea quién entró de verdad.

**Architecture:** La app es la fuente de verdad de quién puede entrar y el registro de qué pasó; el reconocimiento facial vive en el equipo, así que nunca se almacena biometría. Dos endpoints HTTP versionados autenticados por token de dispositivo (reportar/consultar eventos, y bajar el padrón para operar offline), un módulo puro con el árbol de decisión, y pantallas de staff más el QR en el portal del alumno.

**Tech Stack:** Django 5.2, templates + CBV (sin DRF), SQLite en dev / Postgres en prod, `segno` (nuevo) para el QR, `fpdf2` (ya está) para las tarjetas imprimibles.

**Spec:** `docs/superpowers/specs/2026-09-27-control-de-ingreso-design.md`

**Ejecución elegida:** subagentes con revisión — un subagente fresco implementa
cada tarea y otro la revisa antes de que arranque la siguiente, más una revisión
de la rama entera al final (`superpowers:subagent-driven-development`). Se eligió
por el encadenamiento de interfaces entre las tareas 1 a 10, porque se tocan
archivos compartidos (`pagos/acceso.py`, `tenants/demo.py`,
`tenants/exportacion.py`), y porque un error acá es una puerta que deja entrar a
quien no corresponde o que traba el molinete a un socio al día.

**Rama:** `worktree-control-de-ingreso`, en el worktree
`.claude/worktrees/control-de-ingreso`, branchada de `main` en `28c8755`.
Baseline medido antes de empezar: **1723 tests, OK**.
Al aprobar, este plan se guarda como
`docs/superpowers/plans/2026-09-28-control-de-ingreso.md` y se commitea.

## Contexto

Varios establecimientos se lo pidieron al dueño del producto: es retención, no
especulación. Y de paso resuelve un agujero del panel — hoy los tres indicadores
de "asistencia" (`tenants/analitica.py:49`, `:406`, `:437`) cuentan `Reserva`, que
es la *intención* de venir y se borra al cancelar.

No hay hardware todavía, así que lo que se construye es el contrato que cualquier
equipo comercial o un puente puede consumir. **No se construye modo kiosco**
(decisión explícita del dueño): la puerta se ejercita con `curl`, no a mano.

## Global Constraints

- **Nunca guardar ni procesar biometría.** Solo el id que el alumno tiene EN el equipo.
- Todo modelo nuevo hereda `core.models.TenantOwnedModel`; **no se pisa el `related_name`** generado (`%(class)ss`).
- **`verbose_name` y `verbose_name_plural` explícitos en los cuatro modelos**: `core/borrado.py` arma con ellos el aviso de borrado.
- Fechas del dominio con `timezone.localdate()`, **nunca** `timezone.now().date()`.
- El costo en queries **no puede crecer con la cantidad de filas** procesadas, solo con el catálogo.
- `QuerySet.update()` no dispara `auto_now`: si una escritura tiene que dejar `modificado` como estaba, se usa `update()`; si tiene que actualizarlo, va explícito.
- Ningún campo renderizado a mano sin sus errores: `{{ form.x }}` exige `form.x.errors` en el mismo template, o el include `partials/campo_form.html`. Lo barre `core.tests.ErroresDeFormularioVisiblesTests`.
- El nav de staff se queda en **8 ítems**: no se agrega ninguno.
- Ninguna variable de entorno nueva.
- Comandos: `python manage.py test <app>` (no pytest). En el worktree, el intérprete es `/Users/fabriziosola/app_gim/.venv/bin/python`.
- Los bloques de test de este plan muestran el cuerpo, no los imports: cada tarea agrega a su archivo los que use (`TestCase`/`SimpleTestCase`, `IntegrityError`, `ProtectedError`, `ValidationError`, `patch`, `CaptureQueriesContext`, `connection`, los modelos). El molde de imports de un archivo de tests del proyecto es `tenants/tests.py:1-51`.

**Cada modelo `TenantOwnedModel` nuevo entra, en el MISMO commit que lo crea, a
sus cuatro lugares de cobertura** — `tenants/demo.py::vaciar_gimnasio`,
`tenants/tests.py::_ensuciar`, y `HOJAS`/`EXCLUIDOS` de
`tenants/exportacion.py`. Los cuatro tests que lo exigen enumeran con
`apps.get_models()`, así que rompen en cuanto el modelo existe:
`test_ensuciar_cubre_todos_los_modelos_tenant_owned`,
`test_deja_en_cero_todos_los_modelos_tenant_owned`,
`test_todo_modelo_tenant_owned_esta_exportado_o_excluido_a_proposito`,
`test_cada_csv_de_cada_carpeta_tiene_datos`.

## Review Focus

Cinco casos que la spec implica y que ninguna tarea probaría sola. Cada uno tiene
su test asignado a la tarea dueña del código.

1. **El QR de un alumno de OTRO gimnasio leído en este molinete** debe dar `SIN_CREDENCIAL`, nunca abrir. Es aislamiento de tenant en la puerta. → Tarea 6.
2. **`momento` naive (sin zona) o en el futuro** mandado por un lector con el reloj mal: un naive comparado contra un aware explota, y uno futuro ensucia el antipassback y los reportes para siempre. → Tarea 7.
3. **Dos lecturas del mismo alumno dentro del MISMO lote**: el antipassback tiene que verlas entre sí, no solo contra lo que ya está en la base. → Tarea 6.
4. **`valor` con espacios o `\r\n` que agrega el lector**: sin normalizar, la tarjeta buena "no anda" y nadie entiende por qué. → Tarea 6.
5. **`tipo` desconocido en el JSON** (un lector que manda `"rfid"`): 400 con mensaje, nunca un 500. → Tarea 7.

## File Structure

```
ingreso/
  apps.py          IngresoConfig
  models.py        ConfiguracionIngreso, Dispositivo, Credencial, EventoIngreso,
                   obtener_configuracion()
  tokens.py        generar(), hash_de()                    — Django-free
  decision.py      Decision, Contexto, contexto_de(), decidir()
  auth.py          dispositivo_autenticado()               — 401/403 tipados
  limites.py       demasiados_intentos_fallidos(), cupo_diario_excedido()
  services.py      generar_credencial_qr(), generar_credenciales_en_bloque(),
                   registrar_lote(), padron_de()
  qr.py            svg_de()                                — Django-free
  tarjetas.py      generar_pdf_credenciales()              — Django-free
  forms.py         ConfiguracionIngresoForm, DispositivoForm, CredencialFacialForm
  views.py         2 de API + 4 de staff + 1 del portal
  urls.py          app_name = "ingreso"
  management/commands/purgar_ingresos.py
  tests.py  tests_decision.py  tests_api.py  tests_vistas.py
templates/ingreso/  evento_list.html, dispositivo_list.html, dispositivo_form.html,
                    dispositivo_token.html, configuracion_form.html,
                    partials/credencial_alumno.html
docs/integracion-control-de-ingreso.md
scripts/puente_ingreso_ejemplo.py
scripts/probar_ingreso_local.sh
```

`decision.py` no toca la base y `contexto_de` es lo único que consulta: es lo que
mantiene el costo fijo. `tokens.py`, `qr.py` y `tarjetas.py` son Django-free para
poder testearlos con `SimpleTestCase`, mismo criterio que `rutinas/pdf.py`.

---

### Task 1: App `ingreso` + `ConfiguracionIngreso`

**Files:**
- Create: `ingreso/__init__.py`, `ingreso/apps.py`, `ingreso/models.py`, `ingreso/admin.py`, `ingreso/urls.py`, `ingreso/migrations/__init__.py`, `ingreso/tests.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`), `config/urls.py`, `tenants/demo.py`, `tenants/tests.py` (`_ensuciar`), `tenants/exportacion.py` (`HOJAS`)

**Interfaces:**
- Produces: `ingreso.models.ConfiguracionIngreso` con `bloquea_por_deuda`, `exige_reserva`, `minutos_antipassback`; `ingreso.models.obtener_configuracion(gimnasio) -> ConfiguracionIngreso`

- [ ] **Step 1: Write the failing test** en `ingreso/tests.py`

```python
from django.test import TestCase

from ingreso.models import ConfiguracionIngreso, obtener_configuracion
from tenants.models import Gimnasio


class ConfiguracionIngresoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")

    def test_obtener_configuracion_es_idempotente(self):
        """Única vía de acceso, y no puede crear una segunda fila: hay una
        `UniqueConstraint` por gimnasio y el resto del código toma esta fila
        como la configuración del gimnasio."""
        primera = obtener_configuracion(self.gimnasio)
        segunda = obtener_configuracion(self.gimnasio)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(ConfiguracionIngreso.objects.count(), 1)

    def test_los_defaults_no_bloquean_a_nadie(self):
        """Prender el control de ingreso no puede cambiarle el comportamiento a
        un gimnasio que no configuró nada, igual que `dias_tolerancia_pago`."""
        config = obtener_configuracion(self.gimnasio)

        self.assertFalse(config.bloquea_por_deuda)
        self.assertFalse(config.exige_reserva)

    def test_la_configuracion_de_un_gimnasio_no_se_ve_desde_el_otro(self):
        obtener_configuracion(self.gimnasio)

        self.assertFalse(
            ConfiguracionIngreso.objects.for_gimnasio(self.otro).exists()
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test ingreso -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingreso'`

- [ ] **Step 3: Write minimal implementation**

`ingreso/apps.py` (molde `notificaciones/apps.py:1-9`):

```python
from django.apps import AppConfig


class IngresoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ingreso"
    verbose_name = "Control de ingreso"
```

`ingreso/models.py`:

```python
"""Control de ingreso al gimnasio: quién puede pasar por la puerta y qué pasó.

La app NUNCA guarda biometría. El lector facial guarda la plantilla en su
propia memoria e identifica a la persona; acá solo se guarda el id que esa
persona tiene EN el equipo (`Credencial` de tipo FACIAL). Ver
`docs/superpowers/specs/2026-09-27-control-de-ingreso-design.md`.
"""

from django.db import models

from core.models import TenantOwnedModel


class ConfiguracionIngreso(TenantOwnedModel):
    """Qué hace la puerta de ESTE gimnasio. Una fila por gimnasio.

    Modelo propio y no cuatro campos en `Gimnasio` porque
    `Gimnasio.version_media` deriva de `modificado` y versiona la URL del logo,
    la del fondo y la del ícono de la PWA: guardar una preferencia de la puerta
    le haría volver a bajar todo eso a cada celular del gimnasio.
    """

    bloquea_por_deuda = models.BooleanField(
        default=False,
        verbose_name="no dejar entrar al que debe la cuota",
        help_text=(
            "Usa la misma regla que el portal: los días de tolerancia se "
            "configuran en «Mi gimnasio»."
        ),
    )
    exige_reserva = models.BooleanField(
        default=False,
        verbose_name="pedir turno reservado para entrar",
        help_text="Solo para gimnasios que trabajan con cupos estrictos.",
    )
    minutos_antipassback = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="minutos entre dos ingresos de la misma persona",
        help_text="0 lo apaga. Con 2 alcanza para evitar la doble lectura.",
    )

    class Meta:
        verbose_name = "configuración de control de ingreso"
        verbose_name_plural = "configuraciones de control de ingreso"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio"], name="config_ingreso_unica_por_gimnasio"
            )
        ]

    def __str__(self):
        return f"Control de ingreso de {self.gimnasio}"


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config de un gimnasio: garantiza que la fila
    exista. Ojo que `turnos.models` tiene una función con este mismo nombre:
    el módulo que necesite las dos las importa calificadas por módulo."""
    config, _ = ConfiguracionIngreso.objects.get_or_create(gimnasio=gimnasio)
    return config
```

`ingreso/urls.py`:

```python
from django.urls import path

app_name = "ingreso"

urlpatterns = []
```

`ingreso/admin.py`: `admin.site.register(ConfiguracionIngreso)`.

`config/settings.py`: agregar `"ingreso",` a `INSTALLED_APPS` **después** de
`turnos` y `pagos` (lee los dos) y antes de `notificaciones`.

`config/urls.py`: `path("ingreso/", include("ingreso.urls")),`.

- [ ] **Step 4: Crear la migración y correr el test**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py makemigrations ingreso`
Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test ingreso -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Los cuatro lugares de cobertura, que ya están rotos**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test tenants -v 2`
Expected: FAIL en `test_ensuciar_cubre_todos_los_modelos_tenant_owned` y
`test_todo_modelo_tenant_owned_esta_exportado_o_excluido_a_proposito`, que
nombran `ingreso.ConfiguracionIngreso`. **Es el comportamiento diseñado de esos
tests, no un problema del plan.**

En `tenants/tests.py::_ensuciar`, antes del `return alumno`:

```python
    from ingreso.models import ConfiguracionIngreso
    ConfiguracionIngreso.objects.create(gimnasio=gimnasio)
```

En `tenants/demo.py::vaciar_gimnasio`, en el bloque final ("sin relaciones
entrantes"), con su import arriba:

```python
        ConfiguracionIngreso.objects.for_gimnasio(gimnasio).delete()
```

En `tenants/exportacion.py::HOJAS`:

```python
    Hoja(
        "control-de-ingreso", "ingreso.ConfiguracionIngreso", "gimnasio",
        "Cómo está configurada la puerta del gimnasio.",
        (
            ("bloquea_por_deuda", "bloquea_por_deuda"),
            ("exige_reserva", "exige_reserva"),
            ("minutos_antipassback", "minutos_antipassback"),
        ),
    ),
```

- [ ] **Step 6: Run the full suite**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test 2>&1 | grep -E "^Ran|^OK|^FAILED"`
Expected: `OK`, con 3 tests más que el baseline de 1723.

- [ ] **Step 7: Commit**

```bash
git add ingreso config/settings.py config/urls.py tenants/demo.py tenants/tests.py tenants/exportacion.py
git commit -m "feat(ingreso): app nueva y configuracion de la puerta por gimnasio"
```

---

### Task 2: `Dispositivo` y su token

**Files:**
- Create: `ingreso/tokens.py`
- Modify: `ingreso/models.py`, `ingreso/tests.py`, `tenants/demo.py`, `tenants/tests.py`, `tenants/exportacion.py`, `tenants/tests_exportacion.py`

**Interfaces:**
- Produces: `ingreso.tokens.generar() -> str`, `ingreso.tokens.hash_de(token: str) -> str`; `ingreso.models.Dispositivo` con `nombre`, `tipo`, `token_hash`, `activo`, `ultimo_contacto`, y `Dispositivo.Tipo`

- [ ] **Step 1: Write the failing test** — agregar a `ingreso/tests.py`

```python
from django.test import SimpleTestCase

from ingreso import tokens
from ingreso.models import Dispositivo


class TokenDeDispositivoTests(SimpleTestCase):
    """Django-free: la propiedad que importa es criptográfica, no de ORM."""

    def test_cada_token_es_distinto(self):
        self.assertNotEqual(tokens.generar(), tokens.generar())

    def test_el_token_tiene_entropia_suficiente(self):
        """32 bytes de `secrets.token_urlsafe`. Es lo que hace que un `sha256`
        pelado no sea atacable por diccionario y no haga falta PBKDF2, que
        costaría ~100 ms del único worker de gunicorn por request."""
        self.assertGreaterEqual(len(tokens.generar()), 40)

    def test_el_hash_es_estable_y_de_64_caracteres(self):
        token = tokens.generar()

        self.assertEqual(tokens.hash_de(token), tokens.hash_de(token))
        self.assertEqual(len(tokens.hash_de(token)), 64)

    def test_un_token_alterado_da_otro_hash(self):
        token = tokens.generar()

        self.assertNotEqual(tokens.hash_de(token), tokens.hash_de(token + "x"))


class DispositivoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")

    def test_el_token_no_queda_en_claro_en_ninguna_columna(self):
        """Un volcado de la base no puede contener una llave usable de la
        puerta. Se buscan los valores de TODAS las columnas, no solo de la que
        se supone que lo guarda."""
        token = tokens.generar()
        dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta principal",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
        )

        fila = Dispositivo.objects.filter(pk=dispositivo.pk).values().get()

        self.assertNotIn(token, [str(valor) for valor in fila.values()])

    def test_dos_dispositivos_no_pueden_compartir_token(self):
        token = tokens.generar()
        Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta A",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
        )

        with self.assertRaises(IntegrityError):
            Dispositivo.objects.create(
                gimnasio=self.gimnasio, nombre="Puerta B",
                tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(token),
            )
```

(Agregar `from django.db import IntegrityError` a los imports del módulo.)

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test ingreso -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingreso.tokens'`

- [ ] **Step 3: Write minimal implementation**

`ingreso/tokens.py`:

```python
"""El token con el que un lector se autentica contra la app.

Django-free a propósito (`SimpleTestCase`, sin base).

La base guarda SOLO el `sha256` del token, con índice único: se verifica
buscando por ese hash, en una query indexada y en tiempo constante, y un
volcado de la base no contiene ninguna llave usable.

Tres decisiones que conviene no deshacer:

1. **No se guarda en claro** (como sí hace `solicitudes.SolicitudAccesoToken`).
   Ese es un token de un solo uso con vencimiento; éste es una llave de larga
   vida que abre una puerta y baja el padrón entero de alumnos.
2. **No se usa `make_password`/`check_password`.** PBKDF2 cuesta ~100 ms de CPU
   BLOQUEANTE por request, y gunicorn corre con un único worker sync
   (`render.yaml:46`, sin `--workers`). Entre los scans y el poll del padrón son
   miles de requests por día. Con 256 bits de entropía de `secrets`, el
   key-stretching no compra nada: no hay diccionario que atacar.
3. **Sin "pimienta" con `SECRET_KEY`**, aunque `solicitudes/antispam.py:75-79` la
   use para otra cosa: rotar `SECRET_KEY` dejaría sin funcionar, en silencio y de
   golpe, la puerta de todos los gimnasios.
"""

import hashlib
import secrets

BYTES_DE_TOKEN = 32


def generar():
    """Un token nuevo, para mostrarle al staff UNA sola vez."""
    return secrets.token_urlsafe(BYTES_DE_TOKEN)


def hash_de(token):
    """Lo único que se persiste. No lleva sal: la entrada ya es aleatoria."""
    return hashlib.sha256(token.encode()).hexdigest()
```

En `ingreso/models.py`:

```python
class Dispositivo(TenantOwnedModel):
    """Un lector o molinete de la puerta.

    Sin `DeleteView`: se desactiva destildando `activo`, y el `PROTECT` de
    `EventoIngreso.dispositivo` lo respalda -- borrarlo se llevaría el
    historial de asistencia. Mismo criterio que `Ejercicio`.
    """

    class Tipo(models.TextChoices):
        QR = "qr", "Lector de QR"
        FACIAL = "facial", "Lector facial"
        MIXTO = "mixto", "Lee QR y cara"

    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.QR)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    activo = models.BooleanField(default=True)
    ultimo_contacto = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "lector de la puerta"
        verbose_name_plural = "lectores de la puerta"
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "nombre"], name="dispositivo_nombre_unico_por_gim"
            )
        ]

    def __str__(self):
        return self.nombre
```

- [ ] **Step 4: Migración y test**

Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py makemigrations ingreso`
Run: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test ingreso -v 2`
Expected: PASS

- [ ] **Step 5: Los cuatro lugares**

`_ensuciar`, antes del `return alumno`:

```python
    from ingreso import tokens as ingreso_tokens
    from ingreso.models import Dispositivo
    Dispositivo.objects.create(
        gimnasio=gimnasio, nombre=f"Puerta {marca}",
        tipo=Dispositivo.Tipo.QR,
        token_hash=ingreso_tokens.hash_de(f"token-{marca}"),
    )
```

`vaciar_gimnasio`, en el bloque final y **después** de `EventoIngreso` (que
todavía no existe; queda un comentario que lo diga, y la Tarea 4 lo inserta
arriba):

```python
        # Después de los eventos: `EventoIngreso.dispositivo` es PROTECT.
        Dispositivo.objects.for_gimnasio(gimnasio).delete()
```

`HOJAS`, **sin `token_hash`**:

```python
    Hoja(
        "lectores-de-puerta", "ingreso.Dispositivo", "gimnasio",
        "Lectores de la puerta. El token con el que cada uno se autentica no se "
        "exporta: es la llave de la puerta (las contraseñas tampoco se exportan).",
        (
            ("id", "id"), ("nombre", "nombre"), ("tipo", "tipo"),
            ("activo", "activo"), ("ultimo_contacto", "ultimo_contacto"),
            ("creado", "creado"),
        ),
    ),
```

En `tenants/tests_exportacion.py::test_no_sale_ningun_secreto`, sumar la
aserción (siguiendo el comentario que ya está ahí: *"El fixture tiene que tener
de verdad lo que se busca, o esto pasa solo"*):

```python
        hash_del_lector = Dispositivo.objects.get(gimnasio=self.gimnasio).token_hash
        self.assertTrue(hash_del_lector)
        ...
        self.assertNotIn(hash_del_lector, todo)
```

- [ ] **Step 6: Suite completa y commit**

```bash
git add ingreso tenants/demo.py tenants/tests.py tenants/exportacion.py tenants/tests_exportacion.py
git commit -m "feat(ingreso): lector de puerta con token hasheado"
```

---

### Task 3: `Credencial` y cómo nace el QR

**Files:**
- Create: `ingreso/services.py`
- Modify: `ingreso/models.py`, `ingreso/tests.py`, `tenants/demo.py`, `tenants/tests.py`, `tenants/exportacion.py`, `tenants/tests_exportacion.py`

**Interfaces:**
- Produces: `ingreso.models.Credencial` (con `Credencial.Tipo`); `ingreso.services.generar_credencial_qr(alumno) -> Credencial`; `ingreso.services.generar_credenciales_en_bloque(gimnasio) -> int`

- [ ] **Step 1: Write the failing test** — agregar a `ingreso/tests.py`

```python
class CredencialTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.otro = Gimnasio.objects.create(nombre="Gimnasio B", slug="gimnasio-b")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )

    def test_generar_credencial_qr_es_idempotente_y_regenera_el_valor(self):
        """Regenerar es un UPDATE en la misma fila: hay una unicidad
        (gimnasio, alumno, tipo), así que un alumno no puede tener dos QR."""
        primera = services.generar_credencial_qr(self.alumno)
        valor_viejo = primera.valor

        segunda = services.generar_credencial_qr(self.alumno, regenerar=True)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertNotEqual(segunda.valor, valor_viejo)
        self.assertEqual(Credencial.objects.count(), 1)

    def test_un_alumno_no_puede_tener_dos_credenciales_del_mismo_tipo(self):
        services.generar_credencial_qr(self.alumno)

        with self.assertRaises(IntegrityError):
            Credencial.objects.create(
                gimnasio=self.gimnasio, alumno=self.alumno,
                tipo=Credencial.Tipo.QR, valor="otro-valor",
            )

    def test_el_mismo_id_facial_puede_repetirse_entre_gimnasios(self):
        """Los lectores numeran sus usuarios desde 1, así que el id "42" va a
        existir en los dos gimnasios. La unicidad es por gimnasio, no global."""
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="Gomez"
        )
        Credencial.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        Credencial.objects.create(
            gimnasio=self.otro, alumno=ajeno,
            tipo=Credencial.Tipo.FACIAL, valor="42",
        )

        self.assertEqual(Credencial.objects.filter(valor="42").count(), 2)

    def test_no_se_puede_colgar_una_credencial_de_un_alumno_de_otro_gimnasio(self):
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="Gomez"
        )
        credencial = Credencial(
            gimnasio=self.gimnasio, alumno=ajeno,
            tipo=Credencial.Tipo.QR, valor="x",
        )

        with self.assertRaises(ValidationError):
            credencial.clean()

    def test_el_alta_en_bloque_saltea_a_los_que_ya_tienen_y_no_crece_en_queries(self):
        """Un gimnasio con 200 alumnos no puede clickear 200 veces, y el alta
        no puede ser una query por alumno."""
        services.generar_credencial_qr(self.alumno)
        for i in range(4):
            Alumno.objects.create(
                gimnasio=self.gimnasio, nombre=f"A{i}", apellido="X"
            )

        creadas = services.generar_credenciales_en_bloque(self.gimnasio)

        self.assertEqual(creadas, 4)
        self.assertEqual(Credencial.objects.count(), 5)

    def test_el_alta_en_bloque_no_toca_a_los_alumnos_inactivos(self):
        Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Baja", apellido="X",
            estado=Alumno.Estado.INACTIVO,
        )

        services.generar_credenciales_en_bloque(self.gimnasio)

        self.assertEqual(Credencial.objects.count(), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `ImportError: cannot import name 'services'`

- [ ] **Step 3: Write minimal implementation**

En `ingreso/models.py`:

```python
class Credencial(TenantOwnedModel):
    """Lo que identifica al alumno en la puerta.

    `alumno` es CASCADE y no PROTECT: una credencial no significa nada sin su
    alumno, y con PROTECT no se podría borrar nunca a un alumno cargado por
    error que ya pasó una vez por la puerta. `core/borrado.py` la lista sola en
    el aviso, porque lee `_meta.related_objects`.

    Para las FACIAL, `valor` es el id que el alumno tiene EN el equipo -- la
    cara se enrola en el lector y el staff copia ese id acá. Nunca una
    plantilla biométrica.
    """

    class Tipo(models.TextChoices):
        QR = "qr", "Código QR"
        FACIAL = "facial", "Cara registrada en el lector"

    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="credenciales"
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    valor = models.CharField(max_length=64)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "credencial de ingreso"
        verbose_name_plural = "credenciales de ingreso"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "tipo", "valor"],
                name="credencial_valor_unico_por_gim",
            ),
            models.UniqueConstraint(
                fields=["gimnasio", "alumno", "tipo"],
                name="credencial_una_por_alumno_y_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.alumno}"

    def clean(self):
        validar_gimnasio_de(self.gimnasio, alumno=self.alumno)
```

(Import: `from core.models import TenantOwnedModel, validar_gimnasio_de`.)

`ingreso/services.py`:

```python
"""Las escrituras del control de ingreso. Las vistas no escriben."""

from django.db import transaction

from alumnos.models import Alumno
from ingreso import tokens
from ingreso.models import Credencial


def generar_credencial_qr(alumno, *, regenerar=False):
    """El QR del alumno. Fijo: sirve impreso en una tarjeta para el que no
    tiene smartphone. `regenerar=True` lo reemplaza (el viejo deja de servir).

    Regenerar es un UPDATE en la misma fila, así que no queda rastro del
    código anterior: es el costo asumido de la unicidad por alumno.
    """
    credencial, creada = Credencial.objects.get_or_create(
        gimnasio=alumno.gimnasio,
        alumno=alumno,
        tipo=Credencial.Tipo.QR,
        defaults={"valor": tokens.generar()},
    )
    if not creada and regenerar:
        credencial.valor = tokens.generar()
        credencial.activa = True
        credencial.save(update_fields=["valor", "activa", "modificado"])
    return credencial


@transaction.atomic
def generar_credenciales_en_bloque(gimnasio):
    """Le crea el QR a todos los alumnos ACTIVOS que todavía no tienen.

    Un `bulk_create` y no una query por alumno: el gimnasio de 200 alumnos es
    justo el caso que motiva el botón.
    """
    ya_tienen = Credencial.objects.for_gimnasio(gimnasio).filter(
        tipo=Credencial.Tipo.QR
    ).values_list("alumno_id", flat=True)
    faltantes = (
        Alumno.objects.for_gimnasio(gimnasio)
        .filter(estado=Alumno.Estado.ACTIVO)
        .exclude(pk__in=ya_tienen)
    )
    nuevas = [
        Credencial(
            gimnasio=gimnasio, alumno=alumno,
            tipo=Credencial.Tipo.QR, valor=tokens.generar(),
        )
        for alumno in faltantes
    ]
    Credencial.objects.bulk_create(nuevas)
    return len(nuevas)
```

- [ ] **Step 4: Migración y test**

Expected: PASS

- [ ] **Step 5: Los cuatro lugares**

`_ensuciar`, después de crear el `alumno` y antes del `return`:

```python
    from ingreso.models import Credencial
    Credencial.objects.create(
        gimnasio=gimnasio, alumno=alumno,
        tipo=Credencial.Tipo.QR, valor=f"qr-{marca}",
    )
```

`vaciar_gimnasio`: **en el bloque de antes de `alumnos.delete()`**, al lado de
`Reserva`/`Novedad`, con este comentario:

```python
        # `Credencial` cascadea desde `Alumno`, pero se borra explícita por el
        # mismo motivo que `Reserva`: no depender de por dónde entra la cascada.
        Credencial.objects.for_gimnasio(gimnasio).delete()
```

`HOJAS`, **sin la columna `valor`**. Se evaluó exportar el `valor` solo para las
FACIAL (que no es secreto, es el número del alumno en el lector), y se descartó:
`Hoja.columnas` es una lista de lookups y no admite un predicado por fila, así
que habría que agregarle al exportador un filtro por tipo — tocar el exportador
por una columna. La columna `tipo` ya dice si el alumno tiene QR, facial o los
dos, que es lo que el gimnasio necesita saber al migrar.

```python
    Hoja(
        "credenciales-de-ingreso", "ingreso.Credencial", "gimnasio",
        "Con qué entra cada alumno a la puerta. Ni el código QR ni el id del "
        "lector se exportan: son llaves de la puerta, igual que las "
        "contraseñas.",
        (
            ("id", "id"), ("alumno_id", "alumno_id"),
            ("alumno", "alumno__apellido"), ("tipo", "tipo"),
            ("activa", "activa"),
        ),
    ),
```

En `test_no_sale_ningun_secreto`:

```python
        qr = Credencial.objects.get(
            gimnasio=self.gimnasio, tipo=Credencial.Tipo.QR
        ).valor
        self.assertTrue(qr)
        ...
        self.assertNotIn(qr, todo)
```

- [ ] **Step 6: Suite completa y commit**

```bash
git commit -am "feat(ingreso): credencial por alumno, QR fijo y alta en bloque"
```

---

### Task 4: `EventoIngreso`

**Files:**
- Modify: `ingreso/models.py`, `ingreso/tests.py`, `tenants/demo.py`, `tenants/tests.py`, `tenants/exportacion.py`

**Interfaces:**
- Produces: `ingreso.models.EventoIngreso` con `EventoIngreso.Resultado` (`PERMITIDO`, `SIN_CREDENCIAL`, `CREDENCIAL_INACTIVA`, `ALUMNO_INACTIVO`, `DEUDA`, `SIN_RESERVA`, `DUPLICADO`) y `EventoIngreso.Sentido` (`ENTRADA`, `SALIDA`)

- [ ] **Step 1: Write the failing test**

```python
class EventoIngresoTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="Gimnasio A", slug="gimnasio-a")
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de("t"),
        )

    def test_el_momento_no_es_auto_now_add(self):
        """Un lote en diferido trae el momento del equipo: si el campo fuera
        `auto_now_add`, todos los eventos que subió un lector que estuvo sin
        internet quedarían fechados en el instante en que volvió."""
        ayer = timezone.now() - timedelta(days=1)

        evento = EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=ayer,
        )

        self.assertEqual(evento.momento, ayer)

    def test_el_mismo_id_de_evento_del_equipo_no_entra_dos_veces(self):
        """Es la idempotencia del lote: el lector reintenta y no puede duplicar
        el ingreso."""
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now(), id_evento_equipo="8821",
        )

        with self.assertRaises(IntegrityError):
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento=timezone.now(), id_evento_equipo="8821",
            )

    def test_los_eventos_sin_id_del_equipo_no_chocan_entre_si(self):
        """La unicidad es PARCIAL: si no lo fuera, el segundo evento de un
        lector que no manda id reventaría contra el primero."""
        for _ in range(3):
            EventoIngreso.objects.create(
                gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento=timezone.now(), id_evento_equipo="",
            )

        self.assertEqual(EventoIngreso.objects.count(), 3)

    def test_borrar_el_lector_no_puede_borrar_el_historial(self):
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            resultado=EventoIngreso.Resultado.PERMITIDO, momento=timezone.now(),
        )

        with self.assertRaises(ProtectedError):
            self.dispositivo.delete()
```

(Imports: `from django.db.models import ProtectedError`, `from django.utils import timezone`, `from datetime import timedelta`.)

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `ImportError: cannot import name 'EventoIngreso'`

- [ ] **Step 3: Write minimal implementation**

```python
class EventoIngreso(TenantOwnedModel):
    """Cada intento de pasar por la puerta, permitido o rechazado.

    El rechazado se guarda igual: es lo que le dice al dueño que alguien está
    intentando entrar con una tarjeta revocada, y lo que explica un reclamo.

    **El valor leído NO se guarda.** De una credencial desconocida quedan los
    últimos 6 caracteres en `detalle`: alcanza para diagnosticar "esta tarjeta
    no anda" sin duplicar miles de veces la llave de la puerta en una tabla que
    además se exporta.
    """

    class Resultado(models.TextChoices):
        PERMITIDO = "permitido", "Pasó"
        SIN_CREDENCIAL = "sin_credencial", "Credencial no reconocida"
        CREDENCIAL_INACTIVA = "credencial_inactiva", "Credencial dada de baja"
        ALUMNO_INACTIVO = "alumno_inactivo", "Alumno dado de baja"
        DEUDA = "deuda", "Cuota impaga"
        SIN_RESERVA = "sin_reserva", "Sin turno reservado"
        DUPLICADO = "duplicado", "Ya había entrado"

    class Sentido(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SALIDA = "salida", "Salida"

    dispositivo = models.ForeignKey(
        Dispositivo, on_delete=models.PROTECT, related_name="eventos"
    )
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE,
        related_name="eventos_ingreso", null=True, blank=True,
    )
    credencial = models.ForeignKey(
        Credencial, on_delete=models.SET_NULL,
        related_name="eventos", null=True, blank=True,
    )
    resultado = models.CharField(max_length=24, choices=Resultado.choices)
    momento = models.DateTimeField()
    sentido = models.CharField(
        max_length=10, choices=Sentido.choices, default=Sentido.ENTRADA
    )
    en_diferido = models.BooleanField(default=False)
    id_evento_equipo = models.CharField(max_length=64, blank=True)
    detalle = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "ingreso"
        verbose_name_plural = "ingresos"
        ordering = ["-momento", "-id"]
        indexes = [
            models.Index(
                fields=["gimnasio", "-momento", "-id"], name="ingreso_listado_idx"
            ),
            models.Index(
                fields=["gimnasio", "alumno", "-momento"],
                name="ingreso_antipassback_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dispositivo", "id_evento_equipo"],
                condition=~models.Q(id_evento_equipo=""),
                name="ingreso_id_del_equipo_unico",
            )
        ]

    def __str__(self):
        return f"{self.get_resultado_display()} - {self.momento:%d/%m %H:%M}"
```

> El `-id` del primer índice existe para que cubra el `ordering`; el precedente
> es `rutinas/migrations/0012`, con
> `Index(fields=["alumno", "-fecha_inicio", "-id"])`. El `UniqueConstraint`
> parcial es el primero del proyecto (los `condition=` que hay son todos
> `CheckConstraint`), y está verificado: la suite corre sobre SQLite 3.50.4, con
> `supports_partial_indexes = True`.

- [ ] **Step 4: Migración y test** — Expected: PASS

- [ ] **Step 5: Los cuatro lugares**

`_ensuciar`: crear un evento colgado del alumno y del dispositivo, y **uno con
`alumno=None`** (la lectura desconocida), que es el que ejercita el orden de
borrado:

```python
    from ingreso.models import EventoIngreso
    EventoIngreso.objects.create(
        gimnasio=gimnasio, dispositivo=dispositivo, alumno=alumno,
        resultado=EventoIngreso.Resultado.PERMITIDO, momento=timezone.now(),
    )
    EventoIngreso.objects.create(
        gimnasio=gimnasio, dispositivo=dispositivo,
        resultado=EventoIngreso.Resultado.SIN_CREDENCIAL,
        momento=timezone.now(), detalle="…abc123",
    )
```

`vaciar_gimnasio`: **antes** de `Credencial`, en el bloque de antes de
`alumnos.delete()`:

```python
        # Antes que `Credencial` porque `EventoIngreso.credencial` es SET_NULL:
        # al revés se dispara un UPDATE masivo sobre filas que borramos igual.
        # Y explícito porque los eventos con `alumno=None` (las lecturas de
        # credenciales desconocidas) NO cascadean desde `alumnos.delete()`:
        # sobrevivirían protegiendo al `Dispositivo`.
        EventoIngreso.objects.for_gimnasio(gimnasio).delete()
```

`HOJAS` — es el historial de asistencia, la hoja más valiosa:

```python
    Hoja(
        "ingresos", "ingreso.EventoIngreso", "gimnasio",
        "Quién entró, cuándo y por qué puerta. Incluye los intentos "
        "rechazados, con el motivo.",
        (
            ("id", "id"), ("momento", "momento"),
            ("alumno_id", "alumno_id"), ("alumno", "alumno__apellido"),
            ("lector", "dispositivo__nombre"), ("resultado", "resultado"),
            ("sentido", "sentido"), ("en_diferido", "en_diferido"),
            ("detalle", "detalle"),
        ),
    ),
```

- [ ] **Step 6: Suite completa y commit**

```bash
git commit -am "feat(ingreso): registro de cada intento de pasar por la puerta"
```

---

### Task 5: `turnos.services.reserva_vigente`

**Files:**
- Modify: `turnos/services.py`, `turnos/tests.py`

**Interfaces:**
- Consumes: `turnos.services._ahora_local()`, `turnos.models.obtener_configuracion`
- Produces: `turnos.services.reserva_vigente(gimnasio, alumno, ahora=None) -> Reserva | None`; `turnos.services.reservas_vigentes(gimnasio, alumnos, ahora=None) -> dict[int, Reserva]`; `turnos.services.TOLERANCIA_INGRESO`

- [ ] **Step 1: Write the failing test** en `turnos/tests.py`

```python
class ReservaVigenteTests(TestCase):
    """El reloj se congela porque un test de bordes que compare contra la hora
    real pasa o falla según la hora del día en que se corra."""

    # 2026-09-27 22:30 UTC == 19:30 en Buenos Aires.
    MOMENTO = datetime(2026, 9, 27, 22, 30, tzinfo=dt_timezone.utc)

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="P"
        )
        obtener_configuracion(self.gimnasio)  # duracion_minutos = 60

    def _reserva(self, hora):
        return Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=date(2026, 9, 27), hora_inicio=hora,
        )

    def test_una_reserva_en_curso_es_vigente(self):
        reserva = self._reserva(time(19))  # 19:00 a 20:00, y son las 19:30

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            self.assertEqual(
                services.reserva_vigente(self.gimnasio, self.alumno), reserva
            )

    def test_el_que_llega_unos_minutos_antes_entra(self):
        """`TOLERANCIA_INGRESO` de 15 minutos: el alumno que llega 19:30 para
        una clase de 19:45 no puede quedar en la calle."""
        self._reserva(time(19, 45))

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            self.assertIsNotNone(
                services.reserva_vigente(self.gimnasio, self.alumno)
            )

    def test_una_reserva_terminada_no_es_vigente(self):
        self._reserva(time(17))  # 17:00 a 18:00

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            self.assertIsNone(
                services.reserva_vigente(self.gimnasio, self.alumno)
            )

    def test_una_reserva_de_mas_tarde_todavia_no_es_vigente(self):
        self._reserva(time(21))

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            self.assertIsNone(
                services.reserva_vigente(self.gimnasio, self.alumno)
            )

    def test_una_clase_que_cruza_la_medianoche(self):
        """La ventana se arma sobre dos fechas: con un solo `fecha=` la clase
        de 23:30 desaparece a las 00:05, que es justo cuando el alumno sale."""
        Reserva.objects.create(
            gimnasio=self.gimnasio, alumno=self.alumno,
            fecha=date(2026, 9, 27), hora_inicio=time(23, 30),
        )
        medianoche = datetime(2026, 9, 28, 3, 5, tzinfo=dt_timezone.utc)  # 00:05 local

        with patch("django.utils.timezone.now", return_value=medianoche):
            self.assertIsNotNone(
                services.reserva_vigente(self.gimnasio, self.alumno)
            )

    def test_reservas_vigentes_en_bloque_no_crece_en_queries(self):
        alumnos = [self.alumno]
        for i in range(9):
            alumnos.append(
                Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"A{i}", apellido="X"
                )
            )
        for alumno in alumnos:
            Reserva.objects.create(
                gimnasio=self.gimnasio, alumno=alumno,
                fecha=date(2026, 9, 27), hora_inicio=time(19),
            )

        with patch("django.utils.timezone.now", return_value=self.MOMENTO):
            with CaptureQueriesContext(connection) as pocos:
                services.reservas_vigentes(self.gimnasio, alumnos[:2])
            with CaptureQueriesContext(connection) as muchos:
                services.reservas_vigentes(self.gimnasio, alumnos)

        self.assertEqual(len(pocos), len(muchos))
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `AttributeError: module 'turnos.services' has no attribute 'reserva_vigente'`

- [ ] **Step 3: Write minimal implementation** en `turnos/services.py`

```python
TOLERANCIA_INGRESO = timedelta(minutes=15)
"""Cuánto antes del turno se le deja entrar al alumno. Sin esto, el que llega
diez minutos antes queda en la puerta mirando el molinete."""


def _ventana_de_reservas(ahora, duracion_minutos):
    """Las `Q` que matchean una reserva EN CURSO en `ahora`.

    Son dos y no una porque la ventana cruza la medianoche: una clase que
    arranca 23:30 sigue en curso a las 00:05 del día siguiente, y con un solo
    `fecha=` desaparecería justo cuando el alumno sale. Mismo problema y misma
    forma que `notificaciones/management/commands/enviar_recordatorios.py`.
    """
    desde = ahora - timedelta(minutes=duracion_minutos)
    hasta = ahora + TOLERANCIA_INGRESO
    if desde.date() == hasta.date():
        return Q(fecha=hasta.date(), hora_inicio__gt=desde.time(),
                 hora_inicio__lte=hasta.time())
    return (
        Q(fecha=desde.date(), hora_inicio__gt=desde.time())
        | Q(fecha=hasta.date(), hora_inicio__lte=hasta.time())
    )


def reservas_vigentes(gimnasio, alumnos, ahora=None) -> dict:
    """`{alumno_id: Reserva}` de los que tienen un turno en curso AHORA.

    En bloque y en UNA query: la usa la puerta, que puede recibir un lote de
    cien lecturas y no puede pagar una query por alumno.
    """
    ahora = ahora or _ahora_local()
    config = obtener_configuracion(gimnasio)
    ids = [a.pk for a in alumnos]
    reservas = Reserva.objects.filter(
        _ventana_de_reservas(ahora, config.duracion_minutos),
        gimnasio=gimnasio,
        alumno_id__in=ids,
    )
    return {r.alumno_id: r for r in reservas}


def reserva_vigente(gimnasio, alumno, ahora=None):
    """El turno en curso de `alumno`, o `None`."""
    return reservas_vigentes(gimnasio, [alumno], ahora=ahora).get(alumno.pk)
```

(Import: `from django.db.models import Q` si no está.)

- [ ] **Step 4: Run test** — Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add turnos/services.py turnos/tests.py
git commit -m "feat(turnos): saber si un alumno tiene un turno en curso ahora"
```

---

### Task 6: El árbol de decisión

**Files:**
- Create: `ingreso/decision.py`, `ingreso/tests_decision.py`
- Modify: `pagos/acceso.py`, `pagos/tests.py`

**Interfaces:**
- Consumes: `pagos.acceso`, `turnos.services.reservas_vigentes`, `ingreso.models.*`
- Produces: `ingreso.decision.Lectura(tipo, valor, sentido, momento, id_equipo="", en_diferido=False)`, `ingreso.decision.Contexto`, `ingreso.decision.contexto_de(dispositivo, lecturas) -> Contexto`, `ingreso.decision.decidir(lectura, contexto) -> Decision` con `Decision(permitido, resultado, mensaje, alumno, credencial)`, y **`pagos.acceso.bloqueados_entre(gimnasio, alumnos, hoy=None) -> set[int]`**

**Nota de diseño:** esta tarea le agrega a `pagos/acceso.py` la versión en bloque
de su propia regla, en vez de copiarla en `ingreso`:

```python
def bloqueados_entre(gimnasio, alumnos, *, hoy=None):
    """De `alumnos`, los pk de los que hoy están bloqueados por falta de pago.

    La hermana en bloque de `bloqueo_de`, para la puerta: un lote de cien
    lecturas no puede pagar una query por alumno. UNA query agregada con el
    mismo `Exists(OuterRef)` que `contar_bloqueados` -- y acá, igual que allá,
    llamar a `bloqueo_de` por fila es el N+1 que este proyecto ya pagó con un
    502 en producción.
    """
    from django.utils import timezone

    hoy = hoy or timezone.localdate()
    umbral = _umbral(gimnasio, hoy)
    if umbral is None or not alumnos:
        return set()

    bloquea = Cuota.objects.filter(
        alumno=OuterRef("pk"),
        gimnasio=gimnasio,
        estado__in=Cuota.ESTADOS_IMPAGOS,
        periodo_inicio__lte=umbral,
        periodo_inicio__gte=gimnasio.fecha_activacion_bloqueo,
    )
    from alumnos.models import Alumno

    return set(
        Alumno.objects.filter(pk__in=[a.pk for a in alumnos])
        .filter(Exists(bloquea))
        .values_list("pk", flat=True)
    )
```

Su test va en `pagos/tests.py`, al lado de los de `bloqueo_de`: que coincida con
`bloqueo_de` alumno por alumno sobre un conjunto mixto (es la garantía de que no
son dos reglas), que devuelva vacío con la tolerancia apagada, y que su costo en
queries no crezca con la cantidad de alumnos.

- [ ] **Step 1: Write the failing test** en `ingreso/tests_decision.py`

Un test por rama, en orden, más los cuatro que fijan invariantes y los tres del
**Review Focus** (1, 3 y 4):

```python
class DecisionTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a", dias_tolerancia_pago=5,
            fecha_activacion_bloqueo=date(2026, 1, 1),
        )
        self.otro = Gimnasio.objects.create(nombre="B", slug="b")
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.MIXTO, token_hash=tokens.hash_de("t"),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.qr = services.generar_credencial_qr(self.alumno)
        self.config = obtener_configuracion(self.gimnasio)

    def _decidir(self, valor, **kwargs):
        lectura = decision.Lectura(
            tipo="qr", valor=valor,
            sentido=EventoIngreso.Sentido.ENTRADA,
            momento=kwargs.get("momento", timezone.now()),
        )
        contexto = decision.contexto_de(self.dispositivo, [lectura])
        return decision.decidir(lectura, contexto)

    def test_una_credencial_que_no_existe_no_abre(self):
        resultado = self._decidir("no-existe")

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL
        )

    def test_el_qr_de_un_alumno_de_OTRO_gimnasio_no_abre(self):
        """Review Focus 1: aislamiento de tenant EN LA PUERTA. Un QR válido de
        otro gimnasio leído en este molinete no puede abrir."""
        ajeno = Alumno.objects.create(
            gimnasio=self.otro, nombre="Ana", apellido="G"
        )
        credencial_ajena = services.generar_credencial_qr(ajeno)

        resultado = self._decidir(credencial_ajena.valor)

        self.assertFalse(resultado.permitido)
        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_CREDENCIAL
        )

    def test_el_lector_que_agrega_espacios_o_salto_de_linea_igual_reconoce(self):
        """Review Focus 4: un lector que manda "abc\\r\\n" no puede hacer que
        la tarjeta buena "no ande"."""
        resultado = self._decidir(f"  {self.qr.valor}\r\n")

        self.assertTrue(resultado.permitido)

    def test_una_credencial_dada_de_baja_no_abre(self):
        Credencial.objects.filter(pk=self.qr.pk).update(activa=False)

        resultado = self._decidir(self.qr.valor)

        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.CREDENCIAL_INACTIVA
        )

    def test_un_alumno_dado_de_baja_no_abre(self):
        Alumno.objects.filter(pk=self.alumno.pk).update(
            estado=Alumno.Estado.INACTIVO
        )

        resultado = self._decidir(self.qr.valor)

        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.ALUMNO_INACTIVO
        )

    def test_con_el_bloqueo_prendido_el_deudor_no_abre(self):
        ConfiguracionIngreso.objects.filter(pk=self.config.pk).update(
            bloquea_por_deuda=True
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        resultado = self._decidir(self.qr.valor)

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DEUDA)

    def test_con_el_bloqueo_apagado_el_deudor_abre_igual(self):
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_prender_el_bloqueo_sin_tolerancia_cargada_no_bloquea_a_nadie(self):
        """`pagos/acceso.py::_umbral` hace fail-open sin `dias_tolerancia_pago`.
        El interruptor queda muerto y sin síntoma: es la razón de que el
        formulario lo avise en pantalla (Tarea 12)."""
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=None
        )
        ConfiguracionIngreso.objects.filter(pk=self.config.pk).update(
            bloquea_por_deuda=True
        )
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=90),
            estado=Cuota.Estado.VENCIDO,
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_con_reserva_exigida_y_sin_turno_no_abre(self):
        ConfiguracionIngreso.objects.filter(pk=self.config.pk).update(
            exige_reserva=True
        )

        resultado = self._decidir(self.qr.valor)

        self.assertEqual(
            resultado.resultado, EventoIngreso.Resultado.SIN_RESERVA
        )

    def test_el_antipassback_frena_la_doble_lectura(self):
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now(),
        )

        resultado = self._decidir(self.qr.valor)

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_el_antipassback_no_cruza_sentido(self):
        """Una salida no puede trabar el reingreso."""
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now(), sentido=EventoIngreso.Sentido.SALIDA,
        )

        self.assertTrue(self._decidir(self.qr.valor).permitido)

    def test_el_antipassback_se_mide_contra_el_momento_de_la_lectura(self):
        """En un lote en diferido, medir contra `now()` daría cualquier cosa: un
        evento de hace tres horas se compararía contra el instante del upload."""
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now() - timedelta(hours=3),
        )

        resultado = self._decidir(
            self.qr.valor, momento=timezone.now() - timedelta(hours=3, seconds=30)
        )

        self.assertEqual(resultado.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_dos_lecturas_del_mismo_alumno_en_EL_MISMO_lote(self):
        """Review Focus 3: el antipassback tiene que verlas entre sí, no solo
        contra lo que ya está en la base."""
        ahora = timezone.now()
        lecturas = [
            decision.Lectura("qr", self.qr.valor,
                             EventoIngreso.Sentido.ENTRADA, ahora),
            decision.Lectura("qr", self.qr.valor,
                             EventoIngreso.Sentido.ENTRADA,
                             ahora + timedelta(seconds=20)),
        ]
        contexto = decision.contexto_de(self.dispositivo, lecturas)

        primera = decision.decidir(lecturas[0], contexto)
        segunda = decision.decidir(lecturas[1], contexto)

        self.assertTrue(primera.permitido)
        self.assertEqual(segunda.resultado, EventoIngreso.Resultado.DUPLICADO)

    def test_el_mensaje_del_permitido_saluda_por_el_nombre(self):
        self.assertIn("Juan", self._decidir(self.qr.valor).mensaje)

    def test_no_carga_el_historial_viejo_en_memoria(self):
        """El test que el de queries NO puede hacer.

        Sin el piso de fecha, `contexto_de` trae todos los ingresos de todos los
        alumnos del lote. La cantidad de queries es la misma, así que el test de
        escala pasa igual: lo único que delata el bug es que un ingreso viejo
        aparezca en el contexto.
        """
        EventoIngreso.objects.create(
            gimnasio=self.gimnasio, dispositivo=self.dispositivo,
            alumno=self.alumno, credencial=self.qr,
            resultado=EventoIngreso.Resultado.PERMITIDO,
            momento=timezone.now() - timedelta(days=700),
        )
        lectura = decision.Lectura(
            "qr", self.qr.valor, EventoIngreso.Sentido.ENTRADA, timezone.now()
        )

        contexto = decision.contexto_de(self.dispositivo, [lectura])

        self.assertEqual(contexto.ultimo_permitido, {})

    def test_contexto_de_no_crece_en_queries_con_el_tamano_del_lote(self):
        """La regla más dura del proyecto: el costo depende del catálogo, no de
        la cantidad de filas. 5 queries por evento × 100 eventos es el patrón
        que ya causó un 502 con el importador."""
        def queries(cantidad):
            lecturas = [
                decision.Lectura("qr", self.qr.valor,
                                 EventoIngreso.Sentido.ENTRADA, timezone.now())
                for _ in range(cantidad)
            ]
            with CaptureQueriesContext(connection) as ctx:
                decision.contexto_de(self.dispositivo, lecturas)
            return len(ctx)

        self.assertEqual(queries(10), queries(40))
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `ModuleNotFoundError: No module named 'ingreso.decision'`

- [ ] **Step 3: Write minimal implementation** — `ingreso/decision.py`

```python
"""El único lugar donde se decide si la puerta abre.

Molde `pagos/acceso.py`: un módulo, resultado en `dataclass(frozen=True)`, y un
docstring con lo que no hay que deshacer.

Tres decisiones que conviene no deshacer:

1. **`decidir` no toca la base.** `contexto_de` resuelve todo el lote de una
   vez, con un número FIJO de queries traiga una lectura o cien. La alternativa
   obvia (una query por evento) es el patrón que ya causó un 502 en producción
   con el importador. Por eso `contexto` no tiene default: un `contexto=None`
   "que se lo arma solo" es la puerta por la que volvería a entrar.

2. **El antipassback va ÚLTIMO.** Significa "podés entrar pero ya entraste".
   Adelantarlo haría que el segundo scan de un deudor diga "ya pasaste" en vez
   de "tenés una cuota vencida".

3. **`estado_cuenta` del gimnasio NO figura acá.** Que un gimnasio le deba la
   app al dueño del producto no le cierra la puerta a sus alumnos: dejar
   ochenta personas afuera de un edificio no es una palanca de cobro. La
   invariante se fija en la vista, con su test.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from alumnos.models import Alumno
from ingreso.models import Credencial, EventoIngreso, obtener_configuracion


@dataclass(frozen=True)
class Lectura:
    """Lo que el lector dice que leyó.

    `id_equipo` es el id que el evento tiene EN el equipo (la idempotencia del
    lote) y `en_diferido` dice que el equipo ya abrió por su cuenta con el
    padrón cacheado. Los dos viajan acá y no en un parámetro aparte de
    `registrar_lote`: con un `set` de ids paralelo, una lectura sin id matchearía
    por el string vacío.
    """

    tipo: str
    valor: str
    sentido: str
    momento: object
    id_equipo: str = ""
    en_diferido: bool = False

    @property
    def valor_normalizado(self):
        """El lector puede agregar espacios o un `\\r\\n`. Sin esto, la tarjeta
        buena "no anda" y nadie entiende por qué."""
        return self.valor.strip()


@dataclass(frozen=True)
class Decision:
    permitido: bool
    resultado: str
    mensaje: str
    alumno: object = None
    credencial: object = None


@dataclass
class Contexto:
    """Todo lo que la decisión necesita, ya traído de la base."""

    config: object
    credenciales: dict            # valor normalizado -> Credencial
    bloqueados: set               # alumno_id de los que deben
    reservas: dict                # alumno_id -> Reserva
    ultimo_permitido: dict        # (alumno_id, sentido) -> momento
    #: Lo que este mismo lote ya dio por permitido, para que el antipassback
    #: vea dos lecturas del mismo alumno dentro del mismo request.
    concedidos: dict = field(default_factory=dict)


def contexto_de(dispositivo, lecturas):
    """La ÚNICA función de este módulo que consulta la base."""
    from pagos import acceso
    from turnos import services as turnos_services

    gimnasio = dispositivo.gimnasio
    config = obtener_configuracion(gimnasio)

    valores = {l.valor_normalizado for l in lecturas}
    credenciales = {
        c.valor: c
        for c in Credencial.objects.for_gimnasio(gimnasio)
        .filter(valor__in=valores)
        .select_related("alumno")
    }
    alumnos = [c.alumno for c in credenciales.values()]

    # La regla de deuda NO se reescribe acá: `pagos/acceso.py` dice en su
    # docstring que es "el único lugar donde se decide si un alumno está
    # bloqueado por falta de pago", y copiar su `Exists` sería la segunda copia
    # que ese módulo existe para evitar. Por eso la Tarea 6 le agrega
    # `bloqueados_entre`, que es su versión en bloque.
    bloqueados = set()
    if config.bloquea_por_deuda and alumnos:
        bloqueados = acceso.bloqueados_entre(gimnasio, alumnos)

    reservas = {}
    if config.exige_reserva and alumnos:
        reservas = turnos_services.reservas_vigentes(gimnasio, alumnos)

    ultimo_permitido = {}
    if config.minutos_antipassback and alumnos:
        # **El piso de fecha es obligatorio, no una optimización.** Sin él esta
        # query trae el historial ENTERO de cada alumno del lote: un socio con
        # dos años de ingresos son ~500 filas, y cien de ellos en un lote son
        # 50.000 filas a memoria en cada scan. La cantidad de QUERIES sería la
        # misma, así que un test que solo cuenta queries pasa con el bug puesto
        # -- exactamente la trampa que este proyecto ya pagó. Lo que se mide es
        # el piso, con su propio test.
        piso = min(l.momento for l in lecturas) - timedelta(
            minutes=config.minutos_antipassback
        )
        previos = (
            EventoIngreso.objects.for_gimnasio(gimnasio)
            .filter(
                alumno__in=alumnos,
                resultado=EventoIngreso.Resultado.PERMITIDO,
                momento__gte=piso,
            )
            .order_by("momento")
            .values_list("alumno_id", "sentido", "momento")
        )
        # Ordenado por momento, así que la última asignación gana y no hace
        # falta comparar a mano.
        for alumno_id, sentido, momento in previos:
            ultimo_permitido[(alumno_id, sentido)] = momento

    return Contexto(
        config=config, credenciales=credenciales, bloqueados=bloqueados,
        reservas=reservas, ultimo_permitido=ultimo_permitido,
    )


def decidir(lectura, contexto):
    """Decide sobre lo que `contexto_de` ya trajo: **no toca la base**.

    No es pura, y la impureza es deliberada: al conceder un ingreso anota el
    momento en `contexto.concedidos`, que es lo que le permite al antipassback
    ver dos lecturas del mismo alumno dentro del MISMO lote. Consecuencia para
    quien la llame: **las lecturas de un lote se recorren en orden**, y el mismo
    `contexto` se reusa para todas.
    """
    credencial = contexto.credenciales.get(lectura.valor_normalizado)
    if credencial is None:
        return Decision(
            False, EventoIngreso.Resultado.SIN_CREDENCIAL,
            "Credencial no reconocida. Pasá por el mostrador.",
        )
    if not credencial.activa:
        return Decision(
            False, EventoIngreso.Resultado.CREDENCIAL_INACTIVA,
            "Esa credencial está dada de baja.",
            credencial.alumno, credencial,
        )

    alumno = credencial.alumno
    if alumno.estado != Alumno.Estado.ACTIVO:
        return Decision(
            False, EventoIngreso.Resultado.ALUMNO_INACTIVO,
            "Tu cuenta está dada de baja. Hablá con el gimnasio.",
            alumno, credencial,
        )
    if alumno.pk in contexto.bloqueados:
        return Decision(
            False, EventoIngreso.Resultado.DEUDA,
            "Tenés una cuota sin pagar. Pasá por el mostrador.",
            alumno, credencial,
        )
    if contexto.config.exige_reserva and alumno.pk not in contexto.reservas:
        return Decision(
            False, EventoIngreso.Resultado.SIN_RESERVA,
            "No tenés turno reservado para este horario.",
            alumno, credencial,
        )

    minutos = contexto.config.minutos_antipassback
    if minutos:
        clave = (alumno.pk, lectura.sentido)
        previo = max(
            filter(None, [
                contexto.ultimo_permitido.get(clave),
                contexto.concedidos.get(clave),
            ]),
            default=None,
        )
        if previo is not None and abs(
            (lectura.momento - previo).total_seconds()
        ) < minutos * 60:
            return Decision(
                False, EventoIngreso.Resultado.DUPLICADO,
                "Ya registramos tu ingreso hace un momento.",
                alumno, credencial,
            )
        contexto.concedidos[clave] = lectura.momento

    return Decision(
        True, EventoIngreso.Resultado.PERMITIDO,
        f"¡Hola, {alumno.nombre}! Pasá.", alumno, credencial,
    )
```

- [ ] **Step 4: Run test** — Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add ingreso/decision.py ingreso/tests_decision.py
git commit -m "feat(ingreso): arbol de decision de la puerta, con costo fijo en queries"
```

---

### Task 7: Autenticación del lector y `POST /ingreso/v1/eventos/`

**Files:**
- Create: `ingreso/auth.py`, `ingreso/tests_api.py`
- Modify: `ingreso/views.py`, `ingreso/urls.py`, `ingreso/services.py`

**Interfaces:**
- Consumes: `ingreso.decision.contexto_de/decidir`, `ingreso.tokens.hash_de`
- Produces: `ingreso.auth.dispositivo_autenticado(request) -> Dispositivo` (levanta `SinToken`/`LectorDesactivado`); `ingreso.services.registrar_lote(dispositivo, lecturas) -> list[dict]`; ruta `ingreso:api_eventos`

- [ ] **Step 1: Write the failing test** en `ingreso/tests_api.py`

```python
class EventosApiTests(TestCase):
    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(nombre="A", slug="a")
        self.token = tokens.generar()
        self.dispositivo = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de(self.token),
        )
        self.alumno = Alumno.objects.create(
            gimnasio=self.gimnasio, nombre="Juan", apellido="Perez"
        )
        self.qr = services.generar_credencial_qr(self.alumno)
        self.url = reverse("ingreso:api_eventos")

    def _post(self, cuerpo, token=None):
        cabeceras = {}
        if token is not None:
            cabeceras["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            self.url, data=json.dumps(cuerpo),
            content_type="application/json", **cabeceras
        )

    def test_sin_token_da_401(self):
        self.assertEqual(self._post({"eventos": []}).status_code, 401)

    def test_con_un_token_alterado_da_401(self):
        respuesta = self._post({"eventos": []}, token=self.token + "x")

        self.assertEqual(respuesta.status_code, 401)

    def test_una_sesion_de_staff_no_alcanza(self):
        """La vista ignora `request.user` por completo: es lo que hace seguro
        el `csrf_exempt`."""
        staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )
        self.client.login(username="staff-a", password="clave-123456")

        self.assertEqual(self._post({"eventos": []}).status_code, 401)

    def test_un_lector_desactivado_da_403(self):
        Dispositivo.objects.filter(pk=self.dispositivo.pk).update(activo=False)

        respuesta = self._post({"eventos": []}, token=self.token)

        self.assertEqual(respuesta.status_code, 403)

    def test_csrf_no_lo_bloquea(self):
        """Sin sesión ni cookie no hay nada que CSRF pueda proteger, y con el
        middleware prendido el endpoint sería imposible de llamar."""
        cliente = Client(enforce_csrf_checks=True)

        respuesta = cliente.post(
            self.url, data=json.dumps({"eventos": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_una_lectura_valida_abre_y_queda_registrada(self):
        respuesta = self._post(
            {"eventos": [{"tipo": "qr", "valor": self.qr.valor}]},
            token=self.token,
        )

        self.assertEqual(respuesta.status_code, 200)
        resultado = respuesta.json()["resultados"][0]
        self.assertTrue(resultado["permitido"])
        self.assertEqual(resultado["resultado"], "permitido")
        self.assertIn("Juan", resultado["mensaje"])
        self.assertEqual(EventoIngreso.objects.count(), 1)

    def test_un_lote_en_diferido_queda_marcado(self):
        """El equipo ya abrió con su padrón cacheado. Se registra igual qué
        habría opinado la app, así el reporte no miente."""
        respuesta = self._post(
            {"eventos": [{
                "tipo": "qr", "valor": self.qr.valor,
                "abierto_por_el_equipo": True, "id_evento_equipo": "1",
            }]},
            token=self.token,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(EventoIngreso.objects.get().en_diferido)

    def test_el_mismo_id_del_equipo_no_duplica_el_ingreso(self):
        cuerpo = {"eventos": [{
            "tipo": "qr", "valor": self.qr.valor, "id_evento_equipo": "8821",
        }]}

        self._post(cuerpo, token=self.token)
        segunda = self._post(cuerpo, token=self.token)

        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(EventoIngreso.objects.count(), 1)

    def test_un_tipo_desconocido_da_400_y_no_un_500(self):
        """Review Focus 5: un lector que manda "rfid"."""
        respuesta = self._post(
            {"eventos": [{"tipo": "rfid", "valor": "x"}]}, token=self.token
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_momento_sin_zona_horaria_no_revienta(self):
        """Review Focus 2: comparar un naive contra un aware explota."""
        respuesta = self._post(
            {"eventos": [{
                "tipo": "qr", "valor": self.qr.valor,
                "momento": "2026-09-27T19:03:11",
            }]},
            token=self.token,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNotNone(EventoIngreso.objects.get().momento.tzinfo)

    def test_un_momento_en_el_futuro_se_rechaza(self):
        """Review Focus 2: el lector con el reloj mal ensuciaría el
        antipassback y los reportes para siempre."""
        futuro = (timezone.now() + timedelta(days=2)).isoformat()

        respuesta = self._post(
            {"eventos": [{
                "tipo": "qr", "valor": self.qr.valor, "momento": futuro,
            }]},
            token=self.token,
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_un_lote_sobre_el_tope_da_400(self):
        eventos = [
            {"tipo": "qr", "valor": self.qr.valor}
            for _ in range(services.MAX_EVENTOS_POR_LOTE + 1)
        ]

        respuesta = self._post({"eventos": eventos}, token=self.token)

        self.assertEqual(respuesta.status_code, 400)

    def test_un_cuerpo_que_no_es_json_da_400(self):
        respuesta = self.client.post(
            self.url, data="no soy json", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_el_ultimo_contacto_se_actualiza_sin_tocar_modificado(self):
        modificado_antes = self.dispositivo.modificado

        self._post({"eventos": []}, token=self.token)

        self.dispositivo.refresh_from_db()
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)
        self.assertEqual(self.dispositivo.modificado, modificado_antes)

    def test_el_costo_no_crece_con_el_tamano_del_lote(self):
        def queries(cantidad):
            EventoIngreso.objects.all().delete()
            eventos = [
                {"tipo": "qr", "valor": self.qr.valor,
                 "id_evento_equipo": str(i)}
                for i in range(cantidad)
            ]
            with CaptureQueriesContext(connection) as ctx:
                self._post({"eventos": eventos}, token=self.token)
            return len(ctx)

        queries(1)  # request en frío: descarta costos de primera vez
        self.assertLess(queries(40), queries(10) + 10)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `NoReverseMatch: 'api_eventos' not found`

- [ ] **Step 3: Write minimal implementation**

`ingreso/auth.py`:

```python
"""Autenticación del lector de la puerta.

No usa sesión ni cookie: el lector manda `Authorization: Bearer <token>` y se
lo busca por el `sha256` de ese token, con índice único.
"""

from ingreso import tokens
from ingreso.models import Dispositivo


class ErrorDeAutenticacion(Exception):
    """Base. El status lo elige la vista."""


class SinToken(ErrorDeAutenticacion):
    """401. No se distingue "no hay token" de "el token no matchea": mismo
    criterio anti-enumeración que el resto del proyecto."""


class LectorDesactivado(ErrorDeAutenticacion):
    """403. Solo alcanzable con un token VÁLIDO, así que no le revela nada a
    quien no tiene la llave, y sí le dice al instalador lo que necesita."""


def dispositivo_autenticado(request):
    cabecera = request.META.get("HTTP_AUTHORIZATION", "")
    if not cabecera.startswith("Bearer "):
        raise SinToken()
    presentado = cabecera.removeprefix("Bearer ").strip()
    if not presentado:
        raise SinToken()
    try:
        dispositivo = Dispositivo.objects.select_related("gimnasio").get(
            token_hash=tokens.hash_de(presentado)
        )
    except Dispositivo.DoesNotExist:
        raise SinToken() from None
    if not dispositivo.activo:
        raise LectorDesactivado()
    return dispositivo
```

En `ingreso/services.py`, el registro del lote (una transacción,
`bulk_create` con `ignore_conflicts`):

```python
MAX_EVENTOS_POR_LOTE = 100
"""Tope del lote. Con `contexto_de` el costo son unas pocas queries fijas más
un `bulk_create`, así que el tope es por el tamaño del body y por los 30 s de
gunicorn, no por el costo por evento."""

HORAS_DE_ADELANTO_TOLERADAS = 1
"""Un lector con el reloj adelantado fecha eventos en el futuro, y eso ensucia
el antipassback y los reportes para siempre. Una hora cubre un desfasaje de
zona horaria mal configurada sin aceptar un evento de la semana que viene."""


class LecturaInvalida(ValueError):
    """Lo que la vista traduce a 400."""


@transaction.atomic
def registrar_lote(dispositivo, lecturas):
    """Decide y registra. Devuelve un dict por lectura, en el mismo orden.

    Los `id_evento_equipo` que ya existen NO se vuelven a decidir: se devuelve
    el veredicto guardado. Es la idempotencia que necesita un lector que
    reintenta el upload.
    """
    from ingreso.decision import contexto_de, decidir

    gimnasio = dispositivo.gimnasio
    ids = [l_id for l_id in (lec.id_equipo for lec in lecturas) if l_id]
    ya_estaban = {
        e.id_evento_equipo: e
        for e in EventoIngreso.objects.filter(
            dispositivo=dispositivo, id_evento_equipo__in=ids
        )
    }

    contexto = contexto_de(dispositivo, lecturas)
    respuestas, a_crear = [], []
    for lectura in lecturas:
        previo = ya_estaban.get(lectura.id_equipo) if lectura.id_equipo else None
        if previo is not None:
            respuestas.append({
                "id_evento_equipo": previo.id_evento_equipo,
                "permitido": previo.resultado == EventoIngreso.Resultado.PERMITIDO,
                "resultado": previo.resultado,
                "mensaje": "",
                "alumno": str(previo.alumno) if previo.alumno_id else None,
            })
            continue
        veredicto = decidir(lectura, contexto)
        a_crear.append(EventoIngreso(
            gimnasio=gimnasio, dispositivo=dispositivo,
            alumno=veredicto.alumno, credencial=veredicto.credencial,
            resultado=veredicto.resultado, momento=lectura.momento,
            sentido=lectura.sentido,
            en_diferido=lectura.en_diferido,
            id_evento_equipo=lectura.id_equipo,
            detalle=(
                f"…{lectura.valor_normalizado[-6:]}"
                if veredicto.credencial is None else ""
            ),
        ))
        respuestas.append({
            "id_evento_equipo": lectura.id_equipo or None,
            "permitido": veredicto.permitido,
            "resultado": veredicto.resultado,
            "mensaje": veredicto.mensaje,
            "alumno": str(veredicto.alumno) if veredicto.alumno else None,
        })

    # `ignore_conflicts` cubre la carrera del reintento simultáneo que el
    # chequeo de arriba no ve. Ojo: no puebla los pk ni dice qué insertó, así
    # que la respuesta sale de `decidir()` y nunca de lo que devuelve esto.
    EventoIngreso.objects.bulk_create(a_crear, ignore_conflicts=True)
    return respuestas
```

`ingreso/views.py` — la vista, con el parseo defensivo del molde de
`notificaciones/views.py:137-143`:

```python
@method_decorator(csrf_exempt, name="dispatch")
class EventosApiView(View):
    """Lo que el molinete llama en cada lectura, y lo que le sube el lote
    cuando estuvo sin internet.

    `csrf_exempt` es el primero del proyecto y está acotado a esto: CSRF
    protege una sesión de navegador de ser montada por otro sitio, y acá no hay
    sesión ni cookie -- se autentica con un header que un form cross-site no
    puede setear. La vista **ignora `request.user` por completo**, y hay un test
    que lo fija.
    """

    http_method_names = ["post"]

    def post(self, request):
        try:
            dispositivo = auth.dispositivo_autenticado(request)
        except auth.LectorDesactivado:
            return JsonResponse({"error": "lector desactivado"}, status=403)
        except auth.ErrorDeAutenticacion:
            return JsonResponse({"error": "token invalido"}, status=401)

        Dispositivo.objects.filter(pk=dispositivo.pk).update(
            ultimo_contacto=timezone.now()
        )

        try:
            lecturas = _parsear(request.body)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return JsonResponse({"error": str(exc) or "cuerpo invalido"}, status=400)

        resultados = services.registrar_lote(dispositivo, lecturas)
        return JsonResponse({"resultados": resultados})
```

`_parsear(cuerpo) -> list[Lectura]` valida el tope, los tipos conocidos y el
`momento`, levantando `LecturaInvalida` (subclase de `ValueError`, así que la
cae el `except` de la vista):

```python
def _parsear(cuerpo):
    datos = json.loads(cuerpo)
    eventos = datos["eventos"]
    if not isinstance(eventos, list):
        raise LecturaInvalida("«eventos» tiene que ser una lista")
    if len(eventos) > services.MAX_EVENTOS_POR_LOTE:
        raise LecturaInvalida(
            f"máximo {services.MAX_EVENTOS_POR_LOTE} eventos por request"
        )

    tope = timezone.now() + timedelta(hours=services.HORAS_DE_ADELANTO_TOLERADAS)
    lecturas = []
    for evento in eventos:
        tipo = evento["tipo"]
        if tipo not in Credencial.Tipo.values:
            raise LecturaInvalida(f"tipo desconocido: {tipo!r}")
        sentido = evento.get("sentido", EventoIngreso.Sentido.ENTRADA)
        if sentido not in EventoIngreso.Sentido.values:
            raise LecturaInvalida(f"sentido desconocido: {sentido!r}")

        crudo = evento.get("momento")
        if crudo:
            momento = parse_datetime(crudo)
            if momento is None:
                raise LecturaInvalida(f"momento ilegible: {crudo!r}")
            # Un lector puede mandar la fecha sin zona. Comparar un naive contra
            # un aware explota, así que se le pone la local -- que es la del
            # gimnasio, no UTC.
            if timezone.is_naive(momento):
                momento = timezone.make_aware(momento)
            # Y con el reloj adelantado ensuciaría el antipassback y los
            # reportes para siempre.
            if momento > tope:
                raise LecturaInvalida("momento en el futuro: revisá el reloj del lector")
        else:
            momento = timezone.now()

        lecturas.append(decision.Lectura(
            tipo=tipo, valor=evento["valor"], sentido=sentido, momento=momento,
            id_equipo=str(evento.get("id_evento_equipo") or ""),
            en_diferido=bool(evento.get("abierto_por_el_equipo")),
        ))
    return lecturas
```

`ingreso/urls.py`: `path("v1/eventos/", EventosApiView.as_view(), name="api_eventos")`.

- [ ] **Step 4: Run test** — Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(ingreso): endpoint de eventos de la puerta, idempotente y con costo fijo"
```

---

### Task 8: `GET /ingreso/v1/padron/`

**Files:** Modify `ingreso/views.py`, `ingreso/urls.py`, `ingreso/services.py`, `ingreso/tests_api.py`

**Interfaces:**
- Produces: `ingreso.services.padron_de(gimnasio) -> dict`; ruta `ingreso:api_padron`

- [ ] **Step 1: Write the failing test**

```python
class PadronApiTests(TestCase):
    # setUp igual al de EventosApiTests, más self.url = reverse("ingreso:api_padron")

    def _get(self, token=None, **extra):
        cabeceras = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(self.url, **cabeceras, **extra)

    def test_sin_token_da_401(self):
        self.assertEqual(self._get().status_code, 401)

    def test_trae_a_los_alumnos_con_su_credencial(self):
        datos = self._get(token=self.token).json()

        self.assertEqual(len(datos["alumnos"]), 1)
        self.assertEqual(datos["alumnos"][0]["qr"], self.qr.valor)
        self.assertTrue(datos["alumnos"][0]["habilitado"])

    def test_declara_las_reglas_que_no_puede_aplicar_offline(self):
        """El equipo con el padrón cacheado no puede exigir reserva ni aplicar
        antipassback, y la app lo dice en vez de prometerlo."""
        datos = self._get(token=self.token).json()

        self.assertIn("reserva", datos["reglas_no_incluidas"])
        self.assertIn("antipassback", datos["reglas_no_incluidas"])

    def test_el_deudor_viaja_como_no_habilitado(self):
        Gimnasio.objects.filter(pk=self.gimnasio.pk).update(
            dias_tolerancia_pago=5, fecha_activacion_bloqueo=date(2026, 1, 1)
        )
        ConfiguracionIngreso.objects.filter(
            gimnasio=self.gimnasio
        ).update(bloquea_por_deuda=True)
        crear_cuota(
            gimnasio=self.gimnasio, alumno=self.alumno,
            inicio=timezone.localdate() - timedelta(days=30),
            estado=Cuota.Estado.VENCIDO,
        )

        datos = self._get(token=self.token).json()

        self.assertFalse(datos["alumnos"][0]["habilitado"])

    def test_un_padron_sin_cambios_responde_304(self):
        primera = self._get(token=self.token)

        segunda = self._get(
            token=self.token, HTTP_IF_NONE_MATCH=primera["ETag"]
        )

        self.assertEqual(segunda.status_code, 304)

    def test_el_padron_de_un_gimnasio_no_incluye_alumnos_del_otro(self):
        otro = Gimnasio.objects.create(nombre="B", slug="b")
        ajeno = Alumno.objects.create(gimnasio=otro, nombre="Ana", apellido="G")
        services.generar_credencial_qr(ajeno)

        datos = self._get(token=self.token).json()

        self.assertEqual(len(datos["alumnos"]), 1)

    def test_el_costo_no_crece_con_la_cantidad_de_alumnos(self):
        """`habilitado` se anota con el `Exists(OuterRef)` de
        `contar_bloqueados`, nunca con `bloqueo_de` por fila."""
        def queries(cantidad):
            Credencial.objects.all().delete()
            Alumno.objects.for_gimnasio(self.gimnasio).exclude(
                pk=self.alumno.pk
            ).delete()
            for i in range(cantidad - 1):
                Alumno.objects.create(
                    gimnasio=self.gimnasio, nombre=f"A{i}", apellido="X"
                )
            services.generar_credenciales_en_bloque(self.gimnasio)
            with CaptureQueriesContext(connection) as ctx:
                self._get(token=self.token)
            return len(ctx)

        queries(5)
        self.assertEqual(queries(5), queries(40))
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `NoReverseMatch`

- [ ] **Step 3: Write minimal implementation**

En `ingreso/services.py`:

```python
REGLAS_NO_INCLUIDAS = ["reserva", "antipassback"]
"""Lo que el equipo NO puede aplicar con el padrón cacheado, declarado en la
respuesta. Son momentáneas: dependen de la hora exacta y de los eventos que el
equipo todavía no subió, así que no se pueden precomputar. La pantalla de
configuración dice lo mismo al lado de los dos checkboxes."""


def padron_de(gimnasio):
    """Quién puede entrar, para que el lector funcione sin internet.

    **Dos queries fijas, no una por alumno**: las credenciales con
    `select_related`, y el conjunto de deudores con `acceso.bloqueados_entre`.
    Llamar a `bloqueo_de` por fila acá es el N+1 que este proyecto ya pagó con
    un 502 -- y es peor que en una pantalla, porque el lector puede pollear esto
    cada minuto.
    """
    from pagos import acceso

    config = obtener_configuracion(gimnasio)
    credenciales = list(
        Credencial.objects.for_gimnasio(gimnasio)
        .filter(activa=True)
        .select_related("alumno")
        .order_by("alumno__apellido", "alumno__nombre", "tipo")
    )
    alumnos = {c.alumno.pk: c.alumno for c in credenciales}.values()
    bloqueados = (
        acceso.bloqueados_entre(gimnasio, list(alumnos))
        if config.bloquea_por_deuda else set()
    )

    por_alumno = {}
    for credencial in credenciales:
        alumno = credencial.alumno
        # El nombre es para la pantallita del lector, así que va corto. La
        # inicial solo se agrega si hay apellido: con `f"{nombre} {apellido[:1]}."`
        # a secas, un alumno sin apellido cargado sale como "Juan .".
        inicial = f" {alumno.apellido[0]}." if alumno.apellido else ""
        fila = por_alumno.setdefault(alumno.pk, {
            "id": alumno.pk,
            "nombre": f"{alumno.nombre}{inicial}",
            "habilitado": (
                alumno.estado == Alumno.Estado.ACTIVO
                and alumno.pk not in bloqueados
            ),
            "qr": None,
            "id_facial": None,
        })
        clave = "qr" if credencial.tipo == Credencial.Tipo.QR else "id_facial"
        fila[clave] = credencial.valor

    cuerpo = {
        "reglas_no_incluidas": REGLAS_NO_INCLUIDAS,
        "alumnos": list(por_alumno.values()),
    }
    # La versión es el hash del contenido y no `max(modificado)`: así cambia
    # exactamente cuando cambia lo que el lector tiene que guardar, ni antes ni
    # después (dar de baja un alumno cambia su `habilitado` sin tocar ninguna
    # credencial).
    serializado = json.dumps(cuerpo, sort_keys=True, ensure_ascii=False)
    cuerpo["version"] = hashlib.sha256(serializado.encode()).hexdigest()[:32]
    return cuerpo
```

La vista autentica igual que la de eventos (mismo `auth.dispositivo_autenticado`,
mismos 401/403), actualiza `ultimo_contacto` con `update()`, compara el `ETag`
contra `If-None-Match` y responde `HttpResponseNotModified()` si coincide; si no,
`JsonResponse` con el header `ETag` puesto.

- [ ] **Step 4: Run test** — Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

---

### Task 9: La puerta de un gimnasio que no paga la app sigue abriendo

**Files:** Modify `ingreso/tests_api.py`

**Interfaces:** ninguna nueva. Es la tarea que **fija una invariante** y no
agrega código: si los tests pasan sin tocar nada, perfecto — existen para que
nadie "ordene" el middleware y le corte la puerta a un gimnasio.

- [ ] **Step 1: Write the test**

```python
class PuertaConLaCuentaSuspendidaTests(TestCase):
    """Que un gimnasio le deba la app al dueño del producto NO le cierra la
    puerta a sus alumnos: dejar ochenta personas afuera de un edificio no es
    una palanca de cobro. Lo que se corta es el panel.
    """

    def setUp(self):
        self.gimnasio = Gimnasio.objects.create(
            nombre="A", slug="a",
            estado_cuenta=Gimnasio.EstadoCuenta.SUSPENDIDA,
        )
        # ... token, dispositivo, alumno y credencial, igual que EventosApiTests
        self.staff = User.objects.create_user("staff-a", password="clave-123456")
        Perfil.objects.create(
            usuario=self.staff, gimnasio=self.gimnasio, rol=Perfil.Rol.STAFF
        )

    def test_la_api_sigue_abriendo_la_puerta(self):
        """**El cliente NO se loguea, y eso es parte del test.** Verificado en
        `plataforma/middleware.py:234-237`: el corte del middleware es
        `if usuario is None or not usuario.is_authenticated: return None`. Con
        una sesión de staff el usuario SÍ estaría autenticado, el middleware
        seguiría hasta `sin_acceso(perfil)` y el POST terminaría redirigido a
        `home` -- el test fallaría por el motivo equivocado y alguien
        "arreglaría" el código. El lector real no tiene cookie; el test tampoco.
        """
        respuesta = self.client.post(
            reverse("ingreso:api_eventos"),
            data=json.dumps({"eventos": [{"tipo": "qr", "valor": self.qr.valor}]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["resultados"][0]["permitido"])

    def test_el_padron_tambien_sigue_respondiendo(self):
        """El lector offline tiene que poder refrescar su padrón igual: si no,
        la puerta abre hoy y deja de abrir mañana."""
        respuesta = self.client.get(
            reverse("ingreso:api_padron"),
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(respuesta.status_code, 200)
```

> **La otra mitad de esta invariante —que las pantallas de staff SÍ queden
> cortadas— es un test de la Tarea 12**, donde existe `ingreso:listado`. Se
> nombra acá porque las dos juntas son la decisión, pero no se escribe acá: un
> test que no puede correr hasta ocho tareas después deja la suite roja o
> tentando a comentarlo.

> **Nota que dejó la Tarea 8:** la mitad del padrón ya está escrita en
> `ingreso/tests_api.py::PadronConLaCuentaSuspendidaTests` (200 con la cuenta
> SUSPENDIDA y sin ninguna cookie). Reusarla en vez de duplicar
> `test_el_padron_tambien_sigue_respondiendo`.

- [ ] **Step 2: Run** — Expected: los dos PASS, sin tocar código de producción.

- [ ] **Step 3: Commit**

---

### Task 10: Rate limiting del endpoint público

**Files:**
- Create: `ingreso/limites.py`
- Modify: `ingreso/views.py`, `ingreso/tests_api.py`

**Interfaces:**
- Produces: `ingreso.limites.demasiados_intentos_fallidos(request) -> bool`, `ingreso.limites.cupo_diario_excedido(dispositivo) -> bool`

Los dos endpoints están en internet y los llama una máquina. El camino de fallo
(sin token o con token inválido) no puede ser gratis.

> **Nota que dejó la Tarea 8:** la autenticación de las dos vistas ya vive en un
> solo lugar, `ingreso/views.py::_lector_del_request`. **El freno por IP de los
> fallos va ahí**, así cubre eventos y padrón sin repetirlo. **El tope diario por
> dispositivo NO se aplica al padrón**: cortarle el poll a un lector rompe la
> invariante de que la puerta sigue abriendo (Tarea 9) — el padrón deja de
> refrescarse y mañana la puerta niega a los que hoy pasan.

- [ ] **Step 1: Write the failing test**

```python
class LimitesTests(TestCase):
    def setUp(self):
        cache.clear()
        # ... gimnasio, token y dispositivo igual que EventosApiTests

    def test_muchos_intentos_fallidos_desde_la_misma_ip_dan_429(self):
        for _ in range(limites.MAX_FALLOS_POR_MINUTO):
            self._post({"eventos": []}, token="invalido")

        respuesta = self._post({"eventos": []}, token="invalido")

        self.assertEqual(respuesta.status_code, 429)

    def test_el_limite_no_frena_al_lector_legitimo(self):
        """Un gimnasio a las 19:00 es legítimamente a ráfagas: el límite es del
        camino de FALLO, no del autenticado."""
        for _ in range(limites.MAX_FALLOS_POR_MINUTO + 5):
            respuesta = self._post({"eventos": []}, token=self.token)

        self.assertEqual(respuesta.status_code, 200)

    def test_se_cuenta_por_ip_de_cloudflare_y_no_por_x_forwarded_for(self):
        """`X-Forwarded-For` lo manda el cliente y lo puede rotar para saltear
        el límite. Mismo criterio que `solicitudes/antispam.py::ip_de`."""
        for _ in range(limites.MAX_FALLOS_POR_MINUTO):
            self._post({"eventos": []}, token="invalido",
                       HTTP_CF_CONNECTING_IP="1.2.3.4")

        respuesta = self._post(
            {"eventos": []}, token="invalido",
            HTTP_CF_CONNECTING_IP="1.2.3.4",
            HTTP_X_FORWARDED_FOR="9.9.9.9",
        )

        self.assertEqual(respuesta.status_code, 429)

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_tope_diario_por_lector_vive_en_la_base(self):
        """El respaldo durable: `LocMemCache` es por proceso y volátil, y deja
        de funcionar en silencio el día que alguien arranque gunicorn con
        `--workers 2`. Este se cuenta sobre `EventoIngreso`.

        El tope se parchea a 3: crear las 20.000 filas reales sería medio minuto
        de suite por un test que prueba una comparación.
        """
        EventoIngreso.objects.bulk_create([
            EventoIngreso(
                gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                resultado=EventoIngreso.Resultado.SIN_CREDENCIAL,
                momento=timezone.now(),
            )
            for _ in range(3)
        ])

        respuesta = self._post(
            {"eventos": [{"tipo": "qr", "valor": "x"}]}, token=self.token
        )

        self.assertEqual(respuesta.status_code, 429)

    @patch.object(limites, "MAX_EVENTOS_POR_DIA", 3)
    def test_el_tope_diario_solo_cuenta_los_de_HOY_y_de_ESTE_lector(self):
        """Si contara todo el historial, un lector con dos años de uso quedaría
        trabado para siempre; si contara los de otro lector, un gimnasio con dos
        puertas se trabaría solo."""
        otro = Dispositivo.objects.create(
            gimnasio=self.gimnasio, nombre="Puerta B",
            tipo=Dispositivo.Tipo.QR, token_hash=tokens.hash_de("otro"),
        )
        ayer = timezone.now() - timedelta(days=1)
        EventoIngreso.objects.bulk_create(
            [
                EventoIngreso(
                    gimnasio=self.gimnasio, dispositivo=self.dispositivo,
                    resultado=EventoIngreso.Resultado.PERMITIDO, momento=ayer,
                )
                for _ in range(5)
            ]
            + [
                EventoIngreso(
                    gimnasio=self.gimnasio, dispositivo=otro,
                    resultado=EventoIngreso.Resultado.PERMITIDO,
                    momento=timezone.now(),
                )
                for _ in range(5)
            ]
        )

        respuesta = self._post(
            {"eventos": [{"tipo": "qr", "valor": self.qr.valor}]},
            token=self.token,
        )

        self.assertEqual(respuesta.status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL, `No module named 'ingreso.limites'`

- [ ] **Step 3: Write minimal implementation**

`ingreso/limites.py`, reusando el patrón de `solicitudes/antispam.py:60-95`
(`cache.add` + `cache.incr` con el `except ValueError` de la carrera, y `ip_de`
leyendo `HTTP_CF_CONNECTING_IP` con fallback a `REMOTE_ADDR`):

```python
"""Frenos del endpoint público de la puerta.

**Honesto sobre sus límites**: no hay setting `CACHES` en `config/`, así que el
backend es `LocMemCache` -- por proceso y volátil. El límite por minuto es un
badén, no una garantía, y deja de funcionar EN SILENCIO el día que alguien
arranque gunicorn con `--workers 2`. La defensa real es Cloudflare adelante; el
respaldo durable es el tope diario, que se cuenta sobre `EventoIngreso` y
sobrevive a los reinicios y a los workers.
"""

MAX_FALLOS_POR_MINUTO = 30
MAX_EVENTOS_POR_DIA = 20_000
```

En la vista: el chequeo de IP corre **antes** de resolver el token (para que un
atacante sin llave no nos cueste ni un `sha256`), y el tope diario **después** de
autenticar (necesita saber de qué lector se trata). Los dos devuelven
`JsonResponse({"error": ...}, status=429)`.

**Contabilidad de queries, que este plan lleva en todas las tareas:** el tope
diario suma **una query por request** (un `COUNT` de los eventos de hoy de ese
lector, cubierto por el índice `(gimnasio, -momento, -id)`). Es un costo fijo, no
por evento, así que los tests de escala de las Tareas 7 y 8 —que comparan dos
tamaños de lote— siguen valiendo: los dos lados pagan la misma query de más. Se
paga a cambio del único freno que sobrevive a un reinicio y a `--workers 2`.

- [ ] **Step 4: Run test** — Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ingreso/limites.py ingreso/views.py ingreso/tests_api.py
git commit -m "feat(ingreso): freno por IP y tope diario en el endpoint de la puerta"
```

---

> **Las tareas que siguen describen archivos y moldes en vez de pegar el código
> entero.** No es un placeholder: cada una nombra el archivo exacto del repo que
> hay que abrir como molde (con línea), los nombres de los tests y qué afirma cada
> uno. Es UI y documentación, donde el proyecto ya tiene un patrón rodado y
> copiarlo textual acá lo duplicaría sin agregar información. El ejecutor abre el
> molde.

---

### Task 11: `segno` y el QR renderizado

**Files:** Create `ingreso/qr.py`; modify `requirements.txt`, `ingreso/tests.py`

- [ ] **Step 1: Write the failing test**

```python
class QrTests(SimpleTestCase):
    def test_devuelve_un_svg_inline(self):
        svg = qr.svg_de("abc123")

        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)

    def test_el_mismo_valor_da_el_mismo_svg(self):
        self.assertEqual(qr.svg_de("abc"), qr.svg_de("abc"))

    def test_valores_distintos_dan_svg_distintos(self):
        self.assertNotEqual(qr.svg_de("abc"), qr.svg_de("abd"))

    def test_tambien_puede_dar_png_para_el_pdf(self):
        png = qr.png_de("abc123")

        self.assertTrue(png.startswith(b"\x89PNG"))
```

- [ ] **Step 2: Run** — Expected: FAIL, `No module named 'segno'`

- [ ] **Step 3: Implementar** — `segno==1.6.1` en `requirements.txt` con el
comentario de por qué (Python puro, sin wheel binario, SVG inline y PNG sin
Pillow), `pip install -r requirements.txt`, y `ingreso/qr.py` Django-free con
`svg_de(valor)` y `png_de(valor)`.

- [ ] **Step 4: Run** — Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 12: Pantallas de staff

**Files:** Create `ingreso/forms.py`, `templates/ingreso/*.html`, `ingreso/tests_vistas.py`; modify `ingreso/views.py`, `ingreso/urls.py`

Tres pantallas, con los **cuatro tests de guardia** del molde
`ejercicios/tests.py:722-759` cada una (anónimo redirigido al login, alumno 403,
staff ve lo suyo y no lo del otro gimnasio, staff de otro gimnasio 404 al
editar):

1. **`ingreso:listado`** — ingresos con filtros por fecha, alumno y resultado.
   `select_related("alumno", "dispositivo")`, más un test de escala comparando
   dos tamaños de conjunto: es justo donde vive un N+1.
2. **`ingreso:dispositivos`** / `dispositivo_crear` / `dispositivo_editar` — el
   token se muestra **una sola vez**, en un 200 que no redirige, con
   `@method_decorator(never_cache, name="dispatch")` sobre toda la vista (molde
   `plataforma/views.py:429`). Test: el token aparece en el HTML del POST de
   alta, y **no** vuelve a aparecer al editar.
Más **la segunda mitad de la invariante de la Tarea 9**: con un gimnasio en
`SUSPENDIDA`, un staff logueado que abre `ingreso:listado` recibe el cartel
(`assertTemplateUsed(respuesta, "plataforma/cuenta_bloqueada.html")`, no un
`assertContains` de la palabra "cuenta", que pasaría de casualidad). Es lo que
prueba que `ingreso` **no** está en `APPS_SIN_BLOQUEO` mientras la API sigue
abriendo la puerta.

3. **`ingreso:configuracion`** — `UpdateView` sobre la fila única, molde
   `ConfiguracionTurnosView` (`turnos/views.py:112-148`), con `get_object`
   devolviendo `obtener_configuracion(self.gimnasio)`. **Dos avisos en
   pantalla**, cada uno con su test:
   - si `bloquea_por_deuda` está tildado y `gimnasio.dias_tolerancia_pago` es
     `None`: "Esto no va a bloquear a nadie hasta que cargues los días de
     tolerancia en Mi gimnasio" (el interruptor muerto de
     `pagos/acceso.py::_umbral`);
   - qué reglas no se aplican con el lector offline, **al lado de los dos
     checkboxes**, no en una nota al pie.

Los tres templates extienden `base.html` y reusan `.tarjeta`, `.boton`,
`.tabla`, `.badge--ok`/`.badge--riesgo`, `acciones-lista`. Los campos van con
`{% include 'partials/campo_form.html' with campo=form.x %}` o con `form.as_p`:
`core.tests.ErroresDeFormularioVisiblesTests` barre todos los templates con
`csrf_token` y falla si un `{{ form.x }}` no muestra sus errores.

**No se agrega ningún ítem al nav** (`templates/base.html:243-254` se queda en 8).

> **Nota que dejó la Tarea 10:** la vista que ROTA el token de un lector
> (regenerarlo desde `dispositivo_editar`) tiene que poner `eventos_hoy=0` en el
> MISMO `update()` que cambia `token_hash`, e invalidar el set cacheado de
> lectores activos (`cache.delete(limites.CLAVE_SET_ACTIVOS)`); lo mismo al
> desactivar o reactivar un lector. Sin lo primero, un atacante que agotó el
> tope diario con el token robado deja al lector con 429 todo el día aunque el
> dueño rote el token. Sin lo segundo, el token viejo sigue pasando el freno
> por IP hasta un minuto (igual da 401, pero cuesta la query) y el nuevo puede
> recibir 429 ese minuto si la IP del gimnasio está frenada. Un test por cada
> cosa.

- [ ] Steps: test de guardia → correr y ver fallar → vista+template → correr → commit, una pantalla por vez.

---

### Task 13: Ficha del alumno y portal

**Files:** Modify `templates/alumnos/alumno_detail.html`, `templates/tenants/home.html`, `ingreso/views.py`, `ingreso/urls.py`, `alumnos/views.py`; create `templates/ingreso/partials/credencial_alumno.html`

- Tarjeta **"Credencial de ingreso"** en la ficha (ojo: esa ficha ya tiene un
  `<h2>Acceso</h2>` que significa usuario+contraseña, así que el título nuevo
  tiene que ser distinto): el QR en SVG, "Generar código"/"Regenerar", el campo
  del id facial, y el link a imprimir.
- Portal del alumno: "Tu código para entrar", con el QR. **Se muestra igual
  estando bloqueado por deuda** — lo que cambia es el mensaje; la puerta decide.
- **Estado vacío** cuando el alumno no tiene credencial: "todavía no tenés
  código, pedíselo al gimnasio". Nunca un error.
- Acción en bloque "Generar códigos para los alumnos activos" en
  `alumnos:listado`, llamando a `services.generar_credenciales_en_bloque`.

Tests: el alumno ve su QR y **no** el de otro alumno; el bloqueado por deuda lo
ve igual; el que no tiene credencial ve el estado vacío; el POST de regenerar
cambia el valor y solo lo puede hacer el staff del propio gimnasio.

> **Nota que dejó la Tarea 11:** `qr.svg_de(valor)` devuelve un `<svg>` SIN
> `width`/`height` (solo `viewBox`): el template tiene que darle un ancho
> (p. ej. `max-w-[16rem] w-full`) o se estira a todo el contenedor. Va inline
> con `|safe`, que es seguro porque el SVG lo arma segno a partir de un token
> de `secrets` y no hay texto del usuario adentro. El QR es una llave al
> portador: la ficha y el portal ya son vistas autenticadas y acotadas por
> gimnasio, pero **la respuesta que lo contiene tiene que llevar
> `Cache-Control: no-store, private`** (mismo criterio que el padrón), y un
> test que lo fije por cada pantalla; si alguna vez se sirve como archivo
> aparte, nada de URL pública ni adivinable, y 404 para otro alumno u otro
> gimnasio.

---

### Task 14: Tarjeta en el dashboard

**Files:** Modify `tenants/views.py` (`_metricas_dashboard`), `templates/tenants/home.html`, `tenants/tests.py`

Suma **dos queries y ni una más**: un `aggregate` con `Count` condicional sobre
los eventos de hoy (permitidos y rechazados en la misma pasada) y un `count()`
de lectores activos — ese segundo número, en cero, es lo que hace que la tarjeta
invite a configurar en vez de mostrar "0 ingresos". El "hoy" con
`timezone.localdate()`, como el resto de la función.

Tests: la tarjeta muestra los números del propio gimnasio y no los del otro; sin
lectores cargados invita a configurar; y un test de costo que compare el dashboard
con y sin eventos para fijar que no aparece un N+1.

---

### Task 15: Tarjetas imprimibles en PDF

**Files:** Create `ingreso/tarjetas.py`; modify `ingreso/views.py`, `ingreso/urls.py`, `ingreso/tests_vistas.py`

`generar_pdf_credenciales(gimnasio, alumnos) -> bytes`, Django-free, con fpdf2,
siguiendo el reparto de `rutinas/pdf.py` ↔ `RutinaAsignadaPdfView` (la vista es
la única capa que conoce `HttpResponse`). Varias tarjetas por hoja, cada una con
el nombre del gimnasio, el del alumno y el QR en PNG.

Tests: devuelve bytes que arrancan con `%PDF`; la vista es solo para staff y solo
de su gimnasio; un alumno sin credencial no rompe la generación.

> **Nota que dejó la Tarea 11:** `qr.png_de(valor)` ya da el PNG con zona
> silenciosa de 4 módulos, fondo blanco pintado y 10 px por módulo (410 px
> para un token de 43 caracteres, versión 4-Q). Se inserta con
> `pdf.image(io.BytesIO(qr.png_de(valor)), x=..., y=..., w=...)` — verificado
> contra fpdf2 2.8.8. No achicar el QR impreso por debajo de ~2,5 cm de lado.
> La respuesta del PDF lleva `Cache-Control: no-store, private`: es un lote de
> llaves de puerta.

---

### Task 16: `manage.py purgar_ingresos`

**Files:** Create `ingreso/management/commands/purgar_ingresos.py`; modify `ingreso/tests.py`

Molde exacto: `tenants/management/commands/exportar_gimnasio.py`. `--gimnasio`
obligatorio por slug con `CommandError` si no existe, `--meses` (default 24),
`--dry-run`, y borrado por tandas de pk.

Existe por el techo del exportador: `tenants/exportacion.py::MAX_FILAS_WEB` es
60.000 y un gimnasio de 300 alumnos genera del orden de 72.000 eventos por año,
así que sin purga la exportación deja de generarse justo cuando el cliente se
quiere ir.

> **Corregido al revisar la Tarea 16:** la purga es retención periódica, para
> que el botón no choque contra el techo en la operación normal. NO es un paso
> previo a exportar los datos de un gimnasio que se va: eso le borraría el
> historial que se quiere llevar. Ese caso va por `manage.py exportar_gimnasio`,
> que no tiene techo.

Tests: `CommandError` con un slug inexistente; `--dry-run` no borra y reporta el
número; no toca los eventos de otro gimnasio; y corre varias tandas (con el
tamaño de tanda parcheado a 2 sobre 5 filas).

Implementado con dos precisiones: el corte son **meses de calendario** desde
`timezone.localdate()`, a la medianoche local (se borra `momento < corte`; el
evento justo en el corte se conserva), y `--meses` tiene un **mínimo de 3**
(`MESES_MINIMOS`) para que un `--meses 1` tipeado de apuro no se lleve los
ingresos que explican un reclamo reciente.

---

### Task 17: Documento de integración y puente de ejemplo

**Files:** Create `docs/integracion-control-de-ingreso.md`, `scripts/puente_ingreso_ejemplo.py`, `scripts/probar_ingreso_local.sh`; modify `ingreso/tests.py`

El documento (molde `docs/runbook-respaldos.md`): el contrato completo de los dos
endpoints con ejemplos de `curl`, cómo se saca el token, **el flujo de
enrolamiento facial** (dar de alta la cara en el equipo y copiar el id a la
ficha), qué hace el puente, **qué reglas no se aplican offline**, y que el token
es tan sensible como el llavero del gimnasio.

`scripts/puente_ingreso_ejemplo.py`: **solo stdlib** (`urllib`, `json`), porque
tiene que correr en la PC de un gimnasio sin `pip install`.

`scripts/probar_ingreso_local.sh`: la verificación end-to-end de la puerta contra
`runserver` — 401 → veredicto online → lote diferido → idempotencia → padrón con
y sin `If-None-Match`. Va como script para que sea repetible y no una sesión
manual irrepetible.

**Test de alambre trampa** (`SimpleTestCase`): saca las rutas `/ingreso/v1/…` del
documento y del script con un regex y exige que cada una resuelva con
`resolve()`, así el documento no puede quedar desfasado del código en silencio.
Mismo criterio que `config/tests.py::BlueprintDeclaraLoQueSettingsLeeTests`, sin
dependencia nueva. Un segundo test parsea los `import` del puente y afirma que no
importa nada fuera de la stdlib.

**Notas que dejó la Tarea 7 para el documento** (el contrato real se apartó del
de este plan; el código manda):

- `id_evento_equipo` numérico (`8821`) vuelve como **string** (`"8821"`) en la
  respuesta de un evento válido: se guarda como texto. El de un evento
  inválido vuelve tal como vino. El puente tiene que comparar como string.
- Un evento inválido no tumba el lote: vuelve en su posición con
  `resultado: "invalido"` y un `error` legible; solo la forma del request
  entero da 400.
- En online manda el reloj del servidor (el `momento` del lector solo deja un
  diagnóstico en `detalle`). En diferido `momento` e `id_evento_equipo` son
  obligatorios y el `momento` tiene que caer entre 7 días atrás y 1 hora
  adelante.
- `abierto_por_el_equipo` es `true`/`false` JSON, no un string.
- Al reintentar un request que no tuvo respuesta, **reusar el mismo
  `id_evento_equipo`**; sin id, el reintento online da `DUPLICADO` (riesgo
  aceptado 11 de la spec).
- El esquema `Bearer` no distingue mayúsculas.

**Notas que dejó la Tarea 10** (frenos):

- La API puede responder **429** con `motivo` (`intentos_fallidos` o
  `tope_diario`) y `Retry-After` en segundos. **Ante cualquier 429, sea cual sea
  el `motivo`, el puente decide con el padrón local** (como si no hubiera
  internet) y sube ese evento después EN DIFERIDO, respetando `Retry-After`.
  Nunca reintentar en bucle: los reintentos también cuentan como fallas si el
  token es malo.
- El padrón nunca recibe 429 por el tope diario (solo por el freno de fallas,
  que a un token válido no lo alcanza).
- **Un TIMEOUT de la API online se trata igual que un 429** (nota que dejó la
  Tarea 15): el puente decide con el padrón local y sube ese evento después en
  diferido, reusando el mismo `id_evento_equipo`. El único worker de gunicorn
  atiende el panel y la puerta a la vez, así que cualquier request largo del
  panel (una tanda de tarjetas, un padrón grande, el exportador) deja las
  lecturas online en cola. Sin esta regla, eso deja gente parada en la puerta
  (spec, riesgo aceptado 15).
- El tope es de 20.000 eventos NUEVOS por lector y por día local, y el backlog
  en diferido cuenta en el día en que se SUBE, no en el de su `momento`: 20.000
  por día cubre unos 10 días offline de un gimnasio grande (unos 2.000 por
  día). Los reintentos con el mismo `id_evento_equipo` no suman.

**Notas que dejó la Tarea 8** (padrón):

- Viajan solo alumnos ACTIVOS con credenciales activas. **Los que desaparecen
  del padrón se borran del equipo, cara incluida** (dados de baja, credenciales
  revocadas); **los `habilitado: false` son deudores: bloquear, no borrar**
  (vuelven apenas pagan). Reactivar a un alumno obliga a volver a enrolar su
  cara.
- Cada fila trae `motivo` (`null` o `"deuda"`). Los textos van una vez, en
  `mensajes` (`permitido`, `deuda`, `sin_credencial`), iguales a los de la
  puerta online. `mensajes.permitido` es una plantilla: el puente reemplaza
  `{nombre}` por el `nombre` de la fila. Lo que no está en el padrón muestra
  `mensajes.sin_credencial`.
- El padrón es una foto del último poll, en las dos direcciones: offline deja
  entrar a quien se atrasó después (incluido el cruce del umbral de deuda a
  medianoche) y a un QR regenerado porque se filtró; y rechaza al que pagó en
  el mostrador, al reactivado y a una credencial recién emitida. Pollear
  seguido y siempre con `If-None-Match`.
- El `ETag` va entre comillas; el 304 acepta `W/"…"` y la versión pelada. Un
  304 igual cuesta lo mismo que un 200 del lado del servidor (riesgo aceptado,
  `ISSUES.md` [2026-09-28]).
- `Cache-Control: no-store`: el puente guarda su copia y manda
  `If-None-Match`; ningún intermediario la guarda.

---

### Task 18: Documentación del proyecto

**Files:** Modify `CLAUDE.md`, `ROADMAP.md`, `ISSUES.md`

- `CLAUDE.md`: sección propia de la app `ingreso` con las decisiones que no hay
  que deshacer (sin biometría; `decidir` no toca la base; `estado_cuenta` no
  cierra la puerta; los cuatro lugares de cobertura), y **sacar "QR de ingreso,
  control de asistencia" de "Qué NO construir todavía"**.
  Mencionar la operación de `manage.py purgar_ingresos` como **retención
  periódica** (primero con `--dry-run`), para que el botón «Exportar mis datos»
  no choque contra `MAX_FILAS_WEB` en la operación normal. **Nunca como paso
  previo a la exportación de un gimnasio que pide sus datos**: purgar ahí le
  borra justo el historial que se quiere llevar. Ese caso se resuelve con
  `manage.py exportar_gimnasio`, que no tiene techo.
- `ROADMAP.md`: sacarlo de la lista de Fase 9.
- `ISSUES.md`: entrada del día con lo que quedó **sin verificar hasta que exista
  hardware** (que un lector real hable este dialecto, que el enrolamiento facial
  coincida con la UI de un fabricante, la latencia de un molinete, y si el cold
  start de Neon entra en su timeout), más la deuda que apareció montando el
  worktree: **cinco tests de la suite necesitan secretos reales en el entorno**
  (`VAPID_PRIVATE_KEY`, `GOOGLE_TOKEN_ENCRYPTION_KEY`), así que un clon fresco no
  la puede correr en verde.

---

## Verificación

1. **Suite completa**: `/Users/fabriziosola/app_gim/.venv/bin/python manage.py test`
   — en verde, partiendo de un baseline medido de **1723 tests, OK**.
2. **`scripts/probar_ingreso_local.sh`** contra `runserver`: es el end-to-end
   real de la puerta, y lo único que ejercita el contrato de punta a punta sin
   hardware.
3. **Navegador**: las cuatro pantallas de staff, la tarjeta del dashboard, la
   ficha del alumno y el QR del portal. El `/code-review` no reemplaza esto.
4. **La tarjeta impresa se decodifica con la cámara del celular**, y se confirma
   que el string leído es idéntico a `Credencial.valor`. Es la única parte física
   verificable hoy, y si el QR sale ilegible impreso se cae toda la decisión del
   "QR fijo para el que no tiene smartphone".
5. **`/code-review`** sobre la rama antes de mergear.

**Sin verificar hasta que haya hardware**: que un lector real hable este
dialecto, el flujo de enrolamiento facial de un fabricante concreto, la latencia
de un molinete, y el cold start de Neon contra su timeout. Queda en `ISSUES.md`.
