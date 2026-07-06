"""
Abstracciones compartidas por todo el dominio.

Esta app NO define tablas propias: solo provee modelos abstractos y un
manager reutilizable. Centralizar acá las reglas transversales (timestamps,
pertenencia a un Gimnasio) evita repetirlas en cada modelo concreto (DRY) y
deja un único lugar donde evolucionar la arquitectura multi-tenant.

Patrón adaptado de ~/gestor-pedidos/core/models.py (Negocio -> Gimnasio).
Ver REUSO.md para el detalle de qué se reutilizó y qué se adaptó.
"""

from django.core.exceptions import ValidationError
from django.db import models


def validar_gimnasio_de(gimnasio, **relaciones):
    """Valida, para usar en `clean()`, que cada instancia en `relaciones`
    (nombre_de_campo=instancia) pertenezca al `gimnasio` dado.

    `TenantScopedModelForm` ya cierra este hueco para los datos que pasan por
    un form de staff, pero no para accesos directos (admin, shell, imports) a
    un modelo con más de una FK a entidades tenant-owned -- p.ej.
    `Reserva.alumno`, `PagoMensual.alumno`, `NovedadLeida.alumno`/`novedad`.
    Instancias `None` se ignoran (los validadores de "campo requerido" ya
    cubren ese caso).
    """
    errores = {
        campo: ValidationError("Pertenece a otro gimnasio.", code="gimnasio_no_coincide")
        for campo, instancia in relaciones.items()
        if instancia is not None and instancia.gimnasio_id != gimnasio.id
    }
    if errores:
        raise ValidationError(errores)


class TimeStampedModel(models.Model):
    """Audita creación y última modificación de cualquier fila.

    Se separa de la lógica de tenancy (SOLID: responsabilidad única) para que
    modelos que NO pertenecen a un Gimnasio igual puedan auditarse.
    """

    creado = models.DateTimeField(auto_now_add=True, editable=False)
    modificado = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    """QuerySet con filtrado de tenant EXPLÍCITO.

    Decisión: NO usamos thread-locals + middleware para inyectar el tenant
    de forma automática. Eso sería "implícito" y acoplaría el modelo al ciclo
    de request. Mientras el producto sea simple (YAGNI), exponer un método
    explícito `for_gimnasio()` es más claro y testeable.
    """

    def for_gimnasio(self, gimnasio):
        return self.filter(gimnasio=gimnasio)


class TenantOwnedModel(TimeStampedModel):
    """Base de toda entidad que pertenece a un Gimnasio (tenant).

    Estrategia de multi-tenancy: BASE DE DATOS COMPARTIDA + ESQUEMA COMPARTIDO,
    con aislamiento a nivel de fila vía FK `gimnasio`. Mismo criterio que
    gestor-pedidos (KISS/YAGNI para esta etapa del producto).

    `on_delete=PROTECT`: borrar un Gimnasio con datos operativos debe ser una
    decisión consciente, no un cascade silencioso que destruya el historial.
    """

    gimnasio = models.ForeignKey(
        "tenants.Gimnasio",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
        # related_name dinámico => cada modelo concreto expone, p.ej.,
        # gimnasio.alumnos / gimnasio.ejercicios sin colisiones (DRY).
        verbose_name="gimnasio",
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True
