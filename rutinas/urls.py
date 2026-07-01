"""URLs de gestión de rutinas (Fase 2): plantillas, sus items, duplicar y
asignación a un alumno.

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from rutinas.views import (
    AsignarRutinaView,
    RutinaAsignadaDetailView,
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
]
