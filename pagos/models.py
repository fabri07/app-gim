"""
Modelo de dominio: pagos mensuales de cada alumno.

`PagoMensual` es un `TenantOwnedModel`: hereda `gimnasio` (aislamiento por
fila) y los timestamps de auditoría. Se FK-ea a `alumnos.Alumno` por string
("alumnos.Alumno") en vez de importar la clase, para no acoplar el orden de
carga de apps entre `pagos` y `alumnos` (Django resuelve el string una vez
que ambas apps están instaladas).

Principio no negociable del ROADMAP (Fase 1 / Fase 2 §6): pagos simples, sin
integración financiera real. Los pendientes se **autogeneran por cron** al
inicio de cada mes (una fila por alumno activo por mes calendario) y el mismo
cron pasa `pendiente -> vencido` cuando el mes ya pasó. El dueño únicamente
**confirma** un pago existente (marca pagado, sube comprobante); nunca crea
un `PagoMensual` a mano. Esa autogeneración vive acá (funciones de módulo,
no una capa de "servicios" separada: el proyecto es chico y no lo justifica).
"""

from django.db import models
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)

from core.models import TenantOwnedModel, validar_gimnasio_de

EXTENSIONES_COMPROBANTE_PERMITIDAS = ["jpg", "jpeg", "png"]


class MedioCobro(TenantOwnedModel):
    """Alias/CBU al que los alumnos transfieren la cuota. Solo datos exhibidos en el
    portal -- sin integración de pagos (principio no negociable del proyecto: "sin
    Mercado Pago ni integraciones financieras en el MVP")."""

    alias = models.CharField(max_length=60)
    titular = models.CharField(max_length=80, blank=True)
    entidad = models.CharField(max_length=60, blank=True)  # banco o billetera virtual
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "medio de cobro"
        verbose_name_plural = "medios de cobro"
        ordering = ["alias"]

    def __str__(self):
        return self.alias


class PagoMensual(TenantOwnedModel):
    """La cuota de un alumno para un mes/año calendario puntual.

    `unique_together` en Meta garantiza una sola fila por
    (gimnasio, alumno, mes, año): coincide con "se autogeneran... para cada
    alumno activo al inicio del mes" (un pago por mes, no varios).

    `comprobante`: es `FileField` (no `ImageField`, que exige poder abrir el
    archivo con Pillow al validar -- acá alcanza con el validador de
    extensión) restringido a `EXTENSIONES_COMPROBANTE_PERMITIDAS`
    (jpg/jpeg/png): son fotos de un comprobante de transferencia sacadas con
    el celular, nunca un PDF -- pedido explícito del dueño del producto para
    que el staff no reciba archivos que no pueda previsualizar de un
    vistazo. En dev queda en el filesystem local (`MEDIA_ROOT`); en
    producción vive en Cloudflare R2 vía `django-storages` sin tocar este
    campo.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PAGADO = "pagado", "Pagado"
        VENCIDO = "vencido", "Vencido"

    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    mes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    anio = models.PositiveSmallIntegerField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.PENDIENTE
    )
    fecha_pago = models.DateField(null=True, blank=True)
    medio_pago_texto = models.CharField(max_length=60, blank=True)
    comprobante = models.FileField(
        upload_to="comprobantes/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=EXTENSIONES_COMPROBANTE_PERMITIDAS)],
    )
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "pago mensual"
        verbose_name_plural = "pagos mensuales"
        unique_together = ("gimnasio", "alumno", "mes", "anio")
        ordering = ["-anio", "-mes"]

    def __str__(self):
        return f"{self.alumno} - {self.mes:02d}/{self.anio:04d}"

    def clean(self):
        super().clean()
        if self.gimnasio_id and self.alumno_id:
            validar_gimnasio_de(self.gimnasio, alumno=self.alumno)


def generar_pagos_pendientes(mes, anio):
    """Crea un `PagoMensual` PENDIENTE para cada alumno activo de cada
    gimnasio activo, para el mes/año dados, si todavía no existe.

    Es idempotente: usa `get_or_create` sobre las mismas columnas del
    `unique_together`, así que llamarla más de una vez para el mismo
    mes/año no duplica filas (el cron puede reintentar sin miedo).

    El `monto` se deja en 0: el dueño lo completa al confirmar el pago
    (Fase 2 §6) — el cron no conoce precios ni planes, solo genera la
    fila pendiente.

    Devuelve la cantidad de pagos creados (int), para que el management
    command pueda informarlo.
    """
    from alumnos.models import Alumno
    from tenants.models import Gimnasio

    creados = 0
    alumnos_activos = Alumno.objects.filter(
        estado=Alumno.Estado.ACTIVO,
        gimnasio__activo=True,
    )
    for alumno in alumnos_activos:
        _, fue_creado = PagoMensual.objects.get_or_create(
            gimnasio=alumno.gimnasio,
            alumno=alumno,
            mes=mes,
            anio=anio,
            defaults={"monto": 0, "estado": PagoMensual.Estado.PENDIENTE},
        )
        if fue_creado:
            creados += 1
    return creados


def marcar_vencidos(mes, anio, dia):
    """Pasa a VENCIDO todo `PagoMensual` PENDIENTE cuyo mes/año sea
    estrictamente anterior al mes/año dado (un pago de un mes que ya
    cerró), o que sea del mismo mes/año pero ya se pasó el
    `Gimnasio.dia_vencimiento_pago` de ESE gimnasio (join vía FK -- cada
    gimnasio define su propio día límite, no hay uno global). Antes de
    este chequeo, `dia_vencimiento_pago` era puramente cosmético: se
    mostraba como fecha límite en el portal del alumno pero un pago del
    mes en curso solo pasaba a VENCIDO cuando cambiaba el mes calendario,
    sin importar el día -- un alumno podía estar atrasado según la fecha
    que el gimnasio le mostraba y seguir viéndose "Pendiente" en el panel
    del staff hasta el mes siguiente.

    `dia` es el día del mes de la fecha en que corre esto (no un campo de
    `PagoMensual`, que no tiene día) -- lo pasa el caller
    (`manage.py generar_pagos`) para no atar esta función a `timezone.now()`
    y poder testearla con fechas fijas.

    Devuelve la cantidad de filas actualizadas (int).
    """
    pendientes_vencidos = PagoMensual.objects.filter(
        estado=PagoMensual.Estado.PENDIENTE
    ).filter(
        models.Q(anio__lt=anio)
        | models.Q(anio=anio, mes__lt=mes)
        | models.Q(anio=anio, mes=mes, gimnasio__dia_vencimiento_pago__lt=dia)
    )
    return pendientes_vencidos.update(estado=PagoMensual.Estado.VENCIDO)
