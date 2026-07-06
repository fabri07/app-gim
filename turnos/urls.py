"""URLs de turnos/reservas.

Task 4: configuración de staff. Task 5: grilla y reservas del alumno
(`mis_turnos`/`reservar`/`cancelar`). Task 6: agenda de staff (`agenda`) --
cierra la Feature A."""

from django.urls import path

from turnos.views import (
    AgendaView,
    CancelarReservaView,
    ConfiguracionTurnosView,
    CupoExcepcionCreateView,
    CupoExcepcionEliminarView,
    HorarioAtencionCreateView,
    HorarioAtencionEliminarView,
    MisTurnosView,
    ReservarView,
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
    path("mis-turnos/", MisTurnosView.as_view(), name="mis_turnos"),
    path("reservar/", ReservarView.as_view(), name="reservar"),
    path(
        "reservas/<int:pk>/cancelar/",
        CancelarReservaView.as_view(),
        name="cancelar",
    ),
    path("agenda/", AgendaView.as_view(), name="agenda"),
]
