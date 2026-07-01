"""
Modelo de dominio: el alumno de un gimnasio.

`Alumno` es el primer `TenantOwnedModel` concreto del dominio (Fase 1). Hereda
de `core.models.TenantOwnedModel` el FK `gimnasio` (aislamiento por fila) y los
timestamps de `TimeStampedModel` (`creado` hace de fecha de alta; ver mismo
criterio en `Gimnasio`, no se duplica un campo `fecha_alta` aparte).
"""

from django.db import models

from core.models import TenantOwnedModel


class Alumno(TenantOwnedModel):
    """Una persona entrenada por el gimnasio.

    `email` y `telefono` son `blank=True` porque no todo alumno tiene los
    dos datos de contacto (algunos gimnasios sólo tienen el teléfono, p.ej.
    tras migrar desde un Excel incompleto). El magic-link de Fase 3 podrá
    enviarse por cualquiera de los dos medios, según cuál exista.

    `fecha_activacion` queda en `None` hasta que el alumno usa por primera
    vez su magic-link (Fase 3); permite medir adopción real, no sólo alta
    administrativa.
    """

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        INACTIVO = "inactivo", "Inactivo"

    nombre = models.CharField(max_length=120)
    apellido = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.ACTIVO
    )
    observaciones = models.TextField(blank=True)
    fecha_activacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cuándo entró por primera vez con su magic-link (Fase 3).",
    )

    class Meta:
        verbose_name = "alumno"
        verbose_name_plural = "alumnos"
        ordering = ["apellido", "nombre"]

    def __str__(self):
        return f"{self.apellido}, {self.nombre}"
