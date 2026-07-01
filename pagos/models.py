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
from django.core.validators import MaxValueValidator, MinValueValidator

from core.models import TenantOwnedModel


class PagoMensual(TenantOwnedModel):
    """La cuota de un alumno para un mes/año calendario puntual.

    `unique_together` en Meta garantiza una sola fila por
    (gimnasio, alumno, mes, año): coincide con "se autogeneran... para cada
    alumno activo al inicio del mes" (un pago por mes, no varios).

    `comprobante`: es `FileField` (no `ImageField`) porque el alumno puede
    subir una foto o un PDF del comprobante. En dev queda en el filesystem
    local (`MEDIA_ROOT`); Fase 5 cambia el storage a Cloudflare R2 vía
    `django-storages` sin tocar este campo — el filesystem de Render es
    efímero y nunca debe recibir uploads reales.
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
    comprobante = models.FileField(upload_to="comprobantes/", blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "pago mensual"
        verbose_name_plural = "pagos mensuales"
        unique_together = ("gimnasio", "alumno", "mes", "anio")
        ordering = ["-anio", "-mes"]

    def __str__(self):
        return f"{self.alumno} - {self.mes:02d}/{self.anio:04d}"


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


def marcar_vencidos(mes, anio):
    """Pasa a VENCIDO todo `PagoMensual` PENDIENTE cuyo mes/año sea
    estrictamente anterior al mes/año dado (es decir, un pago pendiente
    de un mes que ya cerró).

    Devuelve la cantidad de filas actualizadas (int).
    """
    pendientes_pasados = PagoMensual.objects.filter(
        estado=PagoMensual.Estado.PENDIENTE
    ).filter(
        models.Q(anio__lt=anio) | models.Q(anio=anio, mes__lt=mes)
    )
    return pendientes_pasados.update(estado=PagoMensual.Estado.VENCIDO)
