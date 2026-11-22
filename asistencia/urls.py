from django.urls import path

from asistencia.views import (
    MarcarAsistenciaReservaView,
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
    path("<int:pk>/quitar/", QuitarAsistenciaView.as_view(), name="quitar"),
]
