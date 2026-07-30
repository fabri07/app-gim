# Runbook: puesta en marcha de los respaldos

Pasos manuales (dashboards de terceros) para dejar andando los tres workflows
de `.github/workflows/`. El código ya está en `main`; sin estos pasos los
workflows fallan por falta de secrets.

El diseño y el porqué de cada decisión están en
`docs/superpowers/specs/2026-07-29-respaldo-y-migracion-neon-design.md`. Esto
es solo la secuencia de ejecución.

**El orden importa.** La clave de cifrado se genera primero porque la necesitan
R2 y GitHub; la rotación de Neon va al final porque hay que actualizarla en tres
lugares a la vez; y la base vieja de Render se borra recién cuando un restore
real haya salido bien.

> **Regla que aplica a todo este documento: nunca pegues una URL con contraseña
> adentro en un chat, un issue o un commit.** Ya pasó una vez con la URL de Neon
> (por eso el paso 6 existe). Si se te escapa, rotá la credencial en vez de
> confiar en que nadie la leyó.

---

## Paso 1 — Clave de cifrado de los backups

Los dumps se cifran con GPG simétrico antes de subirlos. **Si perdés esta clave,
todos los backups quedan ilegibles** — no hay recuperación posible.

Generala vos, en tu máquina:

```bash
openssl rand -base64 48
```

Guardala en **dos lugares independientes**, y que ninguno de los dos sea este
repositorio ni un chat:

1. Tu gestor de contraseñas.
2. Algo que sobreviva a perder el acceso a esa cuenta (papel en un cajón, o un
   archivo cifrado en otro dispositivo).

Va a ser el secret `BACKUP_ENCRYPTION_KEY` en el paso 4.

---

## Paso 2 — Cloudflare R2: bucket de backups

**Bucket separado del de media, a propósito.** `app-gim-media` guarda archivos
que la app lee y escribe todo el tiempo; el de backups solo lo tocan los
workflows. Si se compromete el token de la app, no alcanza los respaldos.

1. **Crear el bucket** `app-gim-backups`. Misma cuenta que `app-gim-media`.
   No hace falta acceso público: los workflows entran con el token.

2. **Crear un API token propio**, no reusar el de la app:
   - Permiso: **Object Read & Write** (leer y escribir objetos).
   - **Sin** permisos de administración de buckets (no debe poder borrar el
     bucket entero ni cambiar su configuración).
   - Alcance: **solo el bucket `app-gim-backups`**.

   Anotá `Access Key ID` y `Secret Access Key` — el secret se muestra **una sola
   vez**.

3. **Anotar el endpoint S3** del bucket. Tiene la forma
   `https://<account_id>.r2.cloudflarestorage.com` (el mismo `account_id` que ya
   usás para `app-gim-media`; lo ves en el panel del bucket).

4. **Lifecycle rule sobre `daily/`**: eliminar objetos a los **30 días**.

5. **Bucket lock sobre `monthly/`**: retención de **12 meses**, inmutable.

> **Esto es lo más fácil de arruinar de todo el runbook: el lock va SOLO en
> `monthly/`.** Las reglas de bucket lock tienen **precedencia sobre las de
> lifecycle**. Si le ponés lock a `daily/`, la expiración a 30 días no ocurre
> nunca y el bucket crece sin freno hasta que te cobren. En `monthly/` la
> inmutabilidad es justamente lo que se busca: ni vos podés borrar esas copias.

---

## Paso 3 — Healthchecks.io: los dos checks

Plan Hobbyist (gratis permanente, 20 checks). Esto **no** vigila que el backup
sea bueno — vigila que el backup **haya ocurrido**.

**Por qué está afuera de GitHub:** si Actions se cae, o GitHub desactiva los
workflows programados por inactividad del repo (lo hace tras 60 días sin
commits), un chequeo que corriera adentro tampoco correría, y la alerta que
justifica todo el mecanismo nunca llegaría. Tiene que ser un tercero el que note
la ausencia.

Creá dos checks y anotá la **ping URL** de cada uno:

| Check | Período | Gracia | Secret |
|---|---|---|---|
| Backup diario | 1 día | 6 horas | `HEALTHCHECKS_URL_BACKUP` |
| Verificación mensual | 30 días | 3 días | `HEALTHCHECKS_URL_VERIFY` |

Configurá el mail de notificación a una casilla que leas de verdad.

---

## Paso 4 — GitHub: límite de gasto y secrets

1. **Poné el límite de gasto de Actions en US$0** primero
   (Settings → Billing → Spending limits). El plan Free da 2.000 min/mes en
   repos privados y este uso ronda los 100-150; con el límite en cero, si algo
   se descontrola las corridas se detienen en vez de cobrarte.

2. **Cargá los 9 secrets** en Settings → Secrets and variables → Actions →
   *New repository secret*:

| Secret | De dónde sale |
|---|---|
| `NEON_DATABASE_URL_DIRECT` | Neon, connection string **sin** `-pooler` en el host |
| `NEON_DATABASE_URL_POOLED` | Neon, connection string **con** `-pooler` |
| `BACKUP_ENCRYPTION_KEY` | Paso 1 |
| `R2_BACKUPS_BUCKET_NAME` | `app-gim-backups` |
| `R2_BACKUPS_ACCESS_KEY_ID` | Paso 2 |
| `R2_BACKUPS_SECRET_ACCESS_KEY` | Paso 2 |
| `R2_BACKUPS_ENDPOINT_URL` | Paso 2 |
| `HEALTHCHECKS_URL_BACKUP` | Paso 3 |
| `HEALTHCHECKS_URL_VERIFY` | Paso 3 |

