from django.conf import settings


def google_staff_login_disponible(request):
    return {"GOOGLE_STAFF_LOGIN_DISPONIBLE": settings.GOOGLE_STAFF_LOGIN_ENABLED}


def password_reset_disponible(request):
    return {"PASSWORD_RESET_DISPONIBLE": settings.PASSWORD_RESET_ENABLED}
