"""
Lo que cada gimnasio le pagó a la PLATAFORMA y cuánto la usa.

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
from tenants.models import Perfil


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
            ),
            # Un gimnasio no puede tener dos pagos que arranquen el mismo día.
            # El caso real es el doble submit del formulario (va boosteado por
            # htmx) y el "¿lo habré cargado?" del superadmin: sin esta clave,
            # el segundo pago entra sin ruido, el gimnasio aparece cobrado dos
            # veces y el ingreso del mes queda inflado. No se topa por
            # `periodo_hasta` porque `periodo_desde` es el ancla del ciclo: es
            # lo que `periodo_siguiente` calcula y lo que define el período.
            models.UniqueConstraint(
                fields=["gimnasio", "periodo_desde"],
                name="pago_plataforma_un_periodo_por_gimnasio",
            ),
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


class ActividadDiaria(models.Model):
    """Un día en que una persona usó la app. Una fila por usuario y por día.

    Es la respuesta a «¿este gimnasio sigue vivo?», que es lo único que se
    puede saber sin espiar: no hay pageviews, ni qué miró, ni cuánto tiempo
    estuvo. La escribe `plataforma/middleware.py` en el primer request de cada
    día y nunca se actualiza.

    **`usuario` va CASCADE y `gimnasio` PROTECT, y la asimetría es
    deliberada.** `tenants/demo.py::vaciar_gimnasio` borra los `User` de los
    alumnos de la cuenta de demostración cada 6 horas: con PROTECT ahí, la
    restauración automática se caería con `ProtectedError` y la demo quedaría
    sin restaurar hasta que saltara Healthchecks. El historial de uso de un
    usuario que ya no existe no le sirve a nadie, así que se va con él. El
    `Gimnasio`, en cambio, no se borra nunca por esta vía: PROTECT es el mismo
    criterio que el resto del proyecto.

    **No es `TenantOwnedModel`**, por el mismo motivo que `PagoPlataforma`
    (ver el docstring del módulo): es un dato *de la plataforma sobre* el
    gimnasio, que su staff no ve en ninguna pantalla. Por eso NO va en
    `vaciar_gimnasio`, ni en el fixture `_ensuciar` de `tenants/tests.py`, ni
    en `HOJAS`/`EXCLUIDOS` del exportador -- los tres lugares donde sí entra un
    modelo tenant-owned nuevo. Las filas de los alumnos igual desaparecen al
    vaciar la demo, por el CASCADE de arriba.

    `rol` va copiado y no se lee del `Perfil` en el momento de mirar: un
    alumno al que después se le da acceso de staff (o al revés) no puede
    reescribir retroactivamente a quién se le atribuyó el uso de marzo.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="actividad_diaria",
    )
    gimnasio = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.PROTECT,
        related_name="actividad",
    )
    rol = models.CharField(max_length=10, choices=Perfil.Rol.choices)
    fecha = models.DateField(
        help_text="Día LOCAL (America/Argentina/Buenos_Aires) en que entró."
    )

    class Meta:
        verbose_name = "día de actividad"
        verbose_name_plural = "días de actividad"
        ordering = ["-fecha", "usuario_id"]
        constraints = [
            # Lo que convierte la tabla en "días de uso" y no en un log de
            # requests. Además es el candado del `ignore_conflicts` del
            # middleware: dos pestañas del mismo usuario pueden cruzar el
            # primer request del día, y sin la clave la fila se duplicaría y
            # el gráfico contaría a esa persona dos veces.
            models.UniqueConstraint(
                fields=["usuario", "fecha"], name="actividad_un_dia_por_usuario"
            )
        ]
        indexes = [
            # Las dos consultas del panel (los 30 días de la ficha y el
            # `Max(fecha)` del monitor, que corre para TODOS los gimnasios en
            # cada carga) filtran por gimnasio y ordenan por fecha.
            models.Index(fields=["gimnasio", "fecha"], name="actividad_gim_fecha_idx")
        ]

    def __str__(self):
        return f"{self.usuario} · {self.fecha:%d/%m/%Y} ({self.rol})"
