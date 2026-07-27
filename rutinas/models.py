"""
Rutinas de entrenamiento: plantillas reutilizables y su asignación a un
alumno concreto.

Dos pares de modelos:

- `RutinaPlantilla` / `RutinaPlantillaItem`: la plantilla que el staff
  diseña y reutiliza (p. ej. "Full body principiante"). Vive en el catálogo
  del gimnasio y se puede seguir editando con el tiempo.
- `RutinaAsignada` / `RutinaAsignadaItem`: el SNAPSHOT que queda cuando esa
  plantilla se asigna a un alumno en una fecha determinada. Es una copia
  congelada: editar la plantilla después no debe alterar lo que el alumno
  ya tiene asignado (mismo principio que `ItemPedido.precio_unitario` en
  ~/gestor-pedidos, que congela `producto.precio` al crear el pedido — ver
  ROADMAP Fase 1, sección "RutinaAsignada (snapshot — modelo clave)").

Los modelos "Item" (`RutinaPlantillaItem`, `RutinaAsignadaItem`) NO son
`TenantOwnedModel`: siempre se acceden a través de su padre
(`RutinaPlantilla` o `RutinaAsignada`), que ya está scopeado por gimnasio.
Repetir el FK `gimnasio` en el item sería redundante (mismo criterio que
`ItemPedido` en gestor-pedidos, que no repite `negocio` y solo lo tiene su
`Pedido` padre).
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from core.models import TenantOwnedModel, TimeStampedModel

SEMANAS_POR_CICLO = 4


class RutinaPlantilla(TenantOwnedModel):
    """Plantilla de rutina reutilizable, diseñada por el staff.

    `objetivo` es texto libre (no un `TextChoices`): el ROADMAP lo describe
    con ejemplos ("Hipertrofia", "Fuerza"), no como un catálogo cerrado, y
    a diferencia de `Ejercicio.grupo_muscular` no hay ningún filtro de Fase 2
    que dependa de un set fijo de valores.
    """

    class Nivel(models.TextChoices):
        PRINCIPIANTE = "principiante", "Principiante"
        INTERMEDIO = "intermedio", "Intermedio"
        AVANZADO = "avanzado", "Avanzado"

    nombre = models.CharField(max_length=120)
    objetivo = models.CharField(max_length=120)
    nivel = models.CharField(max_length=15, choices=Nivel.choices)
    dias_por_semana = models.PositiveSmallIntegerField()
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plantilla de rutina"
        verbose_name_plural = "plantillas de rutina"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def duplicar(self):
        """Crea una copia independiente de esta plantilla y sus items.

        ROADMAP Fase 2 §4: "duplicar rutina existente (antes que crear desde
        cero)". Es lógica de modelo (no de vista) porque el copiado de los
        items debe ser atómico y reutilizable desde cualquier flujo futuro.
        La copia es completamente independiente: modificar los items de una
        no afecta a la otra (a diferencia del snapshot de `RutinaAsignada`,
        aquí SÍ seguimos editando ambas plantillas con FKs vivos a
        `Ejercicio`).
        """
        with transaction.atomic():
            copia = RutinaPlantilla.objects.create(
                gimnasio=self.gimnasio,
                nombre=f"{self.nombre} (copia)",
                objetivo=self.objetivo,
                nivel=self.nivel,
                dias_por_semana=self.dias_por_semana,
                activa=self.activa,
            )
            RutinaPlantillaItem.objects.bulk_create(
                [
                    RutinaPlantillaItem(
                        rutina=copia,
                        ejercicio=item.ejercicio,
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        descanso=item.descanso,
                        notas=item.notas,
                    )
                    for item in self.items.all()
                ]
            )
        return copia


class RutinaPlantillaItem(TimeStampedModel):
    """Un ejercicio dentro de un día de una `RutinaPlantilla`."""

    rutina = models.ForeignKey(
        RutinaPlantilla,
        on_delete=models.CASCADE,
        related_name="items",
        # CASCADE: borrar la plantilla borra sus items, no tiene sentido que
        # sobrevivan huérfanos.
    )
    ejercicio = models.ForeignKey(
        "ejercicios.Ejercicio",
        on_delete=models.PROTECT,
        related_name="items_plantilla",
        # PROTECT: si un ejercicio sigue referenciado por una plantilla viva,
        # obligamos al staff a reasignarlo/quitarlo antes de borrarlo, en vez
        # de romper la plantilla en silencio.
    )
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
    orden = models.PositiveSmallIntegerField(help_text="Orden dentro del día.")
    series = models.PositiveSmallIntegerField()
    repeticiones = models.CharField(
        max_length=20,
        help_text='Notación libre: "10", "8-12", "AMRAP", etc.',
    )
    descanso = models.CharField(
        max_length=30, blank=True, help_text='Ej: "60s", "2 min".'
    )
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = "item de plantilla"
        verbose_name_plural = "items de plantilla"
        ordering = ["semana", "dia", "orden"]

    def __str__(self):
        return f"Día {self.dia} · {self.ejercicio.nombre}"


class RutinaAsignada(TenantOwnedModel):
    """El snapshot de una `RutinaPlantilla` asignado a un alumno concreto.

    A diferencia de los modelos "Item", ESTE sí es `TenantOwnedModel`: se
    consulta directamente por alumno a lo largo del tiempo (historial de
    rutinas asignadas), no solo a través de un padre.
    """

    alumno = models.ForeignKey(
        "alumnos.Alumno",
        on_delete=models.PROTECT,
        related_name="rutinas_asignadas",
        # PROTECT: el historial de rutinas de un alumno no debe desaparecer
        # si se intenta borrar al alumno; primero hay que decidir qué hacer
        # con ese historial.
    )
    nombre_snapshot = models.CharField(max_length=120)
    objetivo_snapshot = models.CharField(max_length=120)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "rutina asignada"
        verbose_name_plural = "rutinas asignadas"
        ordering = ["-fecha_inicio"]

    def __str__(self):
        return f"{self.alumno} · {self.nombre_snapshot} desde {self.fecha_inicio}"

    @classmethod
    def crear_desde_plantilla(cls, *, gimnasio, alumno, plantilla, fecha_inicio):
        """Copia la plantilla (y sus items) al momento de la asignación.

        Editar la plantilla después NO debe afectar esta asignación — ver
        ROADMAP Fase 1, RutinaAsignada.

        `gimnasio` se recibe explícito (no se infiere de `plantilla.gimnasio`
        ni de `alumno.gimnasio`) para que quien llama sea explícito sobre en
        qué tenant está operando: validamos acá que los tres coincidan, en
        vez de confiar en que el caller ya filtró correctamente. Asignar una
        plantilla o un alumno de OTRO gimnasio sería un bug de aislamiento
        de tenant, así que lo tratamos como error de datos (`ValidationError`),
        no como un bug de programación (`AssertionError`).
        """
        if plantilla.gimnasio_id != gimnasio.id or alumno.gimnasio_id != gimnasio.id:
            raise ValidationError(
                "La plantilla y el alumno deben pertenecer al gimnasio indicado."
            )

        with transaction.atomic():
            asignada = cls.objects.create(
                gimnasio=gimnasio,
                alumno=alumno,
                nombre_snapshot=plantilla.nombre,
                objetivo_snapshot=plantilla.objetivo,
                fecha_inicio=fecha_inicio,
            )
            RutinaAsignadaItem.objects.bulk_create(
                [
                    RutinaAsignadaItem(
                        rutina_asignada=asignada,
                        ejercicio_nombre_snapshot=item.ejercicio.nombre,
                        ejercicio_video_snapshot=item.ejercicio.url_video,
                        semana=item.semana,
                        dia=item.dia,
                        orden=item.orden,
                        series=item.series,
                        repeticiones=item.repeticiones,
                        descanso=item.descanso,
                        notas=item.notas,
                    )
                    for item in plantilla.items.all()
                ]
            )
        return asignada


class RutinaAsignadaItem(TimeStampedModel):
    """Un ejercicio dentro de un día de una `RutinaAsignada`, ya congelado.

    Sin FK viva a `Ejercicio`: ese es justamente el punto del snapshot —
    editar o borrar el `Ejercicio` original nunca debe alterar la rutina
    histórica de un alumno.
    """

    rutina_asignada = models.ForeignKey(
        RutinaAsignada,
        on_delete=models.CASCADE,
        related_name="items",
    )
    ejercicio_nombre_snapshot = models.CharField(max_length=120)
    ejercicio_video_snapshot = models.URLField(blank=True)
    semana = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(SEMANAS_POR_CICLO)],
        help_text="Semana del ciclo (1 a 4).",
    )
    dia = models.PositiveSmallIntegerField(
        help_text="Día N de la rutina (1..dias_por_semana), no día de la semana."
    )
    orden = models.PositiveSmallIntegerField(help_text="Orden dentro del día.")
    series = models.PositiveSmallIntegerField()
    repeticiones = models.CharField(max_length=20)
    descanso = models.CharField(max_length=30, blank=True)
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = "item de rutina asignada"
        verbose_name_plural = "items de rutina asignada"
        ordering = ["semana", "dia", "orden"]

    def __str__(self):
        return f"Día {self.dia} · {self.ejercicio_nombre_snapshot}"
