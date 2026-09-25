"""
Máquina de estados de las solicitudes de acceso — ÚNICO lugar que escribe
sobre `SolicitudAcceso`.

Todas las transiciones son idempotentes y los mails se disparan por
`transaction.on_commit` (mismo patrón que `notificaciones/`/`calendario/`: si la
transacción se revierte, no sale ningún mail). Las URLs absolutas las arma la
vista (tiene el `request`) y las pasa como callables/valores ya resueltos.

Aprobar NO reimplementa el alta: delega en `tenants.services.crear_gimnasio`
(única fuente de tenants). La contraseña generada se descarta; el dueño la
define por el link de invitación (token de reset de Django).
"""

import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from solicitudes import emails
from solicitudes.models import SolicitudAcceso, SolicitudAccesoToken, SolicitudEvento
from tenants.services import crear_gimnasio, normalizar_email

#: Cuánto vive el link de verificación de email.
DIAS_VALIDEZ_TOKEN = 3

_Estado = SolicitudAcceso.Estado


def _evento(solicitud, de, a, actor=None, motivo=""):
    SolicitudEvento.objects.create(
        solicitud=solicitud, de_estado=de, a_estado=a, actor=actor, motivo=motivo
    )


def crear_solicitud(*, datos, ip_hash="", url_verificacion_para):
    """Crea una solicitud `NO_VERIFICADA` y dispara el mail de doble opt-in.

    `datos`: dict con SOLO campos del modelo (la vista ya sacó honeypot/tiempo).
    `url_verificacion_para(token)`: callable que arma la URL absoluta de
    verificación (la vista lo provee con el `request`).

    Neutral a enumeración: si ya hay un trámite ABIERTO para ese email, el
    índice parcial levanta `IntegrityError`, se traga y devuelve `None` — sin
    revelar nada. Devuelve la `SolicitudAcceso` creada, o `None` si chocó.
    """
    datos = {**datos, "email": normalizar_email(datos["email"])}
    token_str = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            solicitud = SolicitudAcceso.objects.create(ip_hash=ip_hash, **datos)
            SolicitudAccesoToken.objects.create(
                solicitud=solicitud,
                token=token_str,
                proposito=SolicitudAccesoToken.Proposito.VERIFICACION,
                expira=timezone.now() + timedelta(days=DIAS_VALIDEZ_TOKEN),
            )
            _evento(solicitud, "", _Estado.NO_VERIFICADA)
            url = url_verificacion_para(token_str)
            transaction.on_commit(lambda: emails.enviar_verificacion(solicitud, url))
        return solicitud
    except IntegrityError:
        return None


def verificar(*, token_str, url_panel_para):
    """`NO_VERIFICADA -> PENDIENTE` al confirmar el email. Idempotente.

    `url_panel_para(solicitud)`: callable que arma la URL absoluta a la ficha
    en el panel (para el aviso al dueño). Devuelve `(solicitud, recien)` donde
    `recien` es True solo si esta llamada hizo la transición; `(None, False)`
    si el token no existe.
    """
    token = (
        SolicitudAccesoToken.objects.select_related("solicitud")
        .filter(
            token=token_str,
            proposito=SolicitudAccesoToken.Proposito.VERIFICACION,
        )
        .first()
    )
    if token is None:
        return None, False
    solicitud = token.solicitud
    if token.usado_en is not None or token.expira < timezone.now():
        # Ya verificado antes (idempotente) o vencido (sigue NO_VERIFICADA).
        return solicitud, False
    with transaction.atomic():
        # Lock sobre la fila (mismo criterio que aprobar/_decidir): dos GETs
        # concurrentes del mismo link (doble click, prefetch, escáner de mails
        # + persona) no pueden transicionar los dos y duplicar el aviso al
        # dueño. El segundo espera el lock, relee estado=PENDIENTE y corta.
        solicitud = SolicitudAcceso.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud.estado != _Estado.NO_VERIFICADA:
            return solicitud, False
        token.usado_en = timezone.now()
        token.save(update_fields=["usado_en", "modificado"])
        de = solicitud.estado
        solicitud.estado = _Estado.PENDIENTE
        solicitud.verificada_en = timezone.now()
        solicitud.save(update_fields=["estado", "verificada_en", "modificado"])
        _evento(solicitud, de, solicitud.estado)
        url = url_panel_para(solicitud)
        transaction.on_commit(lambda: emails.enviar_aviso_dueno(solicitud, url))
    return solicitud, True


