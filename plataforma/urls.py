"""Rutas del panel de plataforma (namespace `plataforma:`)."""

from django.urls import path

from plataforma.views import GimnasioDetalleView, InicioView

app_name = "plataforma"

urlpatterns = [
    path("", InicioView.as_view(), name="inicio"),
    path(
        "gimnasios/<int:pk>/",
        GimnasioDetalleView.as_view(),
        name="gimnasio_detalle",
    ),
]
