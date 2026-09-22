"""
Lo que cada gimnasio le pagó a la PLATAFORMA.

**Estos modelos no son `TenantOwnedModel` a propósito**, aunque tengan un FK a
`Gimnasio`. Un `TenantOwnedModel` es un dato *del* gimnasio, que su staff ve y
edita y que le pertenece: por eso se exporta con «Exportar mis datos», se borra
con `vaciar_gimnasio` y entra en los barridos por introspección de
`tenants/tests.py` y `tenants/tests_exportacion.py`. Un `PagoPlataforma` es un
dato *de la plataforma sobre* el gimnasio: lo escribe únicamente el superadmin,
el gimnasio no lo ve en ninguna pantalla, y restaurar la cuenta de demostración
no tiene por qué borrar el historial de lo que facturó el producto. Por eso
queda afuera de los tres lugares donde sí va un modelo tenant-owned nuevo
(`vaciar_gimnasio`, `_ensuciar` y `HOJAS`/`EXCLUIDOS`).
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class PagoPlataforma(TimeStampedModel):
    """Un cobro de la plataforma a un gimnasio, registrado a mano.

    No hay integración de cobros: el superadmin cobra por transferencia y
    carga la fila. Mismo criterio que `pagos.Cuota` dentro del gimnasio
    («primero se cobra, después se sofistica»).

    Los montos se guardan **los dos**, en dólares y en pesos, además del
    `tipo_cambio` del día. No es redundancia: el precio se pacta en USD pero
    lo que de verdad entró a la cuenta son pesos, y con la inflación
    argentina recalcularlos después con la cotización de hoy daría un número
    que nunca existió. `monto_ars` y `tipo_cambio` son opcionales porque la
    API de cotización puede estar caída justo cuando se registra el pago, y
    eso no puede impedir anotarlo.

    `alumnos_activos` es la foto del día: es lo que justifica el escalón de
    precio que se cobró. Sin el número congelado, un gimnasio que crece hace
    que el historial de precios deje de tener explicación.
    """

    gimnasio = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.PROTECT,
        related_name="pagos_plataforma",
    )
    fecha_pago = models.DateField(
        verbose_name="fecha de pago",
        help_text="El día en que entró la plata.",
    )
    monto_usd = models.DecimalField(
        max_digits=8, decimal_places=2, verbose_name="monto en USD"
    )
    monto_ars = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name="monto en pesos",
        help_text="Lo que de verdad se cobró. Opcional si no hay cotización.",
    )
    tipo_cambio = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="tipo de cambio",
        help_text="Dólar MEP (venta) del día del pago.",
    )
    alumnos_activos = models.PositiveIntegerField(
        verbose_name="alumnos activos",
        help_text="Cuántos tenía el gimnasio al cobrarle: justifica el precio.",
    )
    periodo_desde = models.DateField(verbose_name="período desde")
    #: **Inclusivo**, igual que `pagos.Cuota.periodo_fin`: un período de 30
    #: días que arranca el 17/5 termina el 15/6, y el siguiente arranca el
    #: 16/6. `plataforma.precios.periodo_siguiente` lo escribe así y
    #: `cubierto_hasta` lo lee así; si alguna de las dos puntas lo tomara como
    #: exclusivo, todos los gimnasios quedarían corridos un día.
    periodo_hasta = models.DateField(verbose_name="período hasta (inclusive)")
    notas = models.TextField(blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pagos_plataforma_registrados",
        verbose_name="registrado por",
    )

    class Meta:
        verbose_name = "pago de plataforma"
        verbose_name_plural = "pagos de plataforma"
        # Lo primero que se quiere ver en la ficha es el último período
        # cobrado. `-id` desempata dos pagos del mismo período (un `ORDER BY`
        # con empate no garantiza orden en Postgres).
        ordering = ["-periodo_hasta", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(periodo_hasta__gte=models.F("periodo_desde")),
                name="pago_plataforma_periodo_no_invertido",
            )
        ]
        indexes = [
            # La consulta del monitor es `Max(periodo_hasta)` por gimnasio, y
            # corre para TODOS los gimnasios en cada carga del panel.
            models.Index(
                fields=["gimnasio", "periodo_hasta"],
                name="pago_plat_gim_hasta_idx",
            )
        ]

    def __str__(self):
        desde = self.periodo_desde.strftime("%d/%m/%Y")
        hasta = self.periodo_hasta.strftime("%d/%m/%Y")
        return f"{self.gimnasio} · {desde} a {hasta} · USD {self.monto_usd}"
