# Respaldo automático + migración a Neon — Plan de implementación

> **Para workers agénticos:** SUB-SKILL REQUERIDA: usar
> `superpowers:subagent-driven-development` (recomendado) o
> `superpowers:executing-plans` para ejecutar tarea por tarea.
>
> **Primer paso al ejecutar:** copiar este plan a
> `docs/superpowers/plans/2026-07-29-respaldo-y-migracion-neon-plan.md`
> (convención del repo) y commitearlo.

**Goal:** Que los datos de app_gim vivan en una base que no expira y tengan
un respaldo diario cifrado, verificado y monitoreado fuera de la plataforma
que corre la app.

**Architecture:** Cuatro plataformas sin punto único de falla — Render corre
la app, Neon guarda los datos, R2 guarda los backups, GitHub Actions
orquesta, y Healthchecks.io alerta por *ausencia* de backup desde afuera de
GitHub. El respaldo es `pg_dump --format=custom` cifrado con `gpg`, nunca
`dumpdata`. La verificación **la invoca el propio backup** pasándole el
objeto exacto que acaba de escribir.

**Tech Stack:** Django 5.2 · `dj-database-url` 3.1.2 · GitHub Actions ·
`pg_dump`/`pg_restore` · `gpg` · AWS CLI contra el endpoint S3 de R2.

**Spec:** `docs/superpowers/specs/2026-07-29-respaldo-y-migracion-neon-design.md`

---

## Context

La app está desplegada en `https://app-gim.onrender.com` con la base creada
por el Blueprint en el plan free de Render, y **no existe ningún respaldo**:
el único management command del proyecto es `generar_pagos`.

Dos relojes corren en contra. El Postgres free de Render **expira a los 30
días + 14 de gracia** (no 90 como todavía dicen `render.yaml` e `ISSUES.md`),
y ante un borrado accidental hoy no hay ninguna vuelta atrás.

El disparador fue la pregunta "¿todos los datos se guardan en R2?". La
respuesta es no — R2 guarda solo `Gimnasio.logo`, `PagoMensual.comprobante` e
`Importacion.archivo`; todo lo demás vive en Postgres — y al revisarlo
apareció que ese Postgres no tenía ni respaldo ni permanencia.

El resultado esperado: una base permanente en Neon y un backup diario que se
prueba solo restaurándolo de verdad una vez por mes.

## Global Constraints

- **Nunca `dumpdata`/`loaddata`.** `calendario/signals.py:24`
  (`sync_reserva_guardada`) no chequea `raw`, y `loaddata` dispara `post_save`
  con `raw=True` → sincronizaría cada `Reserva` contra la API real de Google
  Calendar. Solo `pg_dump`/`pg_restore`.
- **`pg_dump` nunca contra la URL pooled** de Neon; siempre la directa. La app
  usa la pooled.
- **La major de `postgresql-client` va pineada** al valor exacto que reporte
  Neon, nunca `latest`: `pg_dump` no puede volcar un servidor de major
  superior a la suya.
- **El dump nunca se sube como artifact de GitHub.** Va cifrado directo a R2.
  En los logs solo metadata (tamaño, checksum, conteos), nunca datos
  personales.
- **`set -euo pipefail`** al inicio de cada bloque `run` multilínea.
- **`gpg` siempre con `--batch --pinentry-mode loopback --passphrase-fd 0`.**
  Sin `loopback`, GnuPG 2.x intenta abrir `pinentry` y falla en un runner sin
  terminal. La passphrase nunca va como argumento de línea de comandos
  (quedaría visible en la tabla de procesos).
- **Todo workflow lleva `permissions`, `concurrency` y `timeout-minutes`
  explícitos**, y las actions de terceros van pineadas a SHA completo.
- Bucket lock **solo** en `monthly/`. Si se le pone a `daily/`, la regla de
  lifecycle a 30 días nunca se aplica (el lock tiene precedencia).
- Suite completa en verde antes de cada commit de código.

---

## Pasos manuales del usuario (cuentas de terceros)

**M1 — Neon** *(bloquea Task 2)*
Crear proyecto. Anotar URL **pooled** y **directa** por separado. Correr
`SELECT version();` y anotar la **major** (define el pin de
`postgresql-client` en Tasks 3 y 4).