def aprobar(*, solicitud, actor, link_invitacion_para):
    """`PENDIENTE`/`LISTA_ESPERA -> APROBADA`, con lock e idempotente.

    Delega en `crear_gimnasio` (única alta). `link_invitacion_para(usuario)`
    arma la URL absoluta para definir contraseña. Devuelve
    `(gimnasio, resultado)` con `resultado` en
    `{"aprobada", "ya_existia", "email_en_uso"}`.
    """
    with transaction.atomic():
        solicitud = SolicitudAcceso.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud.estado == _Estado.APROBADA:
            return solicitud.gimnasio_creado, "ya_existia"
        try:
            gimnasio, usuario, _password = crear_gimnasio(
                nombre=solicitud.nombre_gimnasio, email=solicitud.email
            )
        except ValidationError:
            # Ya hay un usuario con ese email: no se crea nada ni se aprueba.
            transaction.on_commit(lambda: emails.enviar_ya_tenes_cuenta(solicitud))
            return None, "email_en_uso"
        de = solicitud.estado
        solicitud.estado = _Estado.APROBADA
        solicitud.gimnasio_creado = gimnasio
        solicitud.decidida_en = timezone.now()
        solicitud.decidida_por = actor
        solicitud.save(
            update_fields=[
                "estado",
                "gimnasio_creado",
                "decidida_en",
                "decidida_por",
                "modificado",
            ]
        )
        _evento(solicitud, de, solicitud.estado, actor=actor)
        url = link_invitacion_para(usuario)
        transaction.on_commit(lambda: emails.enviar_invitacion(solicitud, url))
    return gimnasio, "aprobada"


def _decidir(solicitud, actor, motivo, nuevo_estado, mail):
    """Núcleo común de rechazar / lista de espera: transición + evento + mail."""
    with transaction.atomic():
        solicitud = SolicitudAcceso.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud.estado == nuevo_estado:
            return solicitud
        de = solicitud.estado
        solicitud.estado = nuevo_estado
        solicitud.decidida_en = timezone.now()
        solicitud.decidida_por = actor
        solicitud.motivo_decision = motivo
        solicitud.save(
            update_fields=[
                "estado",
                "decidida_en",
                "decidida_por",
                "motivo_decision",
                "modificado",
            ]
        )
        _evento(solicitud, de, nuevo_estado, actor=actor, motivo=motivo)
        transaction.on_commit(lambda: mail(solicitud))
    return solicitud


def rechazar(*, solicitud, actor, motivo=""):
    return _decidir(solicitud, actor, motivo, _Estado.RECHAZADA, emails.enviar_rechazo)


def a_lista_espera(*, solicitud, actor, motivo=""):
    return _decidir(
        solicitud, actor, motivo, _Estado.LISTA_ESPERA, emails.enviar_lista_espera
    )


def reenviar_invitacion(*, solicitud, usuario, link_invitacion_para):
    """Reenvía el mail de invitación de una solicitud ya `APROBADA` (el link de
    reset caduca a `PASSWORD_RESET_TIMEOUT`). `usuario` es el dueño recién
    creado; `link_invitacion_para(usuario)` arma la URL fresca."""
    if solicitud.estado != _Estado.APROBADA:
        return
    url = link_invitacion_para(usuario)
    transaction.on_commit(lambda: emails.enviar_invitacion(solicitud, url))


def expirar_vencidas(*, ahora=None):
    """`NO_VERIFICADA` con su token de verificación vencido -> `EXPIRADA`.

    Lo corre un comando de management. No manda mail (no hay nada útil que
    decirle a quien nunca confirmó). Devuelve cuántas expiró.
    """
    ahora = ahora or timezone.now()
    vencidas = SolicitudAcceso.objects.filter(
        estado=_Estado.NO_VERIFICADA,
        tokens__proposito=SolicitudAccesoToken.Proposito.VERIFICACION,
        tokens__expira__lt=ahora,
    ).distinct()
    n = 0
    for solicitud in vencidas:
        with transaction.atomic():
            solicitud = SolicitudAcceso.objects.select_for_update().get(pk=solicitud.pk)
            if solicitud.estado != _Estado.NO_VERIFICADA:
                continue
            _evento(solicitud, solicitud.estado, _Estado.EXPIRADA)
            solicitud.estado = _Estado.EXPIRADA
            solicitud.save(update_fields=["estado", "modificado"])
            n += 1
    return n
