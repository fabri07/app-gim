"""Rutas del panel de plataforma (namespace `plataforma:`)."""

from django.urls import path

from plataforma.views import (
    EstadoCuentaView,
    ExportacionToggleView,
    FacturacionUpdateView,
    GimnasioCrearView,
    GimnasioDetalleView,
    InicioView,
    PagoPlataformaCreateView,
    SolicitudAprobarView,
    SolicitudDetalleView,
    SolicitudListaEsperaView,
    SolicitudListaView,
    SolicitudRechazarView,
    SolicitudReenviarInvitacionView,
)

app_name = "plataforma"

urlpatterns = [
    path("", InicioView.as_view(), name="inicio"),
    # Antes que `gimnasios/<int:pk>/`: `nuevo` no es un entero, así que no
    # podrían pisarse, pero el alta va arriba porque es lo único de esta
    # sección que no cuelga de un gimnasio existente.
    path("gimnasios/nuevo/", GimnasioCrearView.as_view(), name="gimnasio_crear"),
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
    # Cola de solicitudes de acceso (leads). La lista va antes que el detalle
    # por el mismo criterio de "ruta más específica primero" del resto.
    path("solicitudes/", SolicitudListaView.as_view(), name="solicitud_lista"),
    path(
        "solicitudes/<int:pk>/",
        SolicitudDetalleView.as_view(),
        name="solicitud_detalle",
    ),
    path(
        "solicitudes/<int:pk>/aprobar/",
        SolicitudAprobarView.as_view(),
        name="solicitud_aprobar",
    ),
    path(
        "solicitudes/<int:pk>/rechazar/",
        SolicitudRechazarView.as_view(),
        name="solicitud_rechazar",
    ),
    path(
        "solicitudes/<int:pk>/lista-espera/",
        SolicitudListaEsperaView.as_view(),
        name="solicitud_lista_espera",
    ),
    path(
        "solicitudes/<int:pk>/reenviar/",
        SolicitudReenviarInvitacionView.as_view(),
        name="solicitud_reenviar",
    ),
]
