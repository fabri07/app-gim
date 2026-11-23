"""Catálogo de premios del gimnasio (pieza 5 de reconocimientos)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.models import TenantOwnedModel, TimeStampedModel


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


class PremioOtorgado(TenantOwnedModel):
    """Un premio ganado por un alumno al cerrar un ciclo con asistencia perfecta.

    Los datos del premio se CONGELAN acá (nombre, tipo, valor, cuotas...): editar
    o desactivar el catálogo después no cambia lo que el alumno ya ganó, y
    `premio` es `SET_NULL` por si algún día se borra la fila del catálogo.
    A lo sumo uno vigente (`OTORGADO`) por alumno y ciclo; uno `ANULADO` no
    cuenta y se puede volver a otorgar.
    """

    class Estado(models.TextChoices):
        OTORGADO = "otorgado", "Otorgado"
        ANULADO = "anulado", "Anulado"

    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="premios"
    )
    premio = models.ForeignKey(
        Premio, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Congelados al otorgar.
    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=12, choices=Premio.Tipo.choices)
    valor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cuotas = models.PositiveSmallIntegerField(default=1)
    texto = models.CharField(max_length=200, blank=True)
    da_insignia = models.BooleanField(default=True)
    # El ciclo que se premia y la asistencia que lo justificó.
    periodo_inicio = models.DateField()
    periodo_fin = models.DateField()
    meta = models.PositiveSmallIntegerField()
    asistidas = models.PositiveSmallIntegerField()
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.OTORGADO
    )
    entregado_en = models.DateTimeField(null=True, blank=True)
    insignia_hasta = models.DateField(null=True, blank=True)
    otorgado_por = models.ForeignKey(
        "tenants.Perfil", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "premio otorgado"
        verbose_name_plural = "premios otorgados"
        ordering = ["-periodo_inicio", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "alumno", "periodo_inicio"],
                condition=Q(estado="otorgado"),
                name="premio_otorgado_unico_por_ciclo",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} - {self.alumno_id}"

    @property
    def restantes(self):
        """Cuotas con descuento que le faltan aplicar (nunca negativo). Un
        obsequio no tiene cuotas: 0. Usa `n_aplicaciones` si la consulta lo
        anotó (evita un N+1 en los listados)."""
        if self.tipo == Premio.Tipo.OBSEQUIO:
            return 0
        aplicadas = getattr(self, "n_aplicaciones", None)
        if aplicadas is None:
            aplicadas = self.aplicaciones.count()
        return max(self.cuotas - aplicadas, 0)


class PremioAplicacion(TimeStampedModel):
    """Una cuota a la que se le aplicó el descuento de un premio.

    No es `TenantOwnedModel`: cuelga de `PremioOtorgado` y de `Cuota`, que ya
    llevan el gimnasio (mismo patrón que `NovedadLeida`). Una cuota admite a lo
    sumo un premio.
    """

    otorgado = models.ForeignKey(
        PremioOtorgado, on_delete=models.CASCADE, related_name="aplicaciones"
    )
    cuota = models.ForeignKey("pagos.Cuota", on_delete=models.CASCADE, related_name="+")
    descuento = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "aplicación de premio"
        verbose_name_plural = "aplicaciones de premio"
        constraints = [
            models.UniqueConstraint(fields=["cuota"], name="premio_aplicacion_una_por_cuota"),
        ]
