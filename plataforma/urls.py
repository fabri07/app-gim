"""Rutas del panel de plataforma (namespace `plataforma:`)."""

from django.urls import path

from plataforma.views import (
    EstadoCuentaView,
    ExportacionToggleView,
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
    # El estado va en la URL y no en el POST: así cada botón de la ficha tiene
    # su propia dirección (y su propia pantalla de confirmación) en vez de un
    # formulario con un campo oculto que decide cuánto se corta.
    path(
        "gimnasios/<int:pk>/estado/<str:estado>/",
        EstadoCuentaView.as_view(),
        name="estado_cuenta",
    ),
    path(
        "gimnasios/<int:pk>/exportacion/",
        ExportacionToggleView.as_view(),
        name="exportacion_toggle",
    ),
]
