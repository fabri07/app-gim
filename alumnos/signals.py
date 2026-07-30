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
from django.db.models.signals import post_save
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


@receiver(post_save, sender="alumnos.Alumno")
def sincronizar_acceso_con_estado(sender, instance, raw=False, **kwargs):
    """Mantiene `User.is_active` como espejo de `Alumno.estado`.

    Vive en una señal y no en las vistas por una razón concreta: `estado` se
    escribe desde TRES lugares distintos (el botón de baja, el form de la
    ficha —donde `estado` es un campo editable— y `crear_acceso`, que puede
    correr sobre un alumno ya dado de baja), y cualquier código futuro que
    haga `alumno.save()` es un cuarto. Ponerlo en cada vista garantiza que
    tarde o temprano una se olvide.

    El síntoma de que se olviden es especialmente feo: el alumno figura activo
    en el listado y no puede entrar, sin nada en pantalla que lo explique — o
    al revés, figura dado de baja y sigue entrando. Justo el problema que este
    frente vino a eliminar.

    `raw=True` significa que viene de `loaddata`: ahí no hay que tocar nada ni
    asumir que las FK relacionadas ya existen. `calendario/signals.py` no
    chequea `raw` y por eso el respaldo del proyecto usa `pg_dump` y nunca
    `dumpdata` (ver ISSUES.md); no repetimos ese error acá.

    **Límite conocido:** `post_save` NO se dispara con
    `Alumno.objects.filter(...).update(estado=...)` ni con `bulk_update`. Hoy
    no hay ninguno en el repo; si se agrega uno, tiene que sincronizar el
    acceso a mano o no usar `update()` para este campo.
    """
    if raw or instance.perfil_id is None:
        return

    perfil = instance.perfil
    # Mismo guard que `suplantacion.iniciar()` y `regenerar_password()`: un
    # `Alumno.perfil` apuntando a un Perfil STAFF es construible desde
    # /admin/, y sin esto dar de baja a ese alumno apagaría la cuenta del
    # STAFF, dejándolo afuera del sistema.
    if perfil.rol != Perfil.Rol.ALUMNO:
        return

    activo = instance.estado == instance.Estado.ACTIVO
    usuario = perfil.usuario
    if usuario.is_active != activo:
        usuario.is_active = activo
        usuario.save(update_fields=["is_active"])
