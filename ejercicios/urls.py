"""URLs de gestión de la biblioteca de ejercicios (Fase 2).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from ejercicios.views import (
    CategoriaCreateView,
    CategoriaListView,
    CategoriaUpdateView,
    EjercicioCreateView,
    EjercicioListView,
    EjercicioUpdateView,
)

app_name = "ejercicios"

urlpatterns = [
    path("", EjercicioListView.as_view(), name="listado"),
    path("nuevo/", EjercicioCreateView.as_view(), name="crear"),
    path("<int:pk>/editar/", EjercicioUpdateView.as_view(), name="editar"),
    path("categorias/", CategoriaListView.as_view(), name="categorias_listado"),
    path("categorias/nueva/", CategoriaCreateView.as_view(), name="categorias_crear"),
    path(
        "categorias/<int:pk>/editar/",
        CategoriaUpdateView.as_view(),
        name="categorias_editar",
    ),
]
