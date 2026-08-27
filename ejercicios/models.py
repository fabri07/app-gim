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
from importaciones.parsing import normalizar_texto


class CategoriaEjercicio(TenantOwnedModel):
    """Cómo cada gimnasio agrupa sus ejercicios.

    Reemplaza al `TextChoices` global que `Ejercicio.grupo_muscular` usaba
    hasta 2026-08-26. El catálogo cerrado era anatómico (Pecho/Espalda/...)
    y no servía para un gimnasio funcional que clasifica por patrón de
    movimiento (EMPUJE/TRACCIÓN/RODILLA/CADERA) ni para uno de calistenia
    que además tiene bloques y skills (MOVILIDAD, MUSCLE UP, HANDSTAND).
    Ninguna lista fija sirve para los dos, así que el catálogo es por
    gimnasio -- ver la entrada de `ISSUES.md` del 2026-08-26.

    `nombre_normalizado` no es un dato que el staff cargue: lo calcula
    `save()` y existe solo para sostener la `UniqueConstraint`. Sin él,
    "CORE", "Core" y "core" serían tres filas distintas, que es exactamente
    lo que pasaría al importar un Excel donde la misma categoría viene
    escrita de varias formas.
    """

    nombre = models.CharField(max_length=60)
    nombre_normalizado = models.CharField(max_length=60, editable=False)
    orden = models.PositiveIntegerField(
        default=0, help_text="Menor primero. A igual orden, alfabético."
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "categoría de ejercicio"
        verbose_name_plural = "categorías de ejercicio"
        ordering = ["orden", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["gimnasio", "nombre_normalizado"],
                name="categoria_unica_por_gimnasio",
            )
        ]

    def save(self, *args, **kwargs):
        # Se recalcula en CADA save, no solo al crear: renombrar "EMPUJE" a
        # "Empujón" tiene que mover también la clave de deduplicación, o la
        # constraint pasaría a proteger un valor que ya no existe.
        self.nombre = self.nombre.strip()
        self.nombre_normalizado = normalizar_texto(self.nombre)
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "nombre",
                "nombre_normalizado",
            }
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre


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
    categoria = models.ForeignKey(
        CategoriaEjercicio,
        on_delete=models.PROTECT,
        related_name="ejercicios",
        null=True,
        blank=True,
        verbose_name="categoría",
    )
    # Reemplazado por `categoria` el 2026-08-26. La columna sobrevive un
    # release a propósito (expand/contract): si el deploy sale mal, revertir
    # el código alcanza porque el dato viejo sigue acá. Se borra en un commit
    # posterior, una vez confirmado que producción está sana.
    grupo_muscular = models.CharField(
        max_length=20, choices=GrupoMuscular.choices, blank=True, null=True
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
