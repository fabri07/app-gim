"""Modelos de la integración con Google Calendar (Parte C).

Ninguno es `TenantOwnedModel`: se scopean a través de su FK
(`alumno`/`reserva`), que ya está acotado por gimnasio -- mismo precedente que
`novedades.NovedadLeida`. La integración es opcional y removible: si se saca la
app `calendario`, ni `alumnos` ni `turnos` cambian de esquema.
"""

from django.db import models
from django.utils.timezone import now

from core.models import TimeStampedModel
from calendario.fields import EncryptedTextField


class GoogleCalendarCredential(TimeStampedModel):
    """Tokens OAuth de un alumno para su Google Calendar + el id del calendario
    secundario ("Turnos de {gimnasio}") que la app creó para volcar los eventos.

    `refresh_token`/`access_token` van cifrados (`EncryptedTextField`). El scope
    usado es `calendar.app.created`, que NO da acceso al calendario principal:
    solo a calendarios creados por la app.
    """

    alumno = models.OneToOneField(
        "alumnos.Alumno",
        on_delete=models.CASCADE,
        related_name="credencial_calendar",
    )
    refresh_token = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True, default="")
    google_calendar_id = models.CharField(max_length=255, blank=True, default="")
    google_calendar_summary = models.CharField(max_length=255, blank=True, default="")
    connected_at = models.DateTimeField(default=now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "credencial de Google Calendar"
        verbose_name_plural = "credenciales de Google Calendar"

    def __str__(self):
        return f"Google Calendar de {self.alumno}"

    @property
    def esta_conectada(self) -> bool:
        return self.revoked_at is None and bool(self.refresh_token)


class ReservaCalendarEvent(TimeStampedModel):
    """Vínculo 1:1 entre una `Reserva` y el evento que la representa en el
    calendario secundario del alumno. Guarda el estado de la última sync para
    poder mostrar/reintentar desde el portal."""

    class SyncStatus(models.TextChoices):
        OK = "ok", "Sincronizado"
        PENDING = "pending", "Pendiente"
        ERROR = "error", "Error"

    reserva = models.OneToOneField(
        "turnos.Reserva",
        on_delete=models.CASCADE,
        related_name="calendar_event",
    )
    google_event_id = models.CharField(max_length=255, blank=True, default="")
    sync_status = models.CharField(
        max_length=10, choices=SyncStatus.choices, default=SyncStatus.PENDING
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Solo código + mensaje corto: NUNCA tokens ni la respuesta completa de la API.
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        verbose_name = "evento de calendario"
        verbose_name_plural = "eventos de calendario"

    def __str__(self):
        return f"Evento de calendario de {self.reserva}"
