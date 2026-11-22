from django.urls import path

from asistencia.views import (
    MarcarAsistenciaReservaView,
    PasarListaView,
    QuitarAsistenciaView,
    RegistrarAsistenciaView,
)

app_name = "asistencia"

urlpatterns = [
    path(
        "reservas/<int:pk>/",
        MarcarAsistenciaReservaView.as_view(),
        name="marcar_reserva",
    ),
    path(
        "alumnos/<int:pk>/registrar/",
        RegistrarAsistenciaView.as_view(),
        name="registrar",
    ),
    path("pasar-lista/", PasarListaView.as_view(), name="pasar_lista"),
    path("<int:pk>/quitar/", QuitarAsistenciaView.as_view(), name="quitar"),
]
