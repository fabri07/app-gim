"""URLs del importador (Proyecto 2). No se incluye acá en `config/urls.py`
-- eso queda para Task 12, mismo criterio que documenta `rutinas/urls.py`."""

from django.urls import path

from importaciones.views import (
    DescartarImportacionView,
    EjemploPlantillasView,
    PreviewBibliotecaView,
    PreviewPlantillasView,
    SeleccionHojasView,
    SubirBibliotecaView,
    SubirPlantillasView,
)

app_name = "importaciones"

urlpatterns = [
    path("plantillas/ejemplo.xlsx", EjemploPlantillasView.as_view(), name="plantillas_ejemplo"),
    path("plantillas/subir/", SubirPlantillasView.as_view(), name="plantillas_subir"),
    path("plantillas/<int:pk>/hojas/", SeleccionHojasView.as_view(), name="plantillas_hojas"),
    path("plantillas/<int:pk>/preview/", PreviewPlantillasView.as_view(), name="plantillas_preview"),
    path("plantillas/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="plantillas_descartar"),
    path("biblioteca/subir/", SubirBibliotecaView.as_view(), name="biblioteca_subir"),
    path("biblioteca/<int:pk>/preview/", PreviewBibliotecaView.as_view(), name="biblioteca_preview"),
    path("biblioteca/<int:pk>/descartar/", DescartarImportacionView.as_view(), name="biblioteca_descartar"),
]
