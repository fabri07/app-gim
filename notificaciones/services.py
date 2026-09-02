"""Único punto de envío de Web Push de la app.

`_enviar` no depende de que el string de `VAPID_PRIVATE_KEY` esté en formato
raw/DER (lo que exigiría `pywebpush.webpush(vapid_private_key=<str>)`, que
internamente prueba ruta-de-archivo y si no lo trata como
`Vapid.from_string`, que NO entiende PEM con headers): en cambio se
construye explícitamente un `Vapid01` con `from_pem`, que sí acepta el PEM
completo tal como lo genera `vapid --gen`.
"""

from contextlib import contextmanager
import json
import logging

from django.conf import settings
from django.urls import reverse
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from notificaciones.models import RecordatorioEnviado, SuscripcionPush
from tenants.models import Perfil

logger = logging.getLogger(__name__)

_vapid_cache = None


def _get_vapid():
    global _vapid_cache
    if _vapid_cache is None:
        _vapid_cache = Vapid01.from_pem(settings.VAPID_PRIVATE_KEY.encode())
    return _vapid_cache


def _icono_url(gimnasio) -> str:
    return reverse("notificaciones:pwa_icono", args=[gimnasio.slug, 192])


#: Interruptor de proceso para operaciones en lote. Lo usa
#: `manage.py sembrar_demo`: sembrar una cuenta de demostración crea cientos
#: de reservas, y CADA reserva dispara un push al staff (ver
#: `notificaciones/signals.py`). Sin esto, llenar el gimnasio de prueba le
#: manda 300+ notificaciones al celular de quien lo corre.
#:
#: NO se implementa con `signal.disconnect()` -- ver CLAUDE.md: muta estado
#: global y no es thread-safe. Acá el efecto es el mismo (no se envía nada)
#: pero los receivers siguen conectados y el resto de la lógica corre igual.
_silenciado = False


@contextmanager
def silenciado():
    """Suprime el envío real de push dentro del bloque.

    Cubre también los `transaction.on_commit` de los signals, SIEMPRE que el
    bloque envuelva la transacción entera: los callbacks corren al cerrarse
    el `atomic()` más externo, y ese cierre tiene que pasar acá adentro.
    """
    global _silenciado
    anterior = _silenciado
    _silenciado = True
    try:
        yield
    finally:
        _silenciado = anterior


