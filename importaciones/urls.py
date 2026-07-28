"""URLs del importador (Proyecto 2). No se incluye acá en `config/urls.py`
-- eso queda para Task 12, mismo criterio que documenta `rutinas/urls.py`."""

from django.urls import path

from importaciones.views import (
    DescartarImportacionView,
    PreviewPlantillasView,
    SubirPlantillasView,
)

app_name = "importaciones"

urlpatterns = [
    path("plantillas/subir/", SubirPlantillasView.as_view(), name="plantillas_subir"),
    path("plantillas/<int:pk>/preview/", PreviewPlantillasView.as_view(), name="plantillas_preview"),
    path("plantillas/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="plantillas_descartar"),
]
