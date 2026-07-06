"""
Modelos de turnos/reservas (Fase 6).

`ConfiguracionTurnos` fija la duración de clase y el cupo por defecto de un
gimnasio; `HorarioAtencion` define qué franjas horarias existen por día de la
semana; `CupoExcepcion` permite pisar el cupo default en un día/horario
puntual (incluso a 0, para bloquear una franja); `Reserva` es la fila que un
alumno ocupa en una franja concreta.
"""

from django.core.validators import MinValueValidator
from django.db import models

from core.models import TenantOwnedModel


class DiaSemana(models.IntegerChoices):
    LUNES = 0, "Lunes"
    MARTES = 1, "Martes"
    MIERCOLES = 2, "Miércoles"
    JUEVES = 3, "Jueves"
    VIERNES = 4, "Viernes"
    SABADO = 5, "Sábado"
    DOMINGO = 6, "Domingo"
    # Alineado con date.weekday() (0=Lunes) -- NO con isoweekday().


DURACION_CHOICES = [(m, f"{m} minutos") for m in range(15, 181, 15)]


class ConfiguracionTurnos(TenantOwnedModel):
    duracion_minutos = models.PositiveSmallIntegerField(
        choices=DURACION_CHOICES, default=60
    )
    vacantes_default = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = "configuración de turnos"
        verbose_name_plural = "configuraciones de turnos"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio"], name="config_turnos_unica_por_gimnasio"
            )
        ]

    def __str__(self):
        return f"Configuración de turnos de {self.gimnasio}"


def obtener_configuracion(gimnasio):
    """Única vía de acceso a la config de un gimnasio: garantiza que la fila exista
    (para poder tomar select_for_update() sobre ella más adelante)."""
    config, _ = ConfiguracionTurnos.objects.get_or_create(gimnasio=gimnasio)
    return config


class HorarioAtencion(TenantOwnedModel):
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_desde = models.TimeField()
    hora_hasta = models.TimeField()

    class Meta:
        verbose_name = "horario de atención"
        verbose_name_plural = "horarios de atención"
        ordering = ["dia_semana", "hora_desde"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hora_desde__lt=models.F("hora_hasta")),
                name="horario_desde_antes_de_hasta",
            )
        ]

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora_desde}-{self.hora_hasta}"


class CupoExcepcion(TenantOwnedModel):
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField()
    vacantes = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "excepción de cupo"
        verbose_name_plural = "excepciones de cupo"
        ordering = ["dia_semana", "hora_inicio"]
        unique_together = ("gimnasio", "dia_semana", "hora_inicio")

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora_inicio} -> {self.vacantes}"


class Reserva(TenantOwnedModel):
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="reservas"
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()

    class Meta:
        verbose_name = "reserva"
        verbose_name_plural = "reservas"
        ordering = ["fecha", "hora_inicio"]
        unique_together = ("gimnasio", "alumno", "fecha", "hora_inicio")
        indexes = [models.Index(fields=["gimnasio", "fecha"])]

    def __str__(self):
        return f"{self.alumno} - {self.fecha} {self.hora_inicio}"
