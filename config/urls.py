"""Routing raíz: admin + autenticación/tenancy. Fase 1 agrega el include del
dominio (alumnos, rutinas, pagos, novedades)."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("tenants.urls")),
]
