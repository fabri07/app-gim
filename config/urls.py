"""Routing raíz: admin + autenticación/tenancy + vistas de dominio (Fase 2)."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("tenants.urls")),
    path("", include("notificaciones.urls")),
    path("alumnos/", include("alumnos.urls")),
    path("ejercicios/", include("ejercicios.urls")),
    path("rutinas/", include("rutinas.urls")),
    path("pagos/", include("pagos.urls")),
    path("novedades/", include("novedades.urls")),
    path("turnos/", include("turnos.urls")),
    path("calendario/", include("calendario.urls")),
    path("importaciones/", include("importaciones.urls")),
]

if settings.DEBUG:
    # Solo en dev: en producción el media va a R2, no lo sirve Django.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
