from django.conf import settings


def google_staff_login_disponible(request):
    return {"GOOGLE_STAFF_LOGIN_DISPONIBLE": settings.GOOGLE_STAFF_LOGIN_ENABLED}
