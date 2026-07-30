# Respaldo automático de datos + migración de Postgres a Neon

## Contexto

app_gim está desplegada (`https://app-gim.onrender.com`) sobre el free tier
de Render, con la base de datos creada por el Blueprint (`render.yaml`,
`plan: free`) y los archivos de usuario en Cloudflare R2
(`app-gim-media`). Hoy **no existe ningún respaldo**: el único management
command del proyecto es `generar_pagos`, y no hay export, dump ni copia de
la base en ninguna parte.

Eso convive con dos relojes:

1. **El Postgres free de Render expira.** `render.yaml` e `ISSUES.md`
   (`[2026-07-01]`) dicen "90 días", pero Render bajó el plazo a **30 días
   más 14 de gracia** en mayo de 2024. Los dos archivos están
   desactualizados y se corrigen como parte de este trabajo.
2. **No hay red de seguridad ante error humano.** Aun con una base que no
   expire, hoy un borrado accidental desde `/admin/` o desde el panel es
   irreversible.

El disparador fue una pregunta del dueño del producto ("¿todos los datos se
guardan en R2?"). La respuesta es no —  R2 guarda solo los tres campos de
archivo (`Gimnasio.logo`, `PagoMensual.comprobante`,
`Importacion.archivo`); todo el resto vive en Postgres — y al revisarlo
apareció que ese Postgres no tenía respaldo ni permanencia.

## Alcance

**Entra:**

- Migrar la base de Render a **Neon** (free tier permanente).
- Backup diario, cifrado, de Postgres a un bucket R2 **separado** del de
  media.
- Verificación mensual automática que restaura el backup de verdad.
- Alerta por **ausencia** de backup, originada fuera de GitHub.
- Desbloquear el cron de `generar_pagos`, frenado desde Fase 5 porque
  Render no ofrece cron en el plan free.

**No entra:**

- **Auditoría de cambios** (quién modificó qué y cuándo). Es el proyecto #3
  del mismo pedido, tiene su propio spec: toca todos los modelos y no
  protege contra ninguno de los riesgos de arriba.
- **Export a Excel/CSV para el dueño del gimnasio.** Es una feature de
  producto, no una red de seguridad.
- **Copia de los archivos de media.** Se cubre con permisos y retención
  (ver Decisión 8), no con código.

## Decisiones

1. **La base va a Neon free, no a Render Basic.** Neon free es permanente
   (no expira), 0.5 GB por proyecto, uso comercial permitido. Render Basic
   (~US$6/mes + US$0.30/GB de disco) resolvía lo mismo con menos trabajo,
   pero el proyecto todavía no tiene gimnasios pagos y el principio no
   negociable #7 es "primero se cobra, después se sofistica". 0.5 GB sobra
   para datos de texto de unos pocos gimnasios.

2. **Backup con `pg_dump --format=custom`, nunca con `dumpdata`.**
   `loaddata` dispara `post_save` con `raw=True`, y
   `calendario/signals.py:24` (`sync_reserva_guardada`) **no chequea
   `raw`** — restaurar por esa vía intentaría sincronizar cada `Reserva`
   contra la API de Google Calendar y crearía eventos duplicados en los
   calendarios reales de los alumnos. `pg_dump`/`pg_restore` no ejecutan
   código de Django, así que el problema no existe. (No se arregla el
   signal en este proyecto: `raw` solo aparece vía `loaddata`, que
   deliberadamente dejamos de usar. Queda anotado en `ISSUES.md` por si
   alguien reintroduce `loaddata` en el futuro.)

3. **El scheduler es GitHub Actions, no Render Cron.** Render no tiene cron
   free. GitHub Free da 2.000 minutos Linux/mes en repos privados; un dump
   diario más una restauración mensual queda holgadamente adentro. Además
   el backup queda **fuera** de la plataforma que corre la app.

4. **Límite de gasto en US$0** en la cuenta de GitHub. Si algún día se
   agota la cuota, los workflows se detienen en vez de generar cargos.

5. **Cuatro plataformas, ninguna con poder para destruir las dos mitades.**
   Render corre la app, Neon guarda los datos, R2 guarda los backups,
   GitHub orquesta. El token de la app no accede al bucket de backups; el
   token del workflow no accede al bucket de media; ninguno de los dos
   administra buckets.

6. **El dump nunca se guarda como artifact de GitHub.** Va cifrado directo
   a R2. En los logs de Actions queda solo metadata sin datos personales:
   si el dump terminó, tamaño, checksum SHA-256, si la subida a R2 fue OK,
   y en la verificación mensual los conteos de validación.

7. **El dump se cifra antes de subirlo**, con `gpg --symmetric --batch`.
   Contiene datos personales, de salud (la ficha de inscripción tiene
   discapacidad y enfermedad crónica), pagos e interacciones. Se usa `gpg`
   y no `age` porque viene preinstalado en los runners de Ubuntu: un paso
   menos que puede fallar.

8. **Los archivos de media se protegen con permisos y retención, no con
   copias.** R2 **no ofrece object versioning**; su documentación advierte
   que un borrado es irreversible. El mecanismo correcto son los **Bucket
   Locks**. El token de la app queda sin permiso de administración de
   bucket.

9. **`generar_pagos` va en su propio workflow**, separado del de backup.
   Comparte infraestructura y secrets, pero una falla en la generación de
   pagos no debe interferir con los respaldos ni al revés.

## Arquitectura

```
Render (web service, free)
    │  DATABASE_URL = URL POOLED
    ▼
Neon (Postgres free, permanente)
    ▲
    │  URL DIRECTA (no pooled)
    │
GitHub Actions ──pg_dump──▶ gpg ──▶ R2 `app-gim-backups`
    │                                        (token propio del workflow)
    │
    └──ping tras éxito──▶ Healthchecks.io ──alerta por mail si NO llega──▶ dueño

R2 `app-gim-media`  ◀── token de la app (sin permiso de admin de bucket)
```

**Pooled vs directa**: la app usa la URL pooled de Neon (muchas conexiones
cortas). `pg_dump` usa la **directa**, porque el pooler no sostiene bien la
sesión larga de un dump.

## Componentes

### 1. `.github/workflows/backup.yml` — diario

Cron a las **06:00 UTC** (03:00 ART, fuera del horario de cualquier
gimnasio). Con `workflow_dispatch` para poder correrlo a mano.

Pasos:

1. Instalar el cliente de PostgreSQL **pineado a la major de Neon**. La
   versión va fija y comentada, nunca `latest`: `pg_dump` no puede volcar
   un servidor de una major posterior a la suya, y ese error aparece recién
   contra la base real.
2. `pg_dump --format=custom` contra `NEON_DATABASE_URL_DIRECT`.
3. `gpg --symmetric --batch --passphrase "$BACKUP_ENCRYPTION_KEY"`.
4. `sha256sum` del archivo **ya cifrado** → al log y a un objeto hermano
   `.sha256`. Se calcula sobre el cifrado, no sobre el dump plano, porque
   lo que hay que detectar es una subida truncada o corrupta, y así se
   verifica antes de descifrar.
5. Subir a R2 (`aws s3 cp` con `--endpoint-url` de R2) el par
   `daily/app-gim-<ISO8601>.dump.gpg` + `.sha256`. **El día 1 del mes**,
   subir además la misma copia a `monthly/`.
6. Ping a Healthchecks solo si todos los pasos anteriores salieron bien.

### 2. `.github/workflows/backup-verify.yml` — mensual

Levanta un **service container de Postgres** de la misma major que Neon,
baja el último objeto de `daily/`, verifica el checksum, descifra, hace
`pg_restore` contra el container y corre conteos mínimos (`auth_user`,
`alumnos_alumno`, `pagos_pagomensual` > 0).

Se usa un container y no una branch de Neon a propósito: no consume la
cuota free, no toca nada real, y ejercita el camino completo **incluyendo
el descifrado** — que es exactamente donde un backup "existente" resulta
inservible.

Tiene su propio check en Healthchecks (período 30 días, gracia 3 días).

### 3. `.github/workflows/generar-pagos.yml` — diario

Reemplaza el cron job comentado en `render.yaml`. Instala
`requirements.txt`, y corre `python manage.py generar_pagos` con
`DATABASE_URL` = URL **pooled**, `DJANGO_SECRET_KEY` y `DJANGO_DEBUG=False`.
Las 4 `R2_*` y las 4 `GOOGLE_*` se omiten: sin ellas Django cae a
`FileSystemStorage` y deja `GOOGLE_CALENDAR_ENABLED = False`, y este
comando no toca ni archivos ni calendarios.

Al agregarlo, el bloque comentado del cron en `render.yaml` se borra (deja
de ser un pendiente).

### 4. Bucket `app-gim-backups` y retención

| Prefijo | Qué sube ahí | Mecanismo | Retención |
|---|---|---|---|
| `daily/` | todos los días | lifecycle rule | 30 días |
| `monthly/` | solo el día 1 | **bucket lock** | 12 meses, inmutable |

Se descarta a propósito el nivel semanal: con 30 diarios se cubre el error
reciente y con 12 mensuales el descubrimiento tardío; un nivel intermedio
agrega prefijo, regla y lógica sin cubrir ningún caso nuevo.

**El lock va solo en `monthly/`.** Las reglas de bucket lock **tienen
precedencia sobre las de lifecycle**: si se le pusiera lock a `daily/`, la
expiración a 30 días no ocurriría nunca y el bucket crecería sin límite.
En `monthly/` la inmutabilidad es justamente lo que se busca — ni el dueño
de la cuenta puede borrar esas copias.

### 5. Secrets de GitHub

| Secret | Uso |
|---|---|
| `NEON_DATABASE_URL_DIRECT` | `pg_dump` |
| `NEON_DATABASE_URL_POOLED` | `generar_pagos` |
| `BACKUP_ENCRYPTION_KEY` | passphrase de `gpg` |
| `R2_BACKUPS_*` (4) | credenciales del bucket de backups |
| `HEALTHCHECKS_URL_BACKUP` / `_VERIFY` | pings |
| `DJANGO_SECRET_KEY` | `generar_pagos` |

## Manejo de errores y monitoreo

- **La alerta por ausencia vive fuera de GitHub.** Healthchecks.io recibe
  un ping tras cada backup exitoso; si no llega dentro de la ventana,
  alerta por mail. Un chequeo que corriera dentro de Actions no serviría:
  si Actions se desactiva o GitHub está caído, ese chequeo tampoco corre y
  la alerta nunca llega. Plan Hobbyist: gratis permanente, 20 chequeos,
  100 entradas de log por job.
- **Riesgo que esto cubre y que de otro modo es invisible:** GitHub
  desactiva automáticamente los workflows programados tras 60 días sin
  actividad de commits. La documentación lo enuncia para repos públicos y
  los reportes sobre repos privados son contradictorios — el diseño no
  depende de resolver esa ambigüedad, porque el monitoreo externo detecta
  igual el caso.
- **Fallo de un workflow** → GitHub manda mail al dueño del repo por
  defecto. Alcanza; no se construyen notificaciones propias.
- **Sin reintentos automáticos** en el dump. Si falla dos días seguidos hay
  que enterarse, no que se autorepare y enmascare el problema.
- **La clave de cifrado va en dos lugares**: secret de GitHub y gestor de
  contraseñas del dueño. Es el riesgo que introduce cifrar — un backup que
  no se puede descifrar no es un backup. Por eso la verificación mensual
  descifra de verdad y no se conforma con listar el archivo.

## Testing

El grueso del trabajo son workflows de CI, que no son unit-testeables desde
la suite de Django. La estrategia:

- Ambos workflows llevan `workflow_dispatch` para ejecución manual.
- El ciclo completo (dump → cifrado → subida → restore en container →
  conteos) se corre **a mano al menos una vez** antes de dar el proyecto
  por cerrado.
- El único cambio de código de aplicación (`CONN_HEALTH_CHECKS = True`)
  entra con la suite existente en verde (414 tests).

## Pre-requisito: migración a Neon

La base de producción está prácticamente vacía (recién desplegada), así que
no hay transferencia de datos: se crea limpia y `migrate` la reconstruye.

1. Crear el proyecto en Neon; anotar la URL pooled y la directa.
2. `render.yaml`: sacar el bloque `fromDatabase` de `DATABASE_URL` y
   dejarla como `sync: false`.
3. Cargar la URL **pooled** en el dashboard de Render. Redeploy — el
   `buildCommand` ya corre `migrate`.
4. `createsuperuser` desde la Shell de Render.
5. `config/settings.py`: agregar **`CONN_HEALTH_CHECKS = True`**. Sin esto,
   el scale-to-zero de Neon suspende el compute y Django reusa conexiones
   muertas del pool de `conn_max_age=600`, con errores intermitentes.
6. Corregir el "90 días" en `render.yaml` e `ISSUES.md`.

**La base de Render NO se borra** hasta verificar, sobre Neon: alta y
modificación de alumno, reserva de turno, confirmación de pago, subida y
descarga de archivo, sincronización con Google Calendar, primer backup
subido, y primera restauración exitosa. Hasta ese punto, mantenerla es la
única vía de rollback.

La recuperación instantánea de Neon es primera línea, no reemplazo del
backup externo: en free la ventana es chica y depende del volumen de
cambios.

## Riesgos aceptados

1. **Neon free tiene cortes duros** (0.5 GB, 100 CU-hours/mes) y el
   scale-to-zero no se puede desactivar. Si se superan, el servicio se
   detiene. Mitigación: el volumen actual es una fracción mínima de eso, y
   el web service free de Render ya se duerme, así que el arranque en frío
   no es un comportamiento nuevo para el usuario.
2. **El `.env` local escribe al bucket de media de producción** (ver
   `ISSUES.md [2026-07-29]`). No lo cambia este proyecto.
3. **La ventana de pérdida es de hasta 24 h** (backup diario). Aceptado: el
   volumen de escritura de un gimnasio chico es bajo y un backup horario
   multiplicaría ejecuciones sin beneficio proporcional.

## Criterios de salida

- [ ] La app corre contra Neon y la base de Render fue dada de baja tras la
      checklist de verificación completa.
- [x] `CONN_HEALTH_CHECKS = True` y suite en verde.
- [ ] Un backup diario cifrado aparece en `daily/` de `app-gim-backups`.
- [ ] Una copia mensual quedó en `monthly/` con bucket lock activo.
- [ ] La verificación mensual restauró un dump real y los conteos dieron
      distinto de cero.
- [ ] Healthchecks alerta si se lo fuerza a fallar (probado a propósito).
- [ ] `generar_pagos` corre por workflow y el cron comentado desapareció de
      `render.yaml`.
- [ ] Límite de gasto de GitHub en US$0.
