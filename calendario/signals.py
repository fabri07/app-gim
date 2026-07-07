"""Signals que enganchan las reservas con Google Calendar (Parte C2).

Van acá y no en `turnos/services.py` para que `turnos` quede 100% desacoplado de
la integración (si se saca la app `calendario`, `turnos` no cambia). Todos los
handlers son no-op si la integración está apagada o el alumno no conectó su
cuenta, y el trabajo real se difiere con `transaction.on_commit(...)` para no
llamar a la API dentro de una transacción abierta (y no crear eventos de
reservas que después hacen rollback).

Límite conocido: los signals corren por instancia. Un `QuerySet.update()` o
`bulk_create` NO los dispara -- todo cambio de `Reserva` que deba reflejarse en
Calendar tiene que pasar por `save()`/`delete()` de instancia (como hacen hoy
`crear_reserva`, `cancelar_reserva` y la reconciliación).
"""

from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from turnos.models import ConfiguracionTurnos, Reserva
from calendario import services


@receiver(post_save, sender=Reserva)
def sync_reserva_guardada(sender, instance, created, update_fields=None, **kwargs):
    if not services.integracion_activa():
        return
    reserva = instance
    if created:
        transaction.on_commit(lambda: services.crear_evento(reserva))
    elif update_fields is None or "hora_inicio" in update_fields:
        # `update_fields is None` = `reserva.save()` pelado; la reconciliación
        # pasa `update_fields=["hora_inicio"]` al migrar.
        transaction.on_commit(lambda: services.actualizar_evento(reserva))


@receiver(pre_delete, sender=Reserva)
def sync_reserva_borrada(sender, instance, **kwargs):
    if not services.integracion_activa():
        return
    credencial = services._credencial_conectada(instance.alumno)
    if credencial is None:
        return
    from calendario.models import ReservaCalendarEvent

    try:
        google_event_id = instance.calendar_event.google_event_id
    except ReservaCalendarEvent.DoesNotExist:
        return
    if not google_event_id:
        return
    # Se captura el id ANTES del borrado (el ReservaCalendarEvent cae por cascade).
    transaction.on_commit(lambda: services.borrar_evento(credencial, google_event_id))


@receiver(pre_save, sender=ConfiguracionTurnos)
def capturar_duracion_vieja(sender, instance, **kwargs):
    # En `post_save` la DB ya tiene el valor nuevo; guardamos el viejo acá para
    # poder comparar y decidir si hay que resincronizar.
    if not instance.pk:
        instance._duracion_vieja = None
        return
    try:
        instance._duracion_vieja = (
            ConfiguracionTurnos.objects.get(pk=instance.pk).duracion_minutos
        )
    except ConfiguracionTurnos.DoesNotExist:
        instance._duracion_vieja = None


@receiver(post_save, sender=ConfiguracionTurnos)
def resync_por_cambio_de_duracion(sender, instance, created, **kwargs):
    if not services.integracion_activa() or created:
        return
    vieja = getattr(instance, "_duracion_vieja", None)
    if vieja is not None and vieja != instance.duracion_minutos:
        gimnasio = instance.gimnasio
        transaction.on_commit(lambda: services.resync_duracion(gimnasio))