**M2 — Cloudflare R2** *(bloquea Task 3)*
Crear bucket `app-gim-backups` (separado de `app-gim-media`) + token de API
propio con acceso **solo** a ese bucket, con permiso de **leer y escribir
objetos pero no de administrar ni borrar el bucket**. Lifecycle: expirar
`daily/` a 30 días. Bucket lock: `monthly/` retenido 12 meses. Verificar
además que el token de la app (`app-gim-media`) tampoco tenga permisos de
administración.

**M3 — Healthchecks.io** *(bloquea Tasks 3 y 4)*
Plan Hobbyist. Dos checks: `app-gim-backup-diario` (período 1 día, gracia
6 h) y `app-gim-backup-verify` (período 35 días, gracia 3 días). Copiar
ambas ping URLs.

**M4 — GitHub** *(bloquea Task 3)*
Límite de gasto en **US$0**. Cargar secrets:

| Secret | Valor | Hace falta desde |
|---|---|---|
| `NEON_DATABASE_URL_DIRECT` | URL directa (M1) | Task 3 |
| `BACKUP_ENCRYPTION_KEY` | passphrase larga al azar — **guardar también en el gestor de contraseñas**; sin ella los backups son ilegibles | Task 3 |
| `R2_BACKUPS_BUCKET_NAME` / `_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `_ENDPOINT_URL` | del token de M2 | Task 3 |
| `HEALTHCHECKS_URL_BACKUP` / `HEALTHCHECKS_URL_VERIFY` | de M3 | Task 3 / 4 |
| `NEON_DATABASE_URL_POOLED` | URL pooled (M1) | **Task 5** — no cargarlo antes |

---

## Task 1: Configuración de base de datos testeable + health checks

**Files:**
- Create: `config/db.py`, `config/tests.py`
- Modify: `config/settings.py:126-143` (y borrar `import dj_database_url` de la línea 18)

**Interfaces:**
- Produces: `config.db.database_config(database_url, debug, base_dir) -> dict`
  — el dict de `DATABASES["default"]`.

> **Por qué se extrae a un módulo y no se cambia sólo la llamada inline.**
> La alternativa mínima sería dejar todo en `settings.py` y testear con
> `assertTrue(settings.DATABASES["default"]["CONN_HEALTH_CHECKS"])`. **Ese
> test no puede pasar**: verificado en este entorno, con la suite corriendo
> (sin `DATABASE_URL`, contra SQLite) esa clave existe pero vale `False`
> — Django rellena los defaults por base. La rama de Postgres solo existe en
> producción, y una función pura es la forma más chica de poder ejercitarla.
> Aun así se recortan los tests de 5 a 3: los dos que se caen sólo
> verificaban comportamiento de `dj-database-url`, no decisiones nuestras.

- [x] **Step 1: Escribir el test que falla**

Crear `config/tests.py`:

```python
"""Tests de la configuración de conexión a la base (`config/db.py`)."""

from pathlib import Path

from django.test import SimpleTestCase

from config.db import database_config

URL_POSTGRES = "postgres://u:p@h:5432/d"


class DatabaseConfigTests(SimpleTestCase):
    def test_sin_database_url_usa_sqlite(self):
        cfg = database_config("", debug=True, base_dir=Path("/tmp"))
        self.assertEqual(cfg["ENGINE"], "django.db.backends.sqlite3")

    def test_postgres_activa_health_checks(self):
        """Neon suspende el compute por inactividad (scale-to-zero). Sin health
        checks, Django reusa una conexión ya muerta del pool de CONN_MAX_AGE y
        el request falla de forma intermitente."""
        cfg = database_config(URL_POSTGRES, debug=False, base_dir=Path("/tmp"))
        self.assertIs(cfg["CONN_HEALTH_CHECKS"], True)
        self.assertEqual(cfg["CONN_MAX_AGE"], 600)

    def test_postgres_exige_ssl_fuera_de_debug(self):
        cfg = database_config(URL_POSTGRES, debug=False, base_dir=Path("/tmp"))
        self.assertEqual(cfg["OPTIONS"]["sslmode"], "require")
