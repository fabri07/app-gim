"""
Núcleo de la arquitectura multi-tenant.

`Gimnasio` ES el tenant. `Perfil` conecta el usuario de autenticación de
Django con su Gimnasio y su rol, sin contaminar el modelo de auth (composición
sobre herencia: no extendemos ni reemplazamos User, lo enlazamos).

Adaptado de ~/gestor-pedidos/tenants/models.py (Negocio -> Gimnasio). Los
campos de Gimnasio se mantienen mínimos a propósito: logo, colores, texto de
bienvenida, contacto y links de redes son de Fase 1 del ROADMAP, no de esta
extracción del esqueleto (Fase 0).
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Gimnasio(TimeStampedModel):
    """Un gimnasio/entrenador que usa el sistema. Unidad de aislamiento de
    datos (tenant).

    `creado` (heredado de TimeStampedModel) hace de fecha de alta; no se
    duplica un campo `fecha_alta` aparte.
    """

    nombre = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "gimnasio"
        verbose_name_plural = "gimnasios"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Perfil(TimeStampedModel):
    """Vínculo 1:1 entre un usuario de Django, su Gimnasio y su rol.

    Diseño:
      - OneToOne con AUTH_USER_MODEL (settings, no `auth.User` hardcodeado):
        permite cambiar a un User custom en el futuro sin reescribir FKs.
      - Un Perfil pertenece a un único Gimnasio (FK). Un Gimnasio puede tener
        varios Perfiles (staff y alumnos) sin cambios de modelo.
      - on_delete CASCADE desde el User (si se borra el usuario, su perfil
        deja de tener sentido) pero PROTECT hacia el Gimnasio (no se borra un
        Gimnasio con usuarios vivos por accidente).
      - Roles del MVP (ver ROADMAP Fase 1): `staff` (dueño y/o entrenador,
        mismos permisos) y `alumno`. Separar dueño de entrenador queda para
        después.
    """

    class Rol(models.TextChoices):
        STAFF = "staff", "Staff"
        ALUMNO = "alumno", "Alumno"

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    gimnasio = models.ForeignKey(
        Gimnasio,
        on_delete=models.PROTECT,
        related_name="perfiles",
    )
    rol = models.CharField(max_length=10, choices=Rol.choices, default=Rol.STAFF)

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfiles"

    def __str__(self):
        return f"{self.usuario} @ {self.gimnasio} ({self.rol})"
