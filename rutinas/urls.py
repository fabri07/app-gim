"""URLs de gestión de rutinas (Fase 2): plantillas, sus items, duplicar y
asignación a un alumno.

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from rutinas.views import (
    AsignarRutinaView,
    RutinaAsignadaDetailView,
    RutinaAsignadaDiaCompletadoToggleView,
    RutinaAsignadaItemCalificarView,
    RutinaMiDiaDetailView,
    RutinaPlantillaCreateView,
    RutinaPlantillaDetailView,
    RutinaPlantillaDuplicarView,
    RutinaPlantillaItemCreateView,
    RutinaPlantillaItemDeleteView,
    RutinaPlantillaItemUpdateView,
    RutinaPlantillaListView,
    RutinaPlantillaUpdateView,
)

app_name = "rutinas"

urlpatterns = [
    path("", RutinaPlantillaListView.as_view(), name="plantilla_listado"),
    path("nueva/", RutinaPlantillaCreateView.as_view(), name="plantilla_crear"),
    path("<int:pk>/", RutinaPlantillaDetailView.as_view(), name="plantilla_detalle"),
    path(
        "<int:pk>/editar/",
        RutinaPlantillaUpdateView.as_view(),
        name="plantilla_editar",
    ),
    path(
        "<int:pk>/duplicar/",
        RutinaPlantillaDuplicarView.as_view(),
        name="plantilla_duplicar",
    ),
    path(
        "<int:plantilla_pk>/items/nuevo/",
        RutinaPlantillaItemCreateView.as_view(),
        name="item_crear",
    ),
    path(
        "<int:plantilla_pk>/items/<int:pk>/editar/",
        RutinaPlantillaItemUpdateView.as_view(),
        name="item_editar",
    ),
    path(
        "<int:plantilla_pk>/items/<int:pk>/eliminar/",
        RutinaPlantillaItemDeleteView.as_view(),
        name="item_eliminar",
    ),
    path("asignar/", AsignarRutinaView.as_view(), name="asignar"),
    path(
        "asignadas/<int:pk>/",
        RutinaAsignadaDetailView.as_view(),
        name="asignada_detalle",
    ),
    path(
        "asignadas/items/<int:pk>/calificar/",
        RutinaAsignadaItemCalificarView.as_view(),
        name="item_calificar",
    ),
    path(
        "mi-rutina/dia/<int:dia>/",
        RutinaMiDiaDetailView.as_view(),
        name="mi_dia_detalle",
    ),
    path(
        "mi-rutina/dia/<int:dia>/semana/<int:semana>/completar/",
        RutinaAsignadaDiaCompletadoToggleView.as_view(),
        name="dia_completado_toggle",
    ),
]
