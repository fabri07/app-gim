# Plan de implementación — Parte C: Google Calendar del alumno

Spec: `docs/superpowers/specs/2026-07-07-google-calendar-alumno-design.md`.
TDD (API de Google mockeada). Rama `google-calendar-alumno`, stackeada sobre B.

## C0 — Base
Rama; deps (`google-auth`, `google-auth-oauthlib`, `google-api-python-client`,
`cryptography`) en `requirements.txt` (instalar con `--only-binary :all:`, ver
ISSUES.md); `startapp calendario` + `INSTALLED_APPS` + `config/urls.py`; settings
`GOOGLE_*` (patrón R2).

## C1 — Conexión OAuth
`calendario/fields.py` (`EncryptedTextField`, Fernet); `models.py`
(`GoogleCalendarCredential`, `ReservaCalendarEvent`) + migración `0001`;
`services.py` OAuth (build_authorization_url/intercambiar_code/guardar_credencial/
get_calendar_service/asegurar_calendario_secundario/revocar/
borrar_calendario_secundario); `views.py`
(Conectar/Callback con backfill/Desconectar/Reintentar) + `urls.py`; card en
`templates/turnos/mis_turnos.html` + contexto en `MisTurnosView`.

## C2 — Sync de reservas
`signals.py` (post_save/pre_delete Reserva, pre_save/post_save ConfiguracionTurnos)
conectados en `apps.py::ready`; `services.py` eventos (crear/actualizar/borrar/
sincronizar_reservas_futuras/resync_duracion), best-effort + on_commit + idempotencia.

## C3 — Cierre
`.env.example` + `render.yaml` + `ISSUES.md`; spec/plan; suite completa verde;
commit(s) en la rama.
