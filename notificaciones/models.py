"""PWA instalable + Web Push para app_gim.

`SuscripcionPush` es `TenantOwnedModel` (no solo scopeada vía FK a
`usuario`), mismo argumento que `tenants.RegistroSuplantacion`: la consulta
natural de un envío masivo (broadcast de novedad, aviso a todo el staff) es
"todas las suscripciones de MI gimnasio", no "las de este usuario".
`RecordatorioEnviado` es el mecanismo de dedup para los eventos que dispara
el cron (`enviar_recordatorios`) -- modelo satélite acá, sin tocar el
esquema de Novedad/Cuota/Reserva, mismo patrón que
`calendario.ReservaCalendarEvent`.
"""

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models

from core.models import TenantOwnedModel


class SuscripcionPush(TenantOwnedModel):
    """Una suscripción Web Push de UN dispositivo/navegador de UN usuario
    (staff o alumno). Un usuario puede tener varias filas (celular + PC): NO
    es OneToOne. `endpoint` es único a nivel de la Push API del navegador
    (lo emite el push service del browser), así que `update_or_create`
    sobre `endpoint` maneja tanto la resuscripción del mismo dispositivo
    como el caso de un dispositivo compartido donde inicia sesión otra
    persona: el endpoint se reasigna al usuario nuevo, que es lo único
    físicamente correcto (el browser no puede entregarle el mismo endpoint
    a dos personas a la vez).

    `gimnasio` se stampa en el alta desde `usuario.perfil.gimnasio`.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suscripciones_push",
    )
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=200, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "suscripción push"
        verbose_name_plural = "suscripciones push"
        ordering = ["-creado"]

    def __str__(self):
        return f"Push de {self.usuario} ({self.endpoint[:40]}…)"

    def clean(self):
        super().clean()
        if self.gimnasio_id and self.usuario_id:
            try:
                perfil_gimnasio_id = self.usuario.perfil.gimnasio_id
            except ObjectDoesNotExist:
                return
            if perfil_gimnasio_id != self.gimnasio_id:
                raise ValidationError(
                    {"usuario": "El usuario pertenece a otro gimnasio."}
                )


class RecordatorioEnviado(TenantOwnedModel):
    """Marca "ya se envió este recordatorio" para los eventos disparados por
    el cron (`enviar_recordatorios`, cada ~15 min), que necesita ser
    idempotente sin agregar campos "ya notificado" a Novedad/Cuota/
    Reserva.
    """

    class Tipo(models.TextChoices):
        NOVEDAD_PUBLICADA = "novedad_publicada", "Novedad publicada"
        PAGO_POR_VENCER = "pago_por_vencer", "Pago por vencer"
        PAGO_VENCIDO = "pago_vencido", "Pago vencido"
        TURNO_PROXIMO = "turno_proximo", "Turno próximo"
        RUTINA_INICIADA = "rutina_iniciada", "Rutina iniciada"

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    objeto_id = models.PositiveBigIntegerField()

    class Meta:
        verbose_name = "recordatorio enviado"
        verbose_name_plural = "recordatorios enviados"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "tipo", "objeto_id"],
                name="recordatorio_unico_por_gimnasio_tipo_objeto",
            )
        ]

    def __str__(self):
        return f"{self.tipo} #{self.objeto_id} ({self.gimnasio})"