```

- [x] **Step 2: Correr el test y verificar que falla**

```bash
python manage.py test config -v 2
```
Esperado: FAIL — `ModuleNotFoundError: No module named 'config.db'`.
Si dice "Ran 0 tests", usar `python manage.py test config.tests -v 2`.

- [x] **Step 3: Escribir `config/db.py`**

```python
"""Configuración de la conexión a la base de datos.

Vive fuera de `settings.py` para poder testear la decisión Postgres-vs-SQLite
sin recargar el módulo de settings con otro entorno: la rama de Postgres solo
se ejercita en producción.
"""

import dj_database_url

# Reusar conexiones entre requests evita abrir una TCP+TLS nueva en cada uno,
# importante en planes free con recursos limitados.
CONN_MAX_AGE = 600


def database_config(database_url, debug, base_dir):
    """Devuelve el dict de `DATABASES["default"]`.

    Con `database_url` seteada usa Postgres (producción, Neon); sin ella,
    SQLite local (desarrollo y tests) -- mismo criterio que el resto de
    settings.

    `conn_health_checks` es obligatorio contra Neon: el compute se suspende
    por inactividad y sin el chequeo Django reusa conexiones muertas del pool.
    """
    if database_url:
        return dj_database_url.parse(
            database_url,
            conn_max_age=CONN_MAX_AGE,
            conn_health_checks=True,
            ssl_require=not debug,
        )
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base_dir / "db.sqlite3",
    }
```

- [x] **Step 4: Reemplazar el bloque en `config/settings.py:126-143`**

Borrar el `if os.environ.get("DATABASE_URL"): ... else: ...` completo, borrar
`import dj_database_url` de la línea 18, agregar
`from config.db import database_config` junto a los demás imports, y dejar:

```python
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# La lógica vive en `config/db.py` para poder testearla sin recargar settings
# con otro entorno -- ver `config/tests.py`.
DATABASES = {
    "default": database_config(os.environ.get("DATABASE_URL"), DEBUG, BASE_DIR)
}
```

- [x] **Step 5: Correr los tests**

```bash
python manage.py test config -v 2 && python manage.py test
```
Esperado: 3 tests nuevos PASS y la suite completa en verde (417 tests).

- [x] **Step 6: Commit**

```bash
git add config/db.py config/tests.py config/settings.py
git commit -m "refactor(config): extraer database_config y activar conn_health_checks

Neon suspende el compute por inactividad; sin health checks Django reusa
conexiones muertas del pool de conn_max_age=600."
```

---

## Task 2: Migrar la base a Neon

**Bloqueada por:** M1. **Files:** Modify `render.yaml`, `ISSUES.md`.

- [x] **Step 1: Inventariar la base de Render ANTES de tocar nada**

Desde la Shell de Render (o con la connection string externa):

```sql
SELECT 'auth_user' t, COUNT(*) FROM auth_user
UNION ALL SELECT 'gimnasios', COUNT(*) FROM tenants_gimnasio
UNION ALL SELECT 'alumnos', COUNT(*) FROM alumnos_alumno
UNION ALL SELECT 'reservas', COUNT(*) FROM turnos_reserva
UNION ALL SELECT 'pagos', COUNT(*) FROM pagos_pagomensual
UNION ALL SELECT 'rutinas_asignadas', COUNT(*) FROM rutinas_rutinaasignada
UNION ALL SELECT 'importaciones', COUNT(*) FROM importaciones_importacion;
```

**Anotar el resultado en el commit de la Step 5.** Este paso decide cuál de
las dos rutas siguientes se toma; no se saltea aunque "se sepa" que está
vacía. La base de Render es hoy la única copia que existe.

- [x] **Step 2a: Ruta VACÍA — solo si todo lo operativo dio 0**

(Se acepta `auth_user` > 0: es el superusuario.) Cargar la URL **pooled** en
el dashboard de Render → Manual Deploy. El `buildCommand` ya corre `migrate`,
así que el esquema se crea solo. Después, `createsuperuser` desde la Shell.

- [x] **Step 2b: Ruta CON DATOS — si cualquier tabla operativa dio > 0**

**No** usar `migrate` + `createsuperuser`: abandonaría los datos. En su lugar,
desde una máquina con `postgresql-client` de la major de Neon:

```bash
set -euo pipefail
pg_dump --format=custom --no-owner --no-privileges \
  --file=render.dump "<CONNECTION_STRING_EXTERNA_DE_RENDER>"
