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
from django.db.models.signals import post_save, pre_save
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


@receiver(pre_save, sender="alumnos.Alumno")
def registrar_fecha_de_baja(sender, instance, raw=False, **kwargs):
    """Estampa (o limpia) `Alumno.fecha_baja` al cambiar `estado`.

    En `pre_save` y no en `post_save` para que el valor viaje en el MISMO
    UPDATE: con un `post_save` habría que hacer un segundo `save()`, que
    volvería a disparar las señales.

    Solo actúa en la TRANSICIÓN, no en cada guardado: si reestampara siempre,
    corregirle el teléfono a alguien dado de baja hace un año lo movería al mes
    actual y el gráfico de bajas mentiría. Reactivar limpia la fecha, o el
    alumno que vuelve seguiría contando como baja para siempre.

    Vive en una señal por el mismo motivo que `sincronizar_acceso_con_estado`:
    `estado` se escribe desde tres lugares distintos y cualquier
    `alumno.save()` futuro es un cuarto.

    **Mismo límite conocido:** `pre_save` no se dispara con `QuerySet.update()`
    ni `bulk_update()`.
    """
    if raw:
        return

    inactivo = instance.estado == instance.Estado.INACTIVO

    if instance.pk is None:
        # Alta directa en estado inactivo: hay baja, aunque no haya transición.
        instance.fecha_baja = timezone.localdate() if inactivo else None
        # Ancla del ciclo de cobro. Sin esto el alumno nuevo no recibe NINGUNA
        # cuota (`generar_pagos_pendientes` filtra por ancla no nula) y es un
        # agujero de facturación silencioso. La corrige después la señal de la
        # primera rutina, si el alumno arranca a entrenar otro día.
        if instance.fecha_inicio_ciclo is None:
            instance.fecha_inicio_ciclo = timezone.localdate()
        return

    estaba_inactivo = (
        sender.objects.filter(pk=instance.pk)
        .values_list("estado", flat=True)
        .first()
        == instance.Estado.INACTIVO
    )
    if inactivo and not estaba_inactivo:
        instance.fecha_baja = timezone.localdate()
    elif not inactivo and estaba_inactivo:
        instance.fecha_baja = None
        _reanclar_ciclo_al_reactivar(instance)


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


def _reanclar_ciclo_al_reactivar(instance):
    """Mueve el ancla del ciclo a hoy cuando el alumno vuelve de una baja.

    Durante la baja no se emiten cuotas (`generar_pagos_pendientes` filtra por
    `estado=ACTIVO`) pero el ancla sigue avanzando sola. Sin esto, el alumno
    que vuelve después de tres meses recibe esa misma noche una cuota cuyo
    período transcurrió casi entero mientras estaba de baja, el cron la vence
    en la misma corrida (genera y vence seguido) y —con la tolerancia
    prendida— queda sin rutina ni turnos el día 1 de su regreso.

    **Condicional a propósito**: solo se re-ancla si el ciclo vigente arrancó
    ANTES de hoy. Re-anclar siempre le regalaría hasta 27 días de cuota al que
    se dio de baja por error y volvió el mismo día.

    Va en el `pre_save` que ya detecta la transición para que el valor viaje
    en el mismo UPDATE que `fecha_baja = None`, mismo argumento que el
    docstring de `registrar_fecha_de_baja`.

    **Límite conocido**, igual que los otros receivers de este archivo: no se
    dispara con `QuerySet.update()` ni `bulk_update()`.
    """
    from pagos.models import ciclo_vigente

    hoy = timezone.localdate()
    ciclo = ciclo_vigente(instance.fecha_inicio_ciclo, hoy)
    if ciclo is None or ciclo[0] < hoy:
        instance.fecha_inicio_ciclo = hoy


@receiver(post_save, sender="rutinas.RutinaAsignada")
def anclar_ciclo_a_la_primera_rutina(sender, instance, created, raw=False, **kwargs):
    """El ciclo de cobro arranca el día que el alumno empieza a entrenar.

    Regla de producto: el alumno se puede dar de alta un lunes y arrancar el
    jueves; la cuota tiene que seguir a la rutina, no al trámite.

    Se corrige el ancla SOLO si el alumno todavía no tiene ninguna cuota
    emitida. Una vez que se le facturó algo, mover el ancla solaparía períodos
    y volvería a cobrar días ya cobrados -- y el `unique_together` no lo
    impide, porque solo prohíbe dos cuotas con el MISMO arranque, no dos
    períodos que se pisan.

    `sender` por string y no importando `RutinaAsignada`: el orden de
    dependencia es `alumnos -> rutinas`, y un import real lo invertiría. Es el
    mismo patrón que ya usan los otros dos receivers de este archivo.

    `instance.fecha_inicio` y no `localdate()`: un plan cargado a futuro tiene
    que anclar el ciclo el día que arranca de verdad.
    """
    if raw or not created:
        return

    alumno = instance.alumno
    if alumno.pagos.exists():
        return
    if alumno.fecha_inicio_ciclo == instance.fecha_inicio:
        return
    alumno.fecha_inicio_ciclo = instance.fecha_inicio
    alumno.save(update_fields=["fecha_inicio_ciclo", "modificado"])
