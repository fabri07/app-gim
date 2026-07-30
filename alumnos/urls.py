"""URLs de gestión de alumnos (Fase 2).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from alumnos.views import (
    AccesoListView,
    AlumnoCreateView,
    AlumnoDetailView,
    AlumnoListView,
    AlumnoToggleEstadoView,
    AlumnoUpdateView,
    CrearAccesoView,
    RegenerarPasswordView,
)

app_name = "alumnos"

urlpatterns = [
    path("", AlumnoListView.as_view(), name="listado"),
    path("nuevo/", AlumnoCreateView.as_view(), name="crear"),
    # Las rutas literales van antes de las que capturan un parámetro. Con
    # `<int:pk>` no hay conflicto real ("accesos" no es un entero), pero el
    # orden evita el problema si algún día se agrega un `<slug:...>`.
    path("accesos/", AccesoListView.as_view(), name="accesos"),
    path("<int:pk>/", AlumnoDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", AlumnoUpdateView.as_view(), name="editar"),
    path("<int:pk>/activar/", AlumnoToggleEstadoView.as_view(), name="activar"),
    path("<int:pk>/acceso/crear/", CrearAccesoView.as_view(), name="acceso_crear"),
    path(
        "<int:pk>/acceso/regenerar/",
        RegenerarPasswordView.as_view(),
        name="acceso_regenerar",
    ),
]
