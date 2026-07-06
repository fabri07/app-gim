"""URLs de turnos/reservas.

Task 4: configuración de staff (esta tarea). Las rutas de grilla/reserva del
alumno (`mis_turnos`/`reservar`/`cancelar`) y agenda de staff (`agenda`) se
agregan en las Tasks 5/6 -- no crearlas acá todavía."""

from django.urls import path

from turnos.views import (
    ConfiguracionTurnosView,
    CupoExcepcionCreateView,
    CupoExcepcionEliminarView,
    HorarioAtencionCreateView,
    HorarioAtencionEliminarView,
)

app_name = "turnos"

urlpatterns = [
    path("configuracion/", ConfiguracionTurnosView.as_view(), name="configuracion"),
    path(
        "configuracion/horarios/nuevo/",
        HorarioAtencionCreateView.as_view(),
        name="horario_crear",
    ),
    path(
        "configuracion/horarios/<int:pk>/eliminar/",
        HorarioAtencionEliminarView.as_view(),
        name="horario_eliminar",
    ),
    path(
        "configuracion/cupos/nuevo/",
        CupoExcepcionCreateView.as_view(),
        name="cupo_crear",
    ),
    path(
        "configuracion/cupos/<int:pk>/eliminar/",
        CupoExcepcionEliminarView.as_view(),
        name="cupo_eliminar",
    ),
]