**Directa vs pooled no es intercambiable.** El `pg_dump` abre una sesión larga
que el pooler no sostiene bien, por eso usa la directa. `generar_pagos` hace
conexiones cortas, que es justo para lo que sirve el pooler.

**No cargues `DJANGO_SECRET_KEY`.** La tabla del spec lo lista, pero quedó
desactualizada: un management command no lo usa y el workflow no se lo pasa.
Menos secrets copiados, menos superficie.

---

## Paso 5 — Correr y verificar

En orden, cada uno desde Actions → *Run workflow* (`workflow_dispatch`):

1. **`Generar pagos del mes`.** Confirmá en el panel de la app que los pagos
   pendientes del mes quedaron creados. Es idempotente: correrlo dos veces no
   duplica nada.

2. **`Backup diario de Postgres`.** En el log tenés que ver el tamaño del dump y
   el checksum. Confirmá en Cloudflare que aparecieron **dos** objetos en
   `daily/`: el `.dump.gpg` y su `.sha256`.

3. **`Verificación del backup (restore real)`**, dejando el input `objeto`
   vacío y `prefijo` en `daily` (así toma el más reciente). El log tiene que
   terminar en `tablas_esenciales=10`. **Este es el paso que de verdad importa**:
   un backup que nunca se restauró no está verificado — el archivo puede existir,
   pesar lo esperado y ser inservible.

4. **Probar el encadenamiento del día 1.** En `backup.yml`, cambiá
   temporalmente la condición `[ "$(date -u +%d)" = "01" ]` por algo que dé
   verdadero hoy, corré el workflow, y confirmá que dispara solo el job
   `verificar` sobre el objeto recién escrito en `monthly/`. **Revertí el cambio
   después.**

5. **Probar que la alerta llega.** Bajá temporalmente el período del check de
   backup en Healthchecks a ~1 hora y **no** corras el workflow. Tiene que
   llegarte el mail. Restaurá el período después.
   > **Pausar el check no sirve como prueba**: un check pausado no alerta, así
   > que no demuestra nada. Lo único que prueba que la alerta funciona es que
   > efectivamente llegue el mail.

   *Verificado el 2026-07-30: último ping a las 16:29 UTC, período de 1 h +
   5 min de gracia, mail recibido con `Status Changed to Down at 17:34:24
   UTC` — al minuto que correspondía.*
   > **Acordate de restaurar el período a 1 día / 6 h de gracia.** Si queda en
   > 1 hora, el check se cae de nuevo una hora después de cada backup diario y
   > la alerta se vuelve ruido que se aprende a ignorar.

6. **Confirmar la retención en Cloudflare**: que la lifecycle de `daily/` figure
   activa, y que un objeto de `monthly/` **no se pueda borrar a mano** (probá
   borrarlo: tiene que fallar; si se borra, el lock no quedó puesto).
   > Probá borrar también uno de `daily/`: ese **sí** tiene que borrarse. Lo que
   > valida la configuración es que los dos prefijos se comporten **distinto**.
   > Si ambos fallan o ambos borran, hay algo mal en uno de los dos lados.

   *Verificado el 2026-07-30: `monthly/` rechazó el borrado, `daily/` lo
   aceptó.*

---

## Paso 6 — Rotar la contraseña de Neon

Recién ahora, con todo lo demás cargado y funcionando. La contraseña actual
estuvo expuesta en texto plano en un chat.

1. Reseteá la contraseña del rol en el dashboard de Neon.
2. Actualizá la nueva URL en **los tres lugares**, o algo se rompe:
   - `DATABASE_URL` en Render (la **pooled**) → esto redespliega la app.
   - `NEON_DATABASE_URL_POOLED` en GitHub.
   - `NEON_DATABASE_URL_DIRECT` en GitHub.
3. Verificá las tres: la app responde, `Generar pagos del mes` corre, y
   `Backup diario` corre.

---

## Paso 7 — La base vieja de Render

**Hecho el 2026-07-30: la base `app-gim-db` se borró.** Neon es la única base
del proyecto. Lo que sigue queda como registro de las condiciones que se
exigieron antes de darla de baja.

Condiciones para darla de baja, todas cumplidas:

- [ ] Producción **escribe** en Neon (no solo lee): un login nuevo suma una fila
      en `django_session`.
- [x] Un backup completo salió bien y quedó guardado. *(2026-07-30: está en
      `monthly/app-gim-20260730T162903Z.dump.gpg`, no en `daily/` — los objetos
      de `daily/` se borraron a mano al probar el paso 5.6. El cron diario
      repuebla `daily/` en la corrida siguiente.)*
- [x] Un restore real de ese backup dio `tablas_esenciales=10`. *(2026-07-30,
      encadenado desde el backup: `migraciones=36 usuarios=1` — el `usuarios=1`
      confirma de paso que el dump salió de Neon y no de la base vieja de
      Render.)*
- [x] La alerta de Healthchecks llegó cuando se la forzó a fallar. *(2026-07-30,
      17:34:24 UTC — exactamente el período + la gracia después del último
      ping.)*
- [ ] La contraseña de Neon quedó rotada y los tres lugares actualizados.
      **Ojo: ya no hay base vieja como vuelta atrás.** Verificá las tres
      puntas (app, `generar-pagos`, `backup`) apenas termines de rotar.

Con las cinco tildadas, borrá el Postgres viejo desde el dashboard de Render.

---

## Qué queda cubierto y qué no

**Cubierto:** borrado accidental de datos, corrupción de la base, y que Neon
desaparezca. Ventana de pérdida máxima: 24 horas.

**No cubierto:** los archivos de media (`app-gim-media`) **no se respaldan a
ningún lado**. Se protegen con permisos y con bucket lock, no con copias. Un
borrado de ese bucket sigue siendo irreversible si el lock no está puesto — son
los logos y los comprobantes de pago.
