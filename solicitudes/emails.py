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
from django.core.mail import EmailMessage
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
    # `EmailMessage` en vez de `send_mail` sólo para poder poner `reply_to`:
    # el From es una casilla no-reply, así que la respuesta del interesado
    # tiene que ir a una casilla que alguien lea (`EMAIL_REPLY_TO`). Sin esa
    # variable el mail sale igual que antes, sin la cabecera.
    reply_to = [settings.EMAIL_REPLY_TO] if getattr(settings, "EMAIL_REPLY_TO", "") else None
    EmailMessage(
        subject=asunto,
        body=cuerpo,
        from_email=settings.DEFAULT_FROM_EMAIL or None,
        to=[destinatario],
        reply_to=reply_to,
    ).send(fail_silently=True)


def _contacto():
    """La casilla a la que se le puede contestar, o "" si no hay ninguna.

    Es `EMAIL_REPLY_TO` y no `SOPORTE_CONTACTO`: la primera es, por
    definición, una dirección de mail (va como cabecera `Reply-To`), y la
    segunda es texto libre que el panel muestra en pantalla y podría no ser un
    email. Sin ella, los mails NO invitan a escribir a ninguna parte: el `From`
    es una casilla no-reply, y prometer un canal que no existe es peor que no
    ofrecer ninguno (mismo criterio que el link de "olvidé mi contraseña", que
    se esconde si no hay `EMAIL_*`)."""
    return getattr(settings, "EMAIL_REPLY_TO", "")


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
    """Postergada: sigue viva, avisamos cuando se pueda.

    **`motivo_decision` NO va en el cuerpo**, ver `enviar_rechazo`.
    """
    _enviar(
        "Recibimos tu solicitud en TuGimApp",
        "lista_espera",
        {"solicitud": solicitud, "contacto": _contacto()},
        solicitud.email,
    )


def enviar_rechazo(solicitud):
    """Rechazada: breve, sin razones internas, y con la puerta abierta.

    **`motivo_decision` NO va en el cuerpo, y no hay que volver a ponerlo.** Es
    la nota de triage que el superadmin escribe para sí mismo: se pide con esa
    etiqueta en `plataforma/solicitud_detalle.html` y se muestra en el historial
    de auditoría (`SolicitudEvento.motivo`). Interpolarla acá mandaba ese texto
    tal cual al interesado — un "no es un gimnasio real" escrito para uso
    interno, leído por la persona de la que se escribió. Un campo no puede
    tener dos audiencias: si algún día hay que decirle algo puntual a un lead,
    va un campo NUEVO, rotulado como visible para él.
    """
    _enviar(
        "Sobre tu solicitud en TuGimApp",
        "rechazo",
        {"solicitud": solicitud, "contacto": _contacto()},
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
