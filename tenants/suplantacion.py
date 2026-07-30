"""Suplantación de un alumno por parte del staff ("entrar como este alumno").

Existe para que el staff pueda resolver "no puedo entrar" y ver la app tal como
la ve su alumno, SIN que el sistema guarde ninguna contraseña legible. Se
evaluó y se descartó el camino alternativo —guardar las contraseñas de los
alumnos para mostrárselas al dueño— porque convierte esa cuenta en un depósito
de credenciales: ver el spec en
`docs/superpowers/specs/2026-07-30-portal-de-cuentas-design.md`.

La lógica vive en un servicio y no en la vista, mismo criterio que
`turnos/services.py`.

Las dos trampas de `django.contrib.auth.login()` que este módulo sortea, ambas
verificadas contra el código de Django y cubiertas por tests de regresión:

1. `login()` hace `request.session.flush()` cuando cambia el usuario. Escribir
   la clave de retorno ANTES la borraría en silencio, y la suplantación
   quedaría sin vuelta atrás.
2. `login()` emite `user_logged_in` al final, y hay dos receivers que
   corromperían datos: el de `alumnos/signals.py` estamparía
   `fecha_activacion` a un alumno que nunca entró (arruina la métrica de
   adopción) y `update_last_login`, conectado por `django.contrib.auth`,
   pisaría el "último ingreso" que muestra el panel de accesos.

**No usar `signal.disconnect()` para esto**: es mutación de estado global
compartido entre requests y no es thread-safe. Aunque hoy gunicorn corra con
workers sync de un solo hilo, es una bomba de tiempo.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model, login
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.utils import timezone

from tenants.models import Perfil, RegistroSuplantacion

CLAVE_SESION = "suplantacion"
MAX_DURACION = timedelta(hours=2)

# Ojo Frente C: cuando exista `tenants.backends.PerfilModelBackend` (y
# django-axes por delante en AUTHENTICATION_BACKENDS), hay que apuntar esta
# constante al backend propio. `login()` necesita saber cuál es porque no
# pasamos por `authenticate()`, y elegir el equivocado se manifiesta como
# "suplantar funciona pero me desloguea al primer click".
BACKEND = "django.contrib.auth.backends.ModelBackend"


def esta_activa(request):
    return CLAVE_SESION in request.session


def vencida(request):
    """True si la suplantación superó `MAX_DURACION`."""
    datos = request.session.get(CLAVE_SESION)
    if not datos:
        return False
    inicio = timezone.datetime.fromisoformat(datos["inicio"])
    return timezone.now() - inicio > MAX_DURACION


def _cambiar_de_usuario(request, usuario):
    """`login()` conservando `last_login` y sin disparar `fecha_activacion`.

    Los dos cuidados son obligatorios, no cosméticos (ver el docstring del
    módulo). `request._suplantacion_en_curso` lo lee el receiver de
    `alumnos/signals.py`; el `request` sobrevive al flush de sesión, así que es
    un canal seguro para pasarle la señal.

    `last_login` se lee de la BASE y no del objeto en memoria: si la instancia
    que llega viene desactualizada (algo la modificó con un `.update()` desde
    que se cargó), restaurar el valor viejo BORRARÍA el real. Se restaura con
    un UPDATE directo y no con `save()` para no arrastrar el resto de los
    campos del usuario.
    """
    User = get_user_model()
    ultimo_login = (
        User.objects.filter(pk=usuario.pk)
        .values_list("last_login", flat=True)
        .first()
    )
    request._suplantacion_en_curso = True
    login(request, usuario, backend=BACKEND)
    User.objects.filter(pk=usuario.pk).update(last_login=ultimo_login)


def iniciar(request, alumno):
    """Pasa la sesión del staff a la cuenta del alumno.

    `alumno` YA tiene que venir acotado al gimnasio del staff: la vista lo
    resuelve con `TenantScopedMixin`, que devuelve 404 si es de otro gimnasio.
    Este servicio no repite ese chequeo, pero sí todos los demás.
    """
    if esta_activa(request):
        # Encadenar suplantaciones haría imposible saber a qué cuenta volver.
        raise PermissionDenied("Ya estás viendo la app como otra persona.")

    if alumno.perfil is None:
        raise PermissionDenied("Este alumno todavía no tiene acceso.")
    if alumno.perfil.rol != Perfil.Rol.ALUMNO:
        raise PermissionDenied("Solo se puede entrar como un alumno.")
    if alumno.estado != alumno.Estado.ACTIVO:
        raise PermissionDenied("Este alumno está dado de baja.")

    usuario = alumno.perfil.usuario
    # Cinturón y tiradores: aunque el rol del Perfil diga ALUMNO, nunca
    # escalar hacia una cuenta con privilegios de plataforma.
    if usuario.is_superuser or usuario.is_staff:
        raise PermissionDenied("No se puede entrar como una cuenta con privilegios.")

    staff_usuario = request.user
    registro = RegistroSuplantacion.objects.create(
        gimnasio=alumno.gimnasio,
        staff_usuario=staff_usuario,
        alumno=alumno,
        ip=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
    )

    _cambiar_de_usuario(request, usuario)

    # DESPUÉS de login(), nunca antes: el flush de sesión lo borraría.
    request.session[CLAVE_SESION] = {
        "original_pk": staff_usuario.pk,
        "original_nombre": staff_usuario.get_username(),
        "alumno_nombre": str(alumno),
        "inicio": timezone.now().isoformat(),
        "registro_pk": registro.pk,
    }


def volver(request):
    """Devuelve la sesión al staff original.

    Fail-closed: si algo no cierra, se descarta la sesión entera en vez de
    intentar recuperarla. Que la sesión diga que sos staff no alcanza — se
    revalida todo contra la base, porque una cookie de sesión manipulada no
    debe poder hacerte saltar de cuenta ni de gimnasio.
    """
    datos = request.session.get(CLAVE_SESION)
    if not datos:
        raise PermissionDenied("No estás viendo la app como otra persona.")

    User = get_user_model()
    try:
        staff_usuario = User.objects.select_related("perfil__gimnasio").get(
            pk=datos["original_pk"]
        )
    except User.DoesNotExist:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no existe.")

    try:
        perfil = staff_usuario.perfil
    except ObjectDoesNotExist:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no tiene perfil.")

    if not staff_usuario.is_active or perfil.rol != Perfil.Rol.STAFF:
        request.session.flush()
        raise PermissionDenied("Tu usuario original ya no puede operar.")

    RegistroSuplantacion.objects.filter(
        pk=datos["registro_pk"], finalizada_en__isnull=True
    ).update(finalizada_en=timezone.now())

    # `login()` flushea la sesión, así que `CLAVE_SESION` se borra sola.
    _cambiar_de_usuario(request, staff_usuario)
