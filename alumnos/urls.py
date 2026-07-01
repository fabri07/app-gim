"""URLs de gestión de alumnos (Fase 2).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from alumnos.views import (
    AlumnoCreateView,
    AlumnoDetailView,
    AlumnoListView,
    AlumnoToggleEstadoView,
    AlumnoUpdateView,
)

app_name = "alumnos"

urlpatterns = [
    path("", AlumnoListView.as_view(), name="listado"),
    path("nuevo/", AlumnoCreateView.as_view(), name="crear"),
    path("<int:pk>/", AlumnoDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", AlumnoUpdateView.as_view(), name="editar"),
    path("<int:pk>/activar/", AlumnoToggleEstadoView.as_view(), name="activar"),
]