pg_restore --no-owner --no-privileges --dbname "<URL_DIRECTA_DE_NEON>" render.dump
psql "<URL_DIRECTA_DE_NEON>" -c "SELECT COUNT(*) FROM alumnos_alumno;"
```

Los conteos en Neon deben coincidir **exactamente** con los de la Step 1
antes de cambiar `DATABASE_URL` en Render.

- [x] **Step 3: Sacar `fromDatabase` de `render.yaml`**

Reemplazar el bloque de `DATABASE_URL` en `services[0].envVars` por:

```yaml
      # Neon (free permanente). URL POOLED acá; la directa solo la usa
      # pg_dump en .github/workflows/backup.yml -- el pooler no sostiene
      # bien la sesión larga de un dump.
      - key: DATABASE_URL
        sync: false
```

Y borrar el bloque `databases:` del inicio del archivo. Sacarlo del Blueprint
**no borra** la base ya creada: Render conserva los recursos existentes hasta
que se los elimina a mano desde el dashboard.

- [x] **Step 4: Corregir el plazo de expiración en los comentarios**

En `render.yaml` el comentario de cabecera dice "el Postgres free expira a los
90 días" — reemplazarlo por una nota de que la base ya no la maneja Render.
En `ISSUES.md`, entrada `[2026-07-01]`: corregir "90 días" por "30 días + 14
de gracia (Render bajó el plazo en mayo de 2024)" y apuntar a la entrada
nueva.

- [x] **Step 5: Documentar en `ISSUES.md` y commitear**

```markdown
## [2026-07-29] Postgres migrado de Render free a Neon free
**Estado:** resuelto
**Impacto:** el Postgres free de Render expira a los 30 días (+14 de gracia)
y después Render borra los datos.
**Resolución / próximo paso:** inventario previo de la base de Render
(resultado: <PEGAR CONTEOS DE LA STEP 1>), migración por <ruta 2a | 2b>.
`render.yaml` ya no declara `databases:`; `DATABASE_URL` se carga a mano
(`sync: false`) con la URL **pooled** de Neon; la **directa** se usa solo
desde `.github/workflows/backup.yml`. Se activó `conn_health_checks` (ver
`config/db.py`) porque Neon suspende el compute por inactividad. La base
vieja de Render NO se borra hasta completar la verificación end-to-end.
```

```bash
git add render.yaml ISSUES.md
git commit -m "chore(deploy): mover la base a Neon y corregir el plazo de expiración documentado"
```

---

## Task 3: Workflow de backup diario

**Bloqueada por:** M2, M3, M4 y Task 2.
**Files:** Create `.github/workflows/backup.yml`; Modify `ISSUES.md`.

**Interfaces:**
- Produces: job `backup` con outputs `monthly` (`"true"`/`"false"`) y
  `objeto` (nombre del archivo `.dump.gpg`). Task 4 los consume.

- [x] **Step 1: Obtener el SHA de las actions que se van a pinear**

```bash
gh api repos/actions/checkout/git/ref/tags/v4 --jq .object.sha
gh api repos/actions/setup-python/git/ref/tags/v5 --jq .object.sha
```

Anotarlos; se usan en Task 5. (Task 3 y 4 no usan actions de terceros.)

- [x] **Step 2: Crear `.github/workflows/backup.yml`**

Major de Neon confirmada: **18** (PostgreSQL 18.4, región sa-east-1).

```yaml
name: Backup diario de Postgres

on:
  schedule:
    - cron: "0 6 * * *"   # 06:00 UTC = 03:00 ART, fuera de horario de gimnasio
  workflow_dispatch:

permissions: {}

concurrency:
  group: backup-production
  cancel-in-progress: false

