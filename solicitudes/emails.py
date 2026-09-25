"""
Envío de los correos del embudo de solicitudes.

Funciones finas, cada una arma su contexto y manda un mail multipart (.txt +
.html). El backend lo decide `config/settings.py`: SMTP (Resend) si `EMAIL_*`
está configurado, consola si no (nunca rompe), locmem en tests. Por eso no hay
que "gatear" el envío: sin config, los mails van a la consola de dev.

Las URLs absolutas (verificación, invitación, ficha del panel) las calcula
quien llama (la vista tiene el `request`), porque estos módulos no tienen uno.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string


def _enviar(asunto, base_template, contexto, destinatario):
    """Manda un mail de TEXTO PLANO (mismo criterio YAGNI que el reset de
    contraseña del proyecto: nada de HTML email). El backend lo decide
    settings: SMTP si hay `EMAIL_*`, consola si no, locmem en tests.

    **`fail_silently=True` a propósito:** estos envíos se disparan por
    `transaction.on_commit`, DESPUÉS de que la fila se commiteó. Si un fallo
    transitorio de SMTP levantara acá, el error subiría por el callback de
    on_commit y tiraría un 500 al interesado con la solicitud YA creada — y el
    índice único parcial le impediría reintentar. El mail es best-effort (mismo
    criterio que el push del proyecto: "esto es telemetría y no puede tumbar una
    página"); la recuperación es el reenvío desde el panel y el cron de
    expiración, no un 500."""
    if not destinatario:
        return
    cuerpo = render_to_string(f"solicitudes/email/{base_template}.txt", contexto)
    send_mail(
        asunto,
        cuerpo,
        settings.DEFAULT_FROM_EMAIL or None,
        [destinatario],
        fail_silently=True,
    )


def enviar_verificacion(solicitud, url_verificacion):
    """Doble opt-in: confirmá que este email es tuyo."""
    _enviar(
        "Confirmá tu email para tu solicitud en TuGimApp",
        "verificacion",
        {"solicitud": solicitud, "url_verificacion": url_verificacion},
        solicitud.email,
    )


def enviar_aviso_dueno(solicitud, url_panel):
    """Aviso al dueño del producto: entró una solicitud verificada.

    La bandeja ES la cola: el mail trae la ficha y un link al panel. Va a
    `SOLICITUDES_AVISO_EMAIL` (o `DEFAULT_FROM_EMAIL` como fallback).
    """
    destino = getattr(settings, "SOLICITUDES_AVISO_EMAIL", "") or settings.DEFAULT_FROM_EMAIL
    _enviar(
        f"Nueva solicitud: {solicitud.nombre_gimnasio}",
        "aviso_dueno",
        {"solicitud": solicitud, "url_panel": url_panel},
        destino,
    )


def enviar_invitacion(solicitud, url_invitacion):
    """Aprobada: definí tu contraseña (link de reset de Django reusado)."""
    _enviar(
        "Tu cuenta en TuGimApp está lista — definí tu contraseña",
        "aprobado_invitacion",
        {"solicitud": solicitud, "url_invitacion": url_invitacion},
        solicitud.email,
    )


def enviar_lista_espera(solicitud):
    _enviar(
        "Recibimos tu solicitud en TuGimApp",
        "lista_espera",
        {"solicitud": solicitud},
        solicitud.email,
    )


def enviar_rechazo(solicitud):
    _enviar(
        "Sobre tu solicitud en TuGimApp",
        "rechazo",
        {"solicitud": solicitud},
        solicitud.email,
    )


def enviar_ya_tenes_cuenta(solicitud):
    """El email ya tiene una cuenta de staff: no se crea nada, se le dice que
    entre. Neutral a enumeración de cara al público (esto es un mail directo al
    dueño de esa casilla, no una respuesta HTTP)."""
    _enviar(
        "Ya tenés una cuenta en TuGimApp",
        "ya_tenes_cuenta",
        {"solicitud": solicitud},
        solicitud.email,
    )
