from django.urls import path

from asistencia.views import MarcarAsistenciaReservaView

app_name = "asistencia"

urlpatterns = [
    path(
        "reservas/<int:pk>/",
        MarcarAsistenciaReservaView.as_view(),
        name="marcar_reserva",
    ),
]