jobs:
  backup:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    outputs:
      monthly: ${{ steps.subir.outputs.monthly }}
      objeto: ${{ steps.subir.outputs.objeto }}
    steps:
      - name: Instalar postgresql-client y verificar herramientas
        run: |
          set -euo pipefail
          sudo install -d /usr/share/postgresql-common/pgdg
          sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
            https://www.postgresql.org/media/keys/ACCC4CF8.asc
          echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
          sudo apt-get update -qq
          sudo apt-get install -y --no-install-recommends postgresql-client-18
          pg_dump --version
          gpg --version | head -1
          aws --version

      - name: Generar el dump
        env:
          PGURL: ${{ secrets.NEON_DATABASE_URL_DIRECT }}
        run: |
          set -euo pipefail
          STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
          echo "STAMP=$STAMP" >> "$GITHUB_ENV"
          pg_dump --format=custom --no-owner --no-privileges \
            --file="app-gim-$STAMP.dump" "$PGURL"
          ls -lh "app-gim-$STAMP.dump"

      - name: Cifrar y calcular checksum
        env:
          KEY: ${{ secrets.BACKUP_ENCRYPTION_KEY }}
        run: |
          set -euo pipefail
          printf '%s' "$KEY" | gpg --symmetric --batch --yes \
            --pinentry-mode loopback --cipher-algo AES256 --passphrase-fd 0 \
            --output "app-gim-$STAMP.dump.gpg" "app-gim-$STAMP.dump"
          rm -f "app-gim-$STAMP.dump"
          # Checksum sobre el archivo YA CIFRADO: lo que hay que detectar es una
          # subida truncada, y así se verifica antes de intentar descifrar.
          sha256sum "app-gim-$STAMP.dump.gpg" > "app-gim-$STAMP.dump.gpg.sha256"
          cat "app-gim-$STAMP.dump.gpg.sha256"
          ls -lh "app-gim-$STAMP.dump.gpg"

      - name: Subir a R2
        id: subir
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.R2_BACKUPS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_BACKUPS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: auto
          ENDPOINT: ${{ secrets.R2_BACKUPS_ENDPOINT_URL }}
          BUCKET: ${{ secrets.R2_BACKUPS_BUCKET_NAME }}
        run: |
          set -euo pipefail
          OBJ="app-gim-$STAMP.dump.gpg"
          for f in "$OBJ" "$OBJ.sha256"; do
            aws s3 cp "$f" "s3://$BUCKET/daily/$f" --endpoint-url "$ENDPOINT"
          done
          echo "objeto=$OBJ" >> "$GITHUB_OUTPUT"
          # El día 1 va también a monthly/, que tiene bucket lock a 12 meses.
          if [ "$(date -u +%d)" = "01" ]; then
            for f in "$OBJ" "$OBJ.sha256"; do
              aws s3 cp "$f" "s3://$BUCKET/monthly/$f" --endpoint-url "$ENDPOINT"
            done
            echo "monthly=true" >> "$GITHUB_OUTPUT"
          else
            echo "monthly=false" >> "$GITHUB_OUTPUT"
          fi

      # Solo si todo lo anterior salió bien. Healthchecks alerta por AUSENCIA
      # de este ping: cubre el dump fallido, el workflow desactivado por
      # inactividad del repo, y GitHub Actions caído.
      - name: Avisar a Healthchecks
        run: curl -fsS -m 10 --retry 3 "${{ secrets.HEALTHCHECKS_URL_BACKUP }}"

  # Encadenar acá (y no con un `schedule` propio) elimina la carrera: GitHub no
  # garantiza puntualidad en los cron, así que un verify agendado "una hora
  # después" podría correr antes de que el backup del día 1 termine y terminar
  # validando el del día anterior. Así se verifica EXACTAMENTE el objeto recién
  # escrito, sin ventanas de tiempo ni heurísticas de antigüedad.
  verificar:
    needs: backup
    if: needs.backup.outputs.monthly == 'true'
    uses: ./.github/workflows/backup-verify.yml
    with:
      objeto: ${{ needs.backup.outputs.objeto }}
      prefijo: monthly
    secrets: inherit
