"""
Comunicados que el staff publica para sus alumnos (avisos de gimnasio
cerrado, cambios de horario, promociones, etc).

Ver ROADMAP Fase 1 (modelo `Novedad`) y Fase 2 §7 (publicar/editar/ocultar/
definir visibilidad), que se implementa recién en las vistas — acá solo se
modela el dato.
"""

from django.db import models
from django.utils.timezone import now

from core.models import TenantOwnedModel, TenantQuerySet, TimeStampedModel, validar_gimnasio_de


class NovedadQuerySet(TenantQuerySet):
    """Extiende `TenantQuerySet` (core) con reglas propias de `Novedad`.

    No se agrega `visibles()` a `TenantQuerySet` porque esa clase es
    compartida por TODOS los modelos tenant-owned (alumnos, rutinas, pagos,
    etc): mezclarle una regla de negocio específica de novedades violaría
    responsabilidad única y la ensuciaría para el resto de las apps. Al
    subclasificar acá, `Novedad.objects.for_gimnasio(g)` sigue funcionando
    por herencia y sumamos `visibles()` sin tocar `core/models.py`.
    """

    def visibles(self):
        """Novedades que corresponde mostrarle HOY a un alumno.

        "Visible" = activa, ya publicada (fecha_publicacion <= hoy) y sin
        vencer (visible_hasta nulo o todavía no llegó). El filtrado real por
        pantalla del alumno lo conecta Fase 2/3; acá solo queda disponible el
        método para que ese código no tenga que reinventar la condición.
        """
        hoy = now().date()
        return self.filter(activa=True, fecha_publicacion__lte=hoy).filter(
            models.Q(visible_hasta__isnull=True) | models.Q(visible_hasta__gte=hoy)
        )


def _hoy():
    # Función a nivel de módulo (no lambda): las migraciones de Django
    # necesitan poder serializar el default por su import path.
    return now().date()


class Novedad(TenantOwnedModel):
    """Aviso publicado por el staff de un gimnasio.

    `fecha_publicacion` es editable (no `auto_now_add`) porque el staff debe
    poder programar una novedad a futuro o dejar constancia de que se
    publicó en una fecha pasada (backdating). El default usa
    `timezone.now().date()` en vez del `date.today()` de stdlib para respetar
    la zona horaria configurada en el proyecto (USE_TZ), en linea con el
    resto del código (que evita "now"/"today" naive).

    `visible_hasta` nulo significa "sin fecha de vencimiento". Cuando tiene
    valor y ya pasó, la novedad deja de listarse para alumnos (Fase 2), pero
    sigue existiendo para el staff (no se borra el historial).

    `activa` permite "ocultar" (Fase 2) sin perder el registro, análogo a
    `Gimnasio.activo`.
    """

    titulo = models.CharField(max_length=140)
    mensaje = models.TextField()
    fecha_publicacion = models.DateField(default=_hoy)
    visible_hasta = models.DateField(null=True, blank=True)
    activa = models.BooleanField(default=True)

    objects = NovedadQuerySet.as_manager()

    class Meta:
        verbose_name = "novedad"
        verbose_name_plural = "novedades"
        ordering = ["-fecha_publicacion"]

    def __str__(self):
        return self.titulo


class NovedadLeida(TimeStampedModel):
    """Registro de que un alumno leyó una novedad. NO es TenantOwnedModel -- se
    scopea a través de `novedad.gimnasio`, mismo patrón que RutinaAsignadaItem."""

    novedad = models.ForeignKey(Novedad, on_delete=models.CASCADE, related_name="lecturas")
    alumno = models.ForeignKey(
        "alumnos.Alumno", on_delete=models.CASCADE, related_name="novedades_leidas"
    )

    class Meta:
        verbose_name = "novedad leída"
        verbose_name_plural = "novedades leídas"
        unique_together = ("novedad", "alumno")

    def __str__(self):
        return f"{self.alumno} leyó '{self.novedad.titulo}'"

    def clean(self):
        super().clean()
        if self.novedad_id and self.alumno_id:
            validar_gimnasio_de(self.novedad.gimnasio, alumno=self.alumno)
