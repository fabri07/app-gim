# Integración con Google Calendar del alumno (Parte C)

## Contexto

Tercera parte del trabajo de "reservas + avisos" (A: reconciliación; B: aviso
por Novedad personal). C sincroniza los turnos del alumno con **su** Google
Calendar (OAuth por usuario). Premisa: la app del gimnasio es SIEMPRE la fuente
de verdad; Google Calendar es un **mirror opcional**. Si el alumno no conecta su
cuenta, o las credenciales OAuth globales no están, todo degrada al deep-link
actual ("Agregar a Google Calendar", `templates/turnos/mis_turnos.html`).

## Decisiones

- Calendario de **cada alumno** (no el del gimnasio). OAuth por usuario.
- Sync **síncrono best-effort**: llamadas inline con `transaction.on_commit`,
  envueltas en try/except; un fallo de Google nunca rompe la reserva.
- Scope **`calendar.app.created`**: NO da acceso al calendario principal — solo
  a calendarios creados por la app. Por eso se crea un calendario secundario
  "Turnos de {gimnasio}" y todos los eventos van ahí.
  (https://developers.google.com/workspace/calendar/api/auth)
- Tokens **cifrados en reposo** (Fernet, `GOOGLE_TOKEN_ENCRYPTION_KEY`).
- Al desconectar: borrar el calendario secundario (best-effort), revocar el
  token, limpiar lo local.
- Producción arranca en modo OAuth **"Testing"** de Google (hasta 100 usuarios,
  sin verificación/CASA).

## Arquitectura: app `calendario`, desacoplada por signals

Todo vive en la app nueva `calendario` (removible). El enganche con `turnos` es
por **signals** (`turnos/services.py` NO se toca):

- `post_save` en `Reserva`: `created` → crear evento; `not created and
  (update_fields is None or "hora_inicio" in update_fields)` → actualizar.
- `pre_delete` en `Reserva`: captura `google_event_id` antes del cascade y borra
  el evento tras el commit. Cubre `cancelar_reserva` y las cancelaciones de la
  reconciliación.
- `pre_save`/`post_save` en `ConfiguracionTurnos`: detecta cambio de
  `duracion_minutos` (guarda el viejo en pre_save) y resincroniza el `dtend` de
  los eventos futuros.

Límite conocido: los signals corren por instancia; `QuerySet.update()`/
`bulk_create` no los disparan.

## Modelos (`calendario/models.py`, scopeados vía FK, no `TenantOwnedModel`)

- `GoogleCalendarCredential`: `alumno OneToOne`, `refresh_token`/`access_token`
  (`EncryptedTextField`, `calendario/fields.py`), `expires_at`, `scopes`,
  `google_calendar_id`, `google_calendar_summary`, `connected_at`, `revoked_at`.
  Propiedad `esta_conectada`.
- `ReservaCalendarEvent`: `reserva OneToOne`, `google_event_id`, `sync_status`
  (ok/pending/error), `last_synced_at`, `last_error` (código + mensaje corto,
  nunca tokens ni respuestas completas).

## Servicio (`calendario/services.py`)

OAuth: `build_authorization_url` (`access_type=offline`, `include_granted_scopes`,
`prompt=consent`), `intercambiar_code`, `guardar_credencial` (conserva el
refresh_token previo si Google no lo reenvía), `get_calendar_service` (refresca el
access token si venció), `asegurar_calendario_secundario`, `revocar`,
`borrar_calendario_secundario`.

Eventos (best-effort, idempotentes): `crear_evento` (si ya existe evento,
actualiza; usa `extendedProperties.private` con `reserva_id`/`gimnasio_id`),
`actualizar_evento`, `borrar_evento`, `sincronizar_reservas_futuras` (backfill al
conectar / reintentar), `resync_duracion`.

## Vistas (`calendario/views.py`, `AlumnoRequiredMixin`)

`ConectarCalendarioView` (state en sesión con expiración), `CalendarioCallbackView`
(valida state, guarda credencial, provisiona calendario, **backfillea las reservas
futuras**), `DesconectarCalendarioView` (POST), `ReintentarSyncView` (POST).

## Portal

`MisTurnosView` expone estado de la integración; `mis_turnos.html` muestra
Conectar / estado conectado ("Sincronizando en 'Turnos de {gimnasio}'" +
Desconectar) y "Reintentar sincronización" si hay `sync_status=error`.

## Settings / deps / pasos manuales

`config/settings.py`: `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`,
`GOOGLE_TOKEN_ENCRYPTION_KEY`, `GOOGLE_CALENDAR_SCOPES`, `GOOGLE_CALENDAR_ENABLED`
(patrón todo-o-nada de R2). `requirements.txt`: google-auth, google-auth-oauthlib,
google-api-python-client, cryptography. Pasos manuales (Google Cloud Console + env
vars `sync:false` en `render.yaml`) documentados en CLAUDE.md/ISSUES.md.

## Tests (Google API mockeada)

Campo cifrado (round-trip + no-plano), disponibilidad, vistas conectar/callback/
desconectar (incluye backfill y state inválido), y el ciclo de sync por signals:
crear/actualizar/borrar, reconciliación migrada/cancelada, cambio de duración,
idempotencia, error de API → `sync_status=error` sin romper la reserva,
integración apagada → sin llamadas. Los tests de sync usan
`captureOnCommitCallbacks(execute=True)`.