def _enviar(suscripcion, payload: dict) -> None:
    if _silenciado:
        return
    if not settings.PUSH_ENABLED:
        return
    try:
        webpush(
            subscription_info={
                "endpoint": suscripcion.endpoint,
                "keys": {"p256dh": suscripcion.p256dh, "auth": suscripcion.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=_get_vapid(),
            vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            # Suscripción muerta (el usuario desinstaló/revocó): se apaga, no
            # se borra -- conserva el historial, mismo criterio que
            # Novedad.activa / MedioCobro.activo.
            suscripcion.activa = False
            suscripcion.save(update_fields=["activa"])
        else:
            logger.warning("Error enviando push a suscripción %s: %s", suscripcion.pk, exc)


def notificar_a_usuario(usuario, payload: dict) -> None:
    for suscripcion in SuscripcionPush.objects.filter(usuario=usuario, activa=True):
        _enviar(suscripcion, payload)


def notificar_a_gimnasio(gimnasio, payload: dict, *, rol=None) -> None:
    qs = SuscripcionPush.objects.for_gimnasio(gimnasio).filter(activa=True)
    if rol is not None:
        qs = qs.filter(usuario__perfil__rol=rol)
    for suscripcion in qs:
        _enviar(suscripcion, payload)


def _ya_notificado(gimnasio, tipo, objeto_id) -> bool:
    """True si ya se creó el `RecordatorioEnviado` para este evento (dedup
    para los eventos que puede volver a evaluar el cron)."""
    _, creado = RecordatorioEnviado.objects.get_or_create(
        gimnasio=gimnasio, tipo=tipo, objeto_id=objeto_id
    )
    return not creado


def notificar_novedad(novedad) -> None:
    # Alumno sin Perfil vinculado todavía (recién dado de alta, sin acceso
    # al portal): no hay a quién mandarle el push. Mismo guard que
    # `turnos/services.py::_generar_novedades_personales`. Chequeado ANTES
    # del dedup: si se marca "ya notificado" sin haber podido entregar nada,
    # esa novedad personal queda sin avisar para siempre, incluso después de
    # que el staff le cree acceso al alumno más adelante.
    if novedad.alumno_id and novedad.alumno.perfil_id is None:
        return
    if _ya_notificado(
        novedad.gimnasio, RecordatorioEnviado.Tipo.NOVEDAD_PUBLICADA, novedad.pk
    ):
        return
    payload = {
        "title": novedad.titulo,
        "body": novedad.mensaje[:140],
        "url": "/",
        "icon": _icono_url(novedad.gimnasio),
    }
    if novedad.alumno_id:
        notificar_a_usuario(novedad.alumno.perfil.usuario, payload)
    else:
        notificar_a_gimnasio(novedad.gimnasio, payload, rol=Perfil.Rol.ALUMNO)


def notificar_rutina_asignada(rutina_asignada) -> None:
    # `perfil` es un FK forward nullable: sin Perfil vinculado, accederlo
    # devuelve `None` (no lanza `ObjectDoesNotExist` -- eso solo pasa del
    # lado reverso de la relación). Chequear `perfil_id`, no envolver en
    # try/except, mismo patrón que `turnos/services.py:517`.
    if rutina_asignada.alumno.perfil_id is None:
        return
    # Dedup: desde que los planes programados a futuro se avisan el día que
    # arrancan, este service lo llama también el cron (cada ~15 min), no solo
    # el signal de creación. Sin esto el alumno recibiría el mismo push 96
    # veces en el día del relevo.
    if _ya_notificado(
        rutina_asignada.gimnasio,
        RecordatorioEnviado.Tipo.RUTINA_INICIADA,
        rutina_asignada.pk,
    ):
        return
    usuario = rutina_asignada.alumno.perfil.usuario
    payload = {
        "title": "Nueva rutina asignada",
        "body": f"Tenés una rutina nueva a partir del {rutina_asignada.fecha_inicio:%d/%m}.",
        "url": "/",
        "icon": _icono_url(rutina_asignada.gimnasio),
    }
    notificar_a_usuario(usuario, payload)


def notificar_nueva_reserva(reserva) -> None:
    payload = {
        "title": "Nueva reserva de turno",
        "body": f"{reserva.alumno} reservó el {reserva.fecha:%d/%m} a las {reserva.hora_inicio:%H:%M}.",
        "url": reverse("turnos:agenda"),
        "icon": _icono_url(reserva.gimnasio),
    }
    notificar_a_gimnasio(reserva.gimnasio, payload, rol=Perfil.Rol.STAFF)


def notificar_comprobante_subido(pago) -> None:
    payload = {
        "title": "Nuevo comprobante subido",
        "body": f"{pago.alumno} subió el comprobante de {pago.mes:02d}/{pago.anio}.",
        "url": reverse("pagos:listado"),
        "icon": _icono_url(pago.gimnasio),
    }
    notificar_a_gimnasio(pago.gimnasio, payload, rol=Perfil.Rol.STAFF)


def notificar_pago_por_vencer(pago) -> None:
    # Orden a propósito: primero confirmar que se puede entregar (el alumno
    # tiene Perfil), recién después marcar "ya notificado". Si el dedup se
    # marcara antes, un alumno sin acceso al portal todavía quedaría sin
    # este aviso para siempre, incluso una vez que el staff le cree acceso.
    if pago.alumno.perfil_id is None:
        return
    usuario = pago.alumno.perfil.usuario
    if _ya_notificado(pago.gimnasio, RecordatorioEnviado.Tipo.PAGO_POR_VENCER, pago.pk):
        return
    payload = {
        "title": "Tu cuota está por vencer",
        "body": f"La cuota de {pago.mes:02d}/{pago.anio} vence el día {pago.gimnasio.dia_vencimiento_pago}.",
        "url": "/",
        "icon": _icono_url(pago.gimnasio),
    }
    notificar_a_usuario(usuario, payload)


def notificar_pago_vencido(pago) -> None:
    if pago.alumno.perfil_id is None:
        return
    usuario = pago.alumno.perfil.usuario
    if _ya_notificado(pago.gimnasio, RecordatorioEnviado.Tipo.PAGO_VENCIDO, pago.pk):
        return
    payload = {
        "title": "Tu cuota está vencida",
        "body": f"La cuota de {pago.mes:02d}/{pago.anio} ya venció.",
        "url": "/",
        "icon": _icono_url(pago.gimnasio),
    }
    notificar_a_usuario(usuario, payload)


def notificar_turno_proximo(reserva) -> None:
    if reserva.alumno.perfil_id is None:
        return
    usuario = reserva.alumno.perfil.usuario
    if _ya_notificado(reserva.gimnasio, RecordatorioEnviado.Tipo.TURNO_PROXIMO, reserva.pk):
        return
    payload = {
        "title": "Tu turno está por empezar",
        "body": f"Tenés un turno hoy a las {reserva.hora_inicio:%H:%M}.",
        "url": reverse("turnos:mis_turnos"),
        "icon": _icono_url(reserva.gimnasio),
    }
    notificar_a_usuario(usuario, payload)
