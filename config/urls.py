"""Routing raíz: admin + autenticación/tenancy. Fase 2 agrega las vistas del
dominio (alumnos, rutinas, pagos, novedades) — Fase 1 solo define los
modelos."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("tenants.urls")),
]

if settings.DEBUG:
    # Solo en dev: en producción el media va a R2, no lo sirve Django.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
