"""Rutas del panel de plataforma (namespace `plataforma:`)."""

from django.urls import path

from plataforma.views import (
    FacturacionUpdateView,
    GimnasioDetalleView,
    InicioView,
    PagoPlataformaCreateView,
)

app_name = "plataforma"

urlpatterns = [
    path("", InicioView.as_view(), name="inicio"),
    path(
        "gimnasios/<int:pk>/",
        GimnasioDetalleView.as_view(),
        name="gimnasio_detalle",
    ),
    # El pk de la URL es el gimnasio, no el pago: es de donde sale la tenencia
    # de la fila que se va a crear.
    path(
        "gimnasios/<int:pk>/pagos/nuevo/",
        PagoPlataformaCreateView.as_view(),
        name="pago_nuevo",
    ),
    path(
        "gimnasios/<int:pk>/facturacion/",
        FacturacionUpdateView.as_view(),
        name="facturacion_editar",
    ),
]
