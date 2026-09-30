from django.conf import settings


def cobro_online(request):
    """¿Se ofrece pagar la suscripción desde la app?

    Lo leen el banner de atraso de `base.html` y el cartel de la cuenta
    congelada para decidir si muestran «Pagar ahora». Cero queries: es una
    bandera de settings (molde: `tenants.context_processors.
    google_staff_login_disponible`), y corre en cada página del sitio.
    """
    return {"COBRO_ONLINE_DISPONIBLE": settings.COBRO_ONLINE_ENABLED}
