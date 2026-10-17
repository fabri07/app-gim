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
    path("pedidos/", views.PedidoListView.as_view(), name="pedidos"),
    path("pedidos/<int:pk>/", views.PedidoDetailView.as_view(), name="pedido_detalle"),
    path("pedidos/<int:pk>/accion/", views.PedidoAccionView.as_view(), name="pedido_accion"),
    path("configuracion/", views.ConfiguracionView.as_view(), name="configuracion"),
    # Alumno
    path("", views.CatalogoView.as_view(), name="catalogo"),
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("carrito/confirmar/", views.CheckoutView.as_view(), name="checkout"),
    path("mis-pedidos/", views.MisPedidosView.as_view(), name="mis_pedidos"),
    path("mis-pedidos/<int:pk>/", views.MiPedidoView.as_view(), name="mi_pedido"),
    path("mis-pedidos/<int:pk>/comprobante/", views.MiPedidoComprobanteView.as_view(), name="mi_pedido_comprobante"),
    path("mis-pedidos/<int:pk>/cancelar/", views.MiPedidoCancelarView.as_view(), name="mi_pedido_cancelar"),
    path("mis-pedidos/<int:pk>/pedir-cancelacion/", views.MiPedidoPedirCancelacionView.as_view(), name="mi_pedido_pedir_cancelacion"),
    # Foto pública (nombre único: `URLS_SIN_BLOQUEO` compara sin namespace)
    path("productos/<int:pk>/foto/", views.FotoProductoView.as_view(), name="tienda_foto_producto"),
]
