from django.urls import path

from tienda import views

app_name = "tienda"

urlpatterns = [
    # Staff
    path("productos/", views.ProductoListView.as_view(), name="productos"),
    path("productos/nuevo/", views.ProductoCreateView.as_view(), name="producto_crear"),
    path("productos/<int:pk>/editar/", views.ProductoUpdateView.as_view(), name="producto_editar"),
    path("productos/<int:pk>/eliminar/", views.ProductoBorrarView.as_view(), name="producto_borrar"),
    path("variantes/<int:pk>/ajustar/", views.VarianteAjustarView.as_view(), name="variante_ajustar"),
    path("categorias/", views.CategoriaListView.as_view(), name="categorias"),
    path("categorias/nueva/", views.CategoriaCreateView.as_view(), name="categoria_crear"),
    path("categorias/<int:pk>/editar/", views.CategoriaUpdateView.as_view(), name="categoria_editar"),
    path("categorias/<int:pk>/eliminar/", views.CategoriaBorrarView.as_view(), name="categoria_borrar"),
    # Foto pública (nombre único: `URLS_SIN_BLOQUEO` compara sin namespace)
    path("productos/<int:pk>/foto/", views.FotoProductoView.as_view(), name="tienda_foto_producto"),
]
