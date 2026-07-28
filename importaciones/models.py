"""Staging de importaciones desde Excel (Proyecto 2).

`Importacion` persiste el archivo subido + el resultado del parseo entre el
POST de subida y el POST de confirmación -- ver spec
`2026-07-27-importador-planes-entrenamiento-design.md` §2 para por qué NO es
sesión de Django ni hidden-fields en el form de preview. El código nunca
vuelve a abrir `archivo` después del preview; todo lo que hace falta para
confirmar ya está en `resultado`.
"""

from django.conf import settings
from django.db import models

from core.models import TenantOwnedModel


class Importacion(TenantOwnedModel):
    class Tipo(models.TextChoices):
        PLANTILLAS = 'plantillas', 'Plantillas de rutina'
        BIBLIOTECA = 'biblioteca', 'Biblioteca de ejercicios'

    class Estado(models.TextChoices):
        EN_REVISION = 'en_revision', 'Pendiente de revisión'
        CONFIRMADA = 'confirmada', 'Confirmada'
        DESCARTADA = 'descartada', 'Descartada'

    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    archivo = models.FileField(upload_to='importaciones/')
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.EN_REVISION
    )
    resultado = models.JSONField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        # SET_NULL: borrar el usuario que subió el archivo no debe borrar
        # el historial de importaciones del gimnasio.
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'importación'
        verbose_name_plural = 'importaciones'
        ordering = ['-creado']

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.gimnasio}"