```

- [x] **Step 3: Anotar en `ISSUES.md` por qué no se usa `dumpdata`**

```markdown
## [2026-07-29] El respaldo usa pg_dump, nunca dumpdata/loaddata
**Estado:** aceptado (restricción de diseño, no bug)
**Impacto:** `calendario/signals.py:24` (`sync_reserva_guardada`) no chequea
`kwargs["raw"]`. `loaddata` emite `post_save` con `raw=True`, así que
restaurar por esa vía dispararía la sincronización con la API real de Google
Calendar para CADA `Reserva` del dump, creando eventos duplicados en los
calendarios de los alumnos.
**Resolución / próximo paso:** el respaldo usa `pg_dump --format=custom` y se
restaura con `pg_restore`, que no ejecutan código de Django. NO se agregó el
guard `if kwargs.get("raw"): return` porque haría creer que `loaddata` es una
vía de restauración soportada, y no lo es. Si alguna vez se reintroduce
`loaddata` para otra cosa, agregar el guard ANTES.
```

- [x] **Step 4: Commit y push** (el workflow todavía no corre — Task 4 crea el
  reusable que referencia; hacer los dos commits antes de probar)

```bash
git add .github/workflows/backup.yml ISSUES.md
git commit -m "feat(backup): workflow diario de pg_dump cifrado a R2"
```

---

## Task 4: Verificación por restore real (reusable workflow)

**Bloqueada por:** Task 3. **Files:** Create `.github/workflows/backup-verify.yml`.

**Interfaces:**
- Consumes: inputs `objeto` (nombre del `.dump.gpg`) y `prefijo`
  (`daily`/`monthly`), provistos por el job `verificar` de `backup.yml`.

- [x] **Step 1: Crear `.github/workflows/backup-verify.yml`**

```yaml
name: Verificación del backup (restore real)

on:
  workflow_call:
    inputs:
      objeto:
        description: Nombre del archivo .dump.gpg a verificar
        required: true
        type: string
      prefijo:
        description: Prefijo en el bucket (daily | monthly)
        required: true
        type: string
  workflow_dispatch:
    inputs:
      objeto:
        description: Nombre del archivo .dump.gpg (vacío = el más reciente de daily/)
        required: false
        type: string
      prefijo:
        description: Prefijo en el bucket
        required: false
        default: daily
        type: string

permissions: {}

jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    services:
      postgres:
        image: postgres:18
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: verify
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      AWS_ACCESS_KEY_ID: ${{ secrets.R2_BACKUPS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_BACKUPS_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION: auto
      ENDPOINT: ${{ secrets.R2_BACKUPS_ENDPOINT_URL }}
      BUCKET: ${{ secrets.R2_BACKUPS_BUCKET_NAME }}
      PGTARGET: postgresql://postgres:postgres@localhost:5432/verify
      OBJETO_IN: ${{ inputs.objeto }}
      PREFIJO: ${{ inputs.prefijo }}
    steps:
      - name: Instalar postgresql-client y verificar herramientas
        run: |
          set -euo pipefail
          sudo install -d /usr/share/postgresql-common/pgdg
          sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
            https://www.postgresql.org/media/keys/ACCC4CF8.asc
          echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
          sudo apt-get update -qq
          sudo apt-get install -y --no-install-recommends postgresql-client-18
          pg_restore --version
          gpg --version | head -1
          aws --version

      - name: Resolver y bajar el objeto
        run: |
          set -euo pipefail
          PREF="${PREFIJO:-daily}"
          OBJ="${OBJETO_IN:-}"
          if [ -z "$OBJ" ]; then
            # Solo en corrida manual sin argumento. La invocación automática
            # SIEMPRE pasa el objeto exacto, así que acá no hay carrera posible.
            OBJ="$(aws s3 ls "s3://$BUCKET/$PREF/" --endpoint-url "$ENDPOINT" \
              | awk '{print $4}' | grep '\.dump\.gpg$' | sort | tail -1)"
          fi
          test -n "$OBJ"
          echo "OBJ=$OBJ" >> "$GITHUB_ENV"
          aws s3 cp "s3://$BUCKET/$PREF/$OBJ" . --endpoint-url "$ENDPOINT"
          aws s3 cp "s3://$BUCKET/$PREF/$OBJ.sha256" . --endpoint-url "$ENDPOINT"
          ls -lh "$OBJ"

      - name: Verificar checksum
        run: |
          set -euo pipefail
          sha256sum -c "$OBJ.sha256"

      - name: Descifrar
        env:
          KEY: ${{ secrets.BACKUP_ENCRYPTION_KEY }}
        run: |
          set -euo pipefail
          printf '%s' "$KEY" | gpg --batch --yes --pinentry-mode loopback \
            --passphrase-fd 0 --output restore.dump --decrypt "$OBJ"

      - name: Restaurar en el container
        run: |
          set -euo pipefail
          pg_restore --no-owner --no-privileges --dbname "$PGTARGET" restore.dump

      - name: Validar el contenido restaurado
        run: |
          set -euo pipefail
          Q() { psql "$PGTARGET" -tAc "$1" | tr -d '[:space:]'; }
          ESENCIALES="$(Q "select count(*) from information_schema.tables
            where table_schema='public' and table_name in (
              'auth_user','tenants_gimnasio','tenants_perfil','alumnos_alumno',
              'rutinas_rutinaasignada','pagos_pagomensual','turnos_reserva',
              'novedades_novedad','importaciones_importacion','django_migrations')")"
          MIGRACIONES="$(Q "select count(*) from django_migrations")"
          USUARIOS="$(Q "select count(*) from auth_user")"
          echo "tablas_esenciales=$ESENCIALES migraciones=$MIGRACIONES usuarios=$USUARIOS"
          # Se nombran las 10 tablas esenciales en vez de exigir un total >= N:
          # un umbral numérico pasa igual si falta justo la tabla que importa.
          test "$ESENCIALES" -eq 10
          # django_migrations poblada = el esquema se restauró completo. No se
          # pinea el nombre de la última migración: cambia en cada feature y
          # volvería frágil la verificación.
          test "$MIGRACIONES" -ge 1
          # No se exigen filas en tablas de dominio: un gimnasio recién dado de
          # alta puede tener 0 alumnos legítimamente, y un falso positivo
          # entrena a ignorar la alerta.
          test "$USUARIOS" -ge 1

      - name: Avisar a Healthchecks
        run: curl -fsS -m 10 --retry 3 "${{ secrets.HEALTHCHECKS_URL_VERIFY }}"
```

- [ ] **Step 2: Commit, push y probar el encadenamiento**
      (commiteado en `80ac21e`; el encadenamiento NO se probó todavía —
      necesita los secrets de M4)

```bash
git add .github/workflows/backup-verify.yml
git commit -m "feat(backup): verificación por restore real, encadenada al backup mensual"
git push
```

Probar en dos pasos:
1. `workflow_dispatch` de "Verificación del backup" sin argumentos → debe
   tomar el último de `daily/` y terminar imprimiendo
   `tablas_esenciales=10 migraciones=<N> usuarios=<N>`.
2. `workflow_dispatch` del backup → debe correr solo el job `backup` (salvo
   que sea día 1, en cuyo caso encadena `verificar`).

---

## Task 5: Workflow de `generar_pagos` (destraba Fase 5)

**Bloqueada por:** Task 2 y el secret `NEON_DATABASE_URL_POOLED`.
**Files:** Create `.github/workflows/generar-pagos.yml`; Modify `render.yaml`,
`CLAUDE.md`.

- [x] **Step 1: Crear `.github/workflows/generar-pagos.yml`**

Reemplazar `<SHA_CHECKOUT>` y `<SHA_SETUP_PYTHON>` por los SHA obtenidos en
Task 3 Step 1 (un tag es mutable; solo el SHA es una referencia inmutable).

```yaml
name: Generar pagos del mes

on:
  schedule:
    - cron: "30 6 * * *"   # 06:30 UTC, después del backup para no solaparse
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: generar-pagos-production
  cancel-in-progress: false

jobs:
  generar:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<SHA_CHECKOUT>          # v4
      - uses: actions/setup-python@<SHA_SETUP_PYTHON>  # v5
        with:
          python-version: "3.14"
          cache: pip
      - run: pip install -r requirements.txt
      # No se pasan las R2_* ni las GOOGLE_*: sin ellas Django cae a
      # FileSystemStorage y deja GOOGLE_CALENDAR_ENABLED=False, y este comando
      # no toca ni archivos ni calendarios. Tampoco DJANGO_SECRET_KEY: un
      # management command no lo usa y hay fallback de dev. Menos secrets
      # copiados = menos superficie. DJANGO_DEBUG sí hace falta: en config/db.py
      # `ssl_require=not debug` depende de ella para exigir TLS contra Neon.
      - name: Correr generar_pagos
        env:
          DATABASE_URL: ${{ secrets.NEON_DATABASE_URL_POOLED }}
          DJANGO_DEBUG: "False"
        run: python manage.py generar_pagos
```

- [x] **Step 2: Borrar el cron comentado de `render.yaml`**

Borrar el bloque completo `# - type: cron ... # name: app-gim-generar-pagos`
del final del archivo — deja de ser un pendiente.

- [x] **Step 3: Actualizar `CLAUDE.md`**

En "Deploy (Fase 5)": reemplazar el ítem que dice que el cron de
`generar_pagos` está pendiente, y agregar una subsección "Respaldos" con los
tres workflows, el bucket `app-gim-backups`, la retención (`daily/` 30 días
por lifecycle, `monthly/` 12 meses por bucket lock) y el monitoreo externo en
Healthchecks. Mencionar explícitamente que la verificación **la encadena el
backup**, no un cron propio, y por qué.

- [ ] **Step 4: Commit, push y correr a mano**
      (falta el secret `NEON_DATABASE_URL_POOLED`)

```bash
git add .github/workflows/generar-pagos.yml render.yaml CLAUDE.md
git commit -m "feat(pagos): correr generar_pagos por GitHub Actions

Destraba el cron frenado desde Fase 5 (Render no tiene cron en free)."
git push
```

Correr por `workflow_dispatch` y confirmar en el panel que los pagos del mes
quedaron generados. El comando es idempotente, así que correrlo dos veces no
duplica nada.

---

## Verificación (end-to-end, antes de dar el proyecto por cerrado)

Contra **producción ya apuntando a Neon**, en este orden:

1. **App**: login de staff, alta y edición de un alumno, reserva de un turno,
   confirmación de un pago con comprobante, y descarga de ese comprobante
   (prueba que Neon y R2 conviven bien).
2. **Google Calendar**: conectar un alumno y confirmar que la reserva aparece
   en el calendario secundario.
3. **Suite**: `python manage.py test` en verde (417 tests).
4. **Backup**: `workflow_dispatch` → objeto en `daily/` + checksum en el log.
5. **Restore**: `workflow_dispatch` de la verificación →
   `tablas_esenciales=10`.
6. **Encadenamiento**: correr el backup con la condición de día 1 forzada
   temporalmente a `true` y confirmar que dispara el job `verificar` con el
   objeto recién escrito. Revertir el cambio después.
7. **Alerta**: bajar temporalmente el período del check de Healthchecks a
   ~1 hora y **no** correr el backup; confirmar que **llega el mail**.
   Pausar el check no sirve — un check pausado no alerta, así que no prueba
   nada. Restaurar el período después.
8. **Retención**: confirmar en Cloudflare que la lifecycle de `daily/` está
   activa y que un objeto de `monthly/` no se puede borrar a mano.
9. **Recién ahí**: borrar la base vieja de Render.

## Riesgos que quedan abiertos

- **Ventana de pérdida de hasta 24 h** (backup diario). Aceptado: el volumen
  de escritura de un gimnasio chico es bajo.
- **La verificación completa corre una vez por mes.** Entre medio, un backup
  diario solo tiene checksum verificado, no restore. Aceptado: un restore
  diario multiplicaría minutos de Actions sin cambiar materialmente el riesgo.
- **Neon free tiene cortes duros** (0.5 GB, 100 CU-hours/mes) y el
  scale-to-zero no se puede desactivar.
- **Si se pierde `BACKUP_ENCRYPTION_KEY`, los backups son ilegibles.** Por eso
  va en dos lugares y la verificación mensual descifra de verdad.
- **Los archivos de media no se copian a ningún lado** — se cubren con
  permisos y bucket lock. Un borrado del bucket `app-gim-media` sigue siendo
  irreversible si el lock no está puesto.
