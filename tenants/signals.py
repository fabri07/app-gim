"""
Estampa `Gimnasio.fecha_activacion_bloqueo` la primera vez que el dueño
configura los días de tolerancia.

Existe para que **prender el bloqueo por falta de pago no sea retroactivo**.
Sin esta fecha, el primer request después de guardar el formulario dejaría
bloqueados de golpe a todos los alumnos que arrastren cualquier cuota impaga
histórica: el que estuvo de licencia, el que pagó en efectivo y el staff nunca
confirmó, el becado al que nunca se le cargó un monto. Es exactamente el caso
que hay que evitar —un alumno que pagó y se queda sin acceso— y encima le pasa
al gimnasio entero el mismo día.

`pagos/acceso.py` ignora las cuotas cuyo período arrancó antes de esta fecha.

Vive en una señal y no en `GimnasioUpdateView` por el mismo motivo que
`alumnos/signals.py::sincronizar_acceso_con_estado`: `dias_tolerancia_pago` se
puede escribir desde el form, desde `/admin/` y desde cualquier `save()`
futuro, y ponerlo en la vista garantiza que algún camino se lo olvide.

**Límite conocido**, igual que el resto de las señales del proyecto: `pre_save`
no se dispara con `QuerySet.update()` ni `bulk_update()`.
"""

from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(pre_save, sender="tenants.Gimnasio")
def registrar_activacion_del_bloqueo(sender, instance, raw=False, **kwargs):
    if raw:
        return

    tiene_tolerancia = instance.dias_tolerancia_pago is not None

    if instance.pk is None:
        # Gimnasio nuevo creado ya con tolerancia: la activación es ahora.
        instance.fecha_activacion_bloqueo = (
            timezone.localdate() if tiene_tolerancia else None
        )
        return

    tenia_tolerancia = (
        sender.objects.filter(pk=instance.pk)
        .values_list("dias_tolerancia_pago", flat=True)
        .first()
        is not None
    )
    if tiene_tolerancia and not tenia_tolerancia:
        # Solo en la TRANSICIÓN apagado -> prendido. Si reestampara en cada
        # guardado, cambiarle el logo al gimnasio movería la fecha de corte y
        # volvería a perdonar deudas que ya estaban bloqueando.
        instance.fecha_activacion_bloqueo = timezone.localdate()
    elif not tiene_tolerancia and tenia_tolerancia:
        # Apagar el bloqueo limpia la marca: si mañana lo vuelven a prender,
        # el corte tiene que ser ese día nuevo y no el viejo.
        instance.fecha_activacion_bloqueo = None
