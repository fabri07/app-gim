"""URLs de gestión de novedades (Fase 2).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from novedades.views import (
    NovedadCreateView,
    NovedadListView,
    NovedadOcultarView,
    NovedadUpdateView,
)

app_name = "novedades"

urlpatterns = [
    path("", NovedadListView.as_view(), name="listado"),
    path("nueva/", NovedadCreateView.as_view(), name="crear"),
    path("<int:pk>/editar/", NovedadUpdateView.as_view(), name="editar"),
    path("<int:pk>/ocultar/", NovedadOcultarView.as_view(), name="ocultar"),
]
