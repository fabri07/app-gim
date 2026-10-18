"""Cobro online de la suscripción de un gimnasio a la plataforma.

`IntentoDeCobro` es el precio congelado en el momento en que se generó un link
de pago de Mercado Pago: cuánto, por qué período, a qué cotización y cuántos
alumnos activos había. Cuando el pago se acredita, el intento se convierte en
un `plataforma.PagoPlataforma` (la fuente de verdad de lo facturado).

**No es `TenantOwnedModel`**: es un dato de la plataforma SOBRE el gimnasio, no
un dato del gimnasio (mismo criterio que `plataforma.PagoPlataforma` y
`solicitudes`). Por eso queda fuera de `tenants/demo.py::vaciar_gimnasio`, del
fixture `_ensuciar` y de `HOJAS`/`EXCLUIDOS` del exportador.

**No tiene FK a `User`**, a propósito: `vaciar_gimnasio` borra los `User` de la
cuenta demo cada 6 h (`restaurar-demo.yml`). Con PROTECT ese cron se cae con
`ProtectedError`; con CASCADE se borra un registro de plata. Es la lección de
`plataforma.ActividadDiaria`. Quién apretó «Pagar» queda como texto en
`email_de_quien_pago`.
"""

import uuid

from django.db import models
from django.db.models import F, Q

from core.models import TimeStampedModel


class IntentoDeCobro(TimeStampedModel):
    class Plan(models.TextChoices):
        MENSUAL = "mensual", "Mensual"
        ANUAL = "anual", "Anual"

    class Estado(models.TextChoices):
        CREADO = "creado", "Link generado"
        PAGADO = "pagado", "Pagado"
        RECHAZADO = "rechazado", "Rechazado"
        VENCIDO = "vencido", "Link vencido"
        CANCELADO = "cancelado", "Cancelado"
        DUPLICADO = "duplicado", "Pago duplicado"
        REVISAR = "revisar", "A revisar"
        DEVUELTO = "devuelto", "Devuelto"

    gimnasio = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.PROTECT,
        related_name="intentos_de_cobro",
    )
    plan = models.CharField(max_length=10, choices=Plan.choices)
    periodo_desde = models.DateField()
    # INCLUSIVO, igual que `PagoPlataforma.periodo_hasta`.
    periodo_hasta = models.DateField()
    monto_usd = models.DecimalField(max_digits=8, decimal_places=2)
    # La parte de `monto_usd` que es la tienda (0 sin tienda). Se congela con el
    # link y `_acreditar_aprobado` la copia al pago: nunca se recalcula.
    # `db_default`: el INSERT del código viejo (durante el `migrate` o tras un
    # rollback) no conoce la columna.
    monto_tienda_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=0, db_default=0
    )
    monto_ars = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=2)
    alumnos_activos = models.PositiveIntegerField()
    referencia_externa = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    preference_id = models.CharField(max_length=64, blank=True)
    init_point = models.URLField(max_length=500, blank=True)
    expira = models.DateTimeField()
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.CREADO
    )
    payment_id = models.CharField(max_length=32, blank=True)
    estado_mp = models.CharField(max_length=30, blank=True)
    respuesta_mp = models.JSONField(default=dict, blank=True)
    acreditado_en = models.DateTimeField(null=True, blank=True)
    pago = models.OneToOneField(
        "plataforma.PagoPlataforma",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="intento_de_cobro",
    )
    # Quién apretó «Pagar». Texto y no FK a `User`: ver el docstring del módulo.
    email_de_quien_pago = models.CharField(max_length=254, blank=True)

    class Meta:
        ordering = ["-creado", "-id"]
        verbose_name = "intento de cobro"
        verbose_name_plural = "intentos de cobro"
        indexes = [models.Index(fields=["gimnasio", "estado"])]
        # Las condiciones van con string literals: dentro de la `Meta` anidada
        # no se ve el scope de la clase, así que `Estado.CREADO` no resuelve.
        constraints = [
            # Un solo link vivo por período: es lo que hace que el segundo
            # click reuse el link en vez de crear un segundo cobro del mismo
            # mes (mismo patrón que el UniqueConstraint parcial de `ingreso`).
            models.UniqueConstraint(
                fields=["gimnasio", "periodo_desde"],
                condition=Q(estado="creado"),
                name="suscripcion_un_intento_abierto_por_periodo",
            ),
            models.UniqueConstraint(
                fields=["payment_id"],
                condition=~Q(payment_id=""),
                name="suscripcion_un_payment_id",
            ),
            models.CheckConstraint(
                condition=Q(periodo_hasta__gte=F("periodo_desde")),
                name="suscripcion_periodo_no_invertido",
            ),
        ]

    def __str__(self):
        return (
            f"{self.gimnasio} · {self.get_plan_display()} · "
            f"{self.periodo_desde:%d/%m/%Y}–{self.periodo_hasta:%d/%m/%Y} · "
            f"{self.get_estado_display()}"
        )


class AvisoDeCobro(models.Model):
    """Un aviso de vencimiento ya mandado al staff de un gimnasio.

    Es el dedup del cron `avisar_cobros`: una fila por (gimnasio, período,
    tipo), así que el mismo aviso no sale dos veces aunque el comando corra
    varias veces el mismo día (o GitHub reintente).

    **No se reusa `notificaciones.RecordatorioEnviado`**: es un
    `TenantOwnedModel`, o sea un dato DEL gimnasio (se exporta, se borra al
    vaciar la demo). Esto es un dato de la plataforma SOBRE el gimnasio, y
    tampoco lleva FK a `User` (ver el docstring del módulo).
    """

    class Tipo(models.TextChoices):
        POR_VENCER = "por_vencer", "Por vencer"
        VENCE_HOY = "vence_hoy", "Vence hoy"
        VENCIDO = "vencido", "Vencido"

    gimnasio = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.PROTECT,
        related_name="avisos_de_cobro",
    )
    periodo_desde = models.DateField()
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    enviado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "aviso de cobro"
        verbose_name_plural = "avisos de cobro"
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "periodo_desde", "tipo"],
                name="suscripcion_un_aviso_por_periodo_y_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.gimnasio} · {self.periodo_desde:%d/%m/%Y} · {self.get_tipo_display()}"
