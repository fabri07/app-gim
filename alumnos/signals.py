"""
Registra `Alumno.fecha_activacion` en el primer login exitoso.

Se activa acá (señal `user_logged_in`), no en la vista de login: la vista de
login es la genérica de Django, compartida con `staff` (ver
`tenants/urls.py`), y no queremos bifurcar esa vista por rol solo para esto.
Una señal es el punto de extensión correcto para "algo pasa en TODO login",
sin acoplar el flujo de auth al dominio de alumnos.
"""

from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import ObjectDoesNotExist
from django.dispatch import receiver
from django.utils import timezone

from tenants.models import Perfil


@receiver(user_logged_in)
def registrar_primera_activacion(sender, request, user, **kwargs):
    # Que el staff entre a la cuenta del alumno para ayudarlo NO es una
    # activación del alumno: marcarla corrompería la métrica de adopción, que
    # es justamente lo que este campo mide. `tenants/suplantacion.py` marca el
    # request antes de llamar a `login()`; el request sobrevive al flush de
    # sesión, así que es un canal seguro (y no muta estado global como sí lo
    # haría desconectar la señal).
    if getattr(request, "_suplantacion_en_curso", False):
        return

    try:
        perfil = user.perfil
    except ObjectDoesNotExist:
        return

    if perfil.rol != Perfil.Rol.ALUMNO:
        return

    try:
        alumno = perfil.alumno
    except ObjectDoesNotExist:
        return

    if alumno.fecha_activacion is None:
        alumno.fecha_activacion = timezone.now()
        alumno.save(update_fields=["fecha_activacion"])
