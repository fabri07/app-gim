from django.conf import settings


def vapid_public_key(request):
    return {"VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY}
