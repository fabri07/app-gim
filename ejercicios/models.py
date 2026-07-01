"""
Biblioteca de ejercicios de cada gimnasio.

Fase 1: solo el modelo de datos. Fase 2 agrega las vistas de gestión (crear,
editar, cargar link de YouTube, filtrar por grupo muscular) sobre esta misma
tabla.

Por ahora la biblioteca es POR GIMNASIO (TenantOwnedModel), no global: cada
staff carga sus propios ejercicios. Una biblioteca compartida entre gimnasios
es una feature posterior (ver ROADMAP), no algo a resolver ahora (YAGNI).
"""

from django.db import models

from core.models import TenantOwnedModel


class Ejercicio(TenantOwnedModel):
    """Un ejercicio de la biblioteca de un gimnasio.

    `grupo_muscular` usa `TextChoices` con un set fijo (en vez de texto
    libre): Fase 2 necesita filtrar por grupo muscular, y un catálogo cerrado
    evita variantes tipo "pecho"/"Pecho"/"PECHO" que romperían ese filtro.

    `url_video` es un link de YouTube (Fase 2 lo carga el staff); queda
    `blank=True` porque no todo ejercicio tiene video todavía.
    """

    class GrupoMuscular(models.TextChoices):
        PECHO = "pecho", "Pecho"
        ESPALDA = "espalda", "Espalda"
        PIERNAS = "piernas", "Piernas"
        HOMBROS = "hombros", "Hombros"
        BRAZOS = "brazos", "Brazos"
        CORE = "core", "Core"
        CARDIO = "cardio", "Cardio"
        CUERPO_COMPLETO = "cuerpo_completo", "Cuerpo completo"

    nombre = models.CharField(max_length=120)
    grupo_muscular = models.CharField(
        max_length=20, choices=GrupoMuscular.choices
    )
    descripcion = models.TextField(blank=True)
    url_video = models.URLField(blank=True, help_text="Link de YouTube")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "ejercicio"
        verbose_name_plural = "ejercicios"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
