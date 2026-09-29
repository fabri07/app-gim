"""
Los avisos del cobro online al dueño del producto (superadmin).

Molde exacto de `solicitudes/emails.py`: texto plano, `EmailMessage` para poder
poner `Reply-To`, y `fail_silently=True`. Van a `COBROS_AVISO_EMAIL` (o
`DEFAULT_FROM_EMAIL` como fallback). Los dispara `suscripcion/services.py` por
`transaction.on_commit`, con los valores ya resueltos: si la transacción se
revierte, no sale ningún mail que cuente algo que no quedó en la base.

La URL al panel la calcula quien llama (este módulo no tiene `request`, y el
cron de reconciliación tampoco): puede llegar vacía, y entonces el mail sale
sin link.
"""

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

#: Qué hacer en cada caso de «cobro con problema». Es el cuerpo del mail: el
#: superadmin tiene que poder actuar leyéndolo, sin abrir el código.
PROBLEMAS = {
    "duplicado": (
        "Pago duplicado",
        "El gimnasio pagó un período que ya estaba registrado (o que se pisa "
        "con un pago cargado a mano). La plata entró pero NO se anotó un "
        "segundo pago. Revisá el período en el panel y, si corresponde, "
        "devolvé el pago desde el panel de Mercado Pago.",
    ),
    "revisar": (
        "Monto a revisar",
        "Mercado Pago informa un monto o una moneda distintos de los que se "
        "le cobraron al gimnasio. El pago NO se acreditó. Revisá el monto en "
        "el panel de Mercado Pago y, si está bien, registralo a mano; si no, "
        "devolvelo.",
    ),
    "devuelto": (
        "Pago devuelto",
        "Mercado Pago informa que el pago se devolvió o tuvo un contracargo. "
        "El pago registrado NO se borró y la cuenta NO se congeló: cortar el "
        "acceso es una decisión tuya, desde el panel.",
    ),
    "pago_adicional": (
        "Pago adicional",
        "El gimnasio pagó de nuevo un link que ya estaba pagado. Ese segundo "
        "pago no se anotó en ningún lado: hay que devolverlo desde el panel "
        "de Mercado Pago.",
    ),
}


def _enviar(asunto, base_template, contexto):
    """Manda un mail de TEXTO PLANO al dueño del producto.

    **`fail_silently=True` a propósito:** estos envíos se disparan por
    `transaction.on_commit`, DESPUÉS de que el pago quedó commiteado. Si un
    fallo transitorio de SMTP levantara acá, el error subiría por el callback
    de on_commit y le tiraría un 500 al webhook de Mercado Pago (que
    reintentaría un pago YA acreditado) o al gimnasio que acaba de pagar. El
    mail es best-effort (mismo criterio que el push del proyecto: "esto es
    telemetría y no puede tumbar una página"); la fuente de verdad es el panel.
    """
    destino = settings.COBROS_AVISO_EMAIL or settings.DEFAULT_FROM_EMAIL
    if not destino:
        return
    cuerpo = render_to_string(f"suscripcion/email/{base_template}.txt", contexto)
    # `EmailMessage` en vez de `send_mail` sólo para poder poner `reply_to`,
    # mismo criterio que `solicitudes/emails.py`: el From es una casilla
    # no-reply. Sin `EMAIL_REPLY_TO` el mail sale sin la cabecera.
    reply_to = [settings.EMAIL_REPLY_TO] if getattr(settings, "EMAIL_REPLY_TO", "") else None
    EmailMessage(
        subject=asunto,
        body=cuerpo,
        from_email=settings.DEFAULT_FROM_EMAIL or None,
        to=[destino],
        reply_to=reply_to,
    ).send(fail_silently=True)


def enviar_cobro_acreditado(intento, *, descongelado, sigue_debiendo_desde, url_panel):
    """Se acreditó un pago online. Dice si la cuenta se descongeló y, si el
    pago no alcanzó para ponerla al día, desde qué fecha sigue debiendo."""
    _enviar(
        f"Cobro acreditado: {intento.gimnasio.nombre}",
        "cobro_acreditado",
        {
            "intento": intento,
            "descongelado": descongelado,
            "sigue_debiendo_desde": sigue_debiendo_desde,
            "url_panel": url_panel,
        },
    )


def enviar_cobro_con_problema(intento, *, problema, url_panel, pago_adicional_id=""):
    """Un pago que necesita que lo mire un humano. `problema` es una clave de
    `PROBLEMAS`. `pago_adicional_id` es el id del pago NUEVO en el caso
    «pago_adicional» (el del intento es el que ya se acreditó, y el que hay
    que devolver es el otro)."""
    titulo, que_hacer = PROBLEMAS[problema]
    _enviar(
        f"Cobro online — {titulo}: {intento.gimnasio.nombre}",
        "cobro_con_problema",
        {
            "intento": intento,
            "titulo": titulo,
            "que_hacer": que_hacer,
            "problema": problema,
            "pago_adicional_id": pago_adicional_id,
            "url_panel": url_panel,
        },
    )
