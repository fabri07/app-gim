"""Catálogo de premios del gimnasio (pieza 5 de reconocimientos)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.models import TenantOwnedModel


class Premio(TenantOwnedModel):
    """Un premio que el gimnasio puede darle a quien completa un ciclo con
    asistencia perfecta.

    Tres tipos: un porcentaje de descuento (1..90) en la cuota, un monto fijo en
    pesos, o un obsequio en especie (`texto`, sin valor ni cuotas). No se borra:
    se desactiva con `activo` (los premios ya otorgados lo referencian). A lo
    sumo uno por gimnasio es `por_defecto`: lo garantiza la constraint parcial.
    """

    class Tipo(models.TextChoices):
        PORCENTAJE = "porcentaje", "Descuento en porcentaje"
        FIJO = "fijo", "Descuento de monto fijo"
        OBSEQUIO = "obsequio", "Obsequio"

    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.PORCENTAJE)
    valor = models.DecimalField(
        "Valor", max_digits=10, decimal_places=2, null=True, blank=True
    )
    cuotas = models.PositiveSmallIntegerField(
        "Cuotas que dura el descuento",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(13)],
    )
    texto = models.CharField("Descripción del obsequio", max_length=200, blank=True)
    da_insignia = models.BooleanField("Da insignia", default=True)
    por_defecto = models.BooleanField("Premio por defecto", default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "premio"
        verbose_name_plural = "premios"
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio"],
                condition=Q(por_defecto=True),
                name="premio_un_solo_por_defecto",
            ),
        ]

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        errores = {}
        if self.tipo == self.Tipo.PORCENTAJE:
            if self.valor is None or not (Decimal(1) <= self.valor <= Decimal(90)):
                errores["valor"] = "El porcentaje tiene que estar entre 1 y 90."
        elif self.tipo == self.Tipo.FIJO:
            if self.valor is None or self.valor <= 0:
                errores["valor"] = "El monto tiene que ser mayor a 0."
        elif self.tipo == self.Tipo.OBSEQUIO:
            if not (self.texto or "").strip():
                errores["texto"] = "Contá en qué consiste el obsequio."
            # Un obsequio no usa valor ni cuotas: se normalizan.
            self.valor = None
            self.cuotas = 1
        if errores:
            raise ValidationError(errores)
