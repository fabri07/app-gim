"""Asistencia: una marca por alumno y día (la fuente de verdad de «vino a
entrenar»), sea porque fue a un turno reservado o porque el staff la cargó a
mano."""

from django.db import models

from core.models import TenantOwnedModel


class Asistencia(TenantOwnedModel):
    class Origen(models.TextChoices):
        TURNO = "turno", "Turno reservado"
        MANUAL = "manual", "Cargada a mano"

    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.CASCADE,
        related_name="asistencias",
        verbose_name="alumno",
    )
    fecha = models.DateField("fecha")
    #: Franja de la reserva, sólo informativa (una carga manual no la tiene).
    hora = models.TimeField("hora", null=True, blank=True)
    origen = models.CharField(
        "origen", max_length=10, choices=Origen.choices, default=Origen.MANUAL
    )
    registrada_por = models.ForeignKey(
        "tenants.Perfil",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="registrada por",
    )
    creado = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        verbose_name = "asistencia"
        verbose_name_plural = "asistencias"
        ordering = ["-fecha"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "alumno", "fecha"],
                name="asistencia_unica_por_dia",
            ),
        ]
        indexes = [
            models.Index(fields=["gimnasio", "fecha"]),
        ]

    def __str__(self):
        return f"{self.alumno_id} · {self.fecha}"
