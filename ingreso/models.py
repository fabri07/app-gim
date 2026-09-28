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


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config de un gimnasio: garantiza que la fila
    exista. Ojo que `turnos.models` tiene una función con este mismo nombre:
    el módulo que necesite las dos las importa calificadas por módulo."""
    config, _ = ConfiguracionIngreso.objects.get_or_create(gimnasio=gimnasio)
    return config
