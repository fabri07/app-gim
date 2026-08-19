"""Signals que enganchan Novedad/RutinaAsignada/Reserva con Web Push.

Viven acá (no en novedades/rutinas/turnos) para que esas apps no importen
infraestructura de push -- mismo criterio de desacople que
`calendario/signals.py` para Google Calendar. Django permite múltiples
receivers por señal+sender, así que estos coexisten sin pisar el signal ya
existente de `calendario` sobre `Reserva`.

Todos chequean `raw` (evita romper con `loaddata`/fixtures -- gotcha que
`calendario/signals.py` NO chequea, por eso el proyecto usa `pg_dump` y
nunca `dumpdata`) y difieren el envío real con `transaction.on_commit`.
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from notificaciones import services
from novedades.models import Novedad
from rutinas.models import RutinaAsignada
from turnos.models import Reserva


@receiver(post_save, sender=Novedad)
def notificar_novedad_publicada(sender, instance, created, raw=False, **kwargs):
    if raw or not created or not instance.activa:
        # `activa=False` es válido desde la creación (NovedadForm la expone
        # editable) -- una novedad creada ya oculta no debe notificar, mismo
        # criterio que `NovedadQuerySet.visibles()` y el filtro `activa=True`
        # que ya usa `enviar_recordatorios` para el barrido de programadas.
        return
    if instance.fecha_publicacion > timezone.localdate():
        # Publicación programada a futuro: la recoge `enviar_recordatorios`
        # el día que corresponda, no acá.
        return
    transaction.on_commit(lambda: services.notificar_novedad(instance))


@receiver(post_save, sender=RutinaAsignada)
def notificar_rutina_asignada(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    transaction.on_commit(lambda: services.notificar_rutina_asignada(instance))


@receiver(post_save, sender=Reserva)
def notificar_nueva_reserva(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    transaction.on_commit(lambda: services.notificar_nueva_reserva(instance))
