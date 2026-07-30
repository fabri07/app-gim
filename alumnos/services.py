"""Lógica de negocio del acceso de un alumno.

Vive acá y no en las vistas por el mismo criterio que `turnos/services.py`: el
alta de un acceso toca tres modelos (`User`, `Perfil`, `Alumno`) y tiene que
ser atómica.

La contraseña NUNCA la elige el staff: la genera la app. Un dueño de gimnasio
no va a inventar cincuenta contraseñas razonables, y las que inventaría serían
peores que las generadas. Tampoco se guarda en ningún lado en texto plano: la
función que la crea la devuelve una sola vez y quien llama tiene que mostrarla
en el momento (ver `alumnos/views.py` y el spec del portal de cuentas para por
qué se descartó guardarlas para consultarlas después).
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from alumnos.identidad import normalizar_identificador
from alumnos.models import Alumno
from tenants.models import Perfil
from tenants.services import generar_password


class IdentificadorEnUso(Exception):
    """El email/teléfono ya está tomado en la plataforma.

    `User.username` es único GLOBAL (no hay namespacing por gimnasio), así que
    esto pasa con la misma persona entrenando en dos gimnasios o con un mail
    familiar compartido entre hermanos. Es un riesgo aceptado y documentado:
    con una sola pantalla de login sin selección de gimnasio, el identificador
    tiene que ser globalmente único.
    """


@transaction.atomic
def crear_acceso(alumno, tipo, identificador):
    """Crea el login del alumno y devuelve la contraseña en claro.

    Es la ÚNICA vez que la contraseña existe en texto plano: quien llama tiene
    que mostrarla en el momento. Después queda solo el hash, así que no se
    puede recuperar — ni el staff ni nadie.

    Levanta `ValidationError` si el identificador no es válido, e
    `IdentificadorEnUso` si ya está tomado. En los dos casos no queda nada
    creado.

    El `select_for_update()` sobre el `Alumno` serializa dos altas concurrentes
    sobre el mismo alumno — mismo patrón anti-TOCTOU que
    `turnos/services.py::crear_reserva` e
    `importaciones/services.py::confirmar_importacion_*`. Sin él, un doble
    submit (el form va boosteado por htmx y no deshabilita el botón) creaba dos
    `User`+`Perfil` y dejaba uno huérfano: podía loguearse, no aparecía en el
    panel de accesos y el staff no tenía forma de verlo ni revocarlo.
    """
    username = normalizar_identificador(tipo, identificador)

    # Relee la fila con lock: el `alumno` que llega puede estar desactualizado.
    alumno = Alumno.objects.select_for_update().get(pk=alumno.pk)
    if alumno.perfil_id is not None:
        raise IdentificadorEnUso(username)

    User = get_user_model()
    if User.objects.filter(username=username).exists():
        raise IdentificadorEnUso(username)

    password = generar_password()
    try:
        usuario = User.objects.create_user(
            username=username,
            password=password,
            # `email` se puebla solo si el identificador ES un email. Lo
            # necesita el password reset del Frente C, que busca por
            # `User.email`; meter un teléfono ahí lo rompería.
            email=username if "@" in username else "",
        )
    except IntegrityError as exc:
        # El lock cubre las altas sobre el MISMO alumno, no el caso de dos
        # alumnos distintos reclamando el mismo identificador a la vez. Ahí
        # gana el UNIQUE de `auth_user.username`: se traduce a un error de
        # formulario en vez de un 500.
        raise IdentificadorEnUso(username) from exc

    perfil = Perfil.objects.create(
        usuario=usuario, gimnasio=alumno.gimnasio, rol=Perfil.Rol.ALUMNO
    )
    alumno.perfil = perfil
    # `save()` completo (no `update_fields`) para que el receiver de
    # `alumnos/signals.py` sincronice `is_active` con el estado: si el alumno
    # ya estaba dado de baja, su acceso tiene que nacer apagado.
    alumno.save()
    return password


@transaction.atomic
def regenerar_password(alumno):
    """Nueva contraseña al azar para un alumno que ya tiene acceso.

    Devuelve la nueva en claro, con el mismo contrato que `crear_acceso`.

    Efecto colateral deseado y gratis: esto EXPULSA al alumno de sus sesiones
    vivas. `auth.get_user()` compara `HASH_SESSION_KEY` contra
    `user.get_session_auth_hash()`, que deriva del hash de la contraseña, así
    que al cambiarla las sesiones abiertas dejan de validar.
    """
    # Cinturón y tiradores, igual que `suplantacion.iniciar()`: un
    # `Alumno.perfil` apuntando a un Perfil STAFF (construible desde /admin/)
    # dejaría a cualquier staff resetear la contraseña de otro staff y entrar
    # con ella.
    if alumno.perfil.rol != Perfil.Rol.ALUMNO:
        raise PermissionDenied("Ese acceso no es el de un alumno.")

    usuario = alumno.perfil.usuario
    password = generar_password()
    usuario.set_password(password)
    usuario.save(update_fields=["password"])
    return password
