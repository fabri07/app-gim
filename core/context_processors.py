from django.conf import settings


def modo_oscuro(request):
    """Expone la bandera `MODO_OSCURO` a las plantillas (`base.html`)."""
    return {"modo_oscuro": settings.MODO_OSCURO}
