from django.urls import path

from tienda import views, views_mercadopago

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
    path("mercadopago/conectar/", views_mercadopago.ConectarView.as_view(), name="mp_conectar"),
    # El valor de MERCADOPAGO_OAUTH_REDIRECT_URI es la URL absoluta de esta ruta.
    path("mercadopago/callback/", views_mercadopago.CallbackView.as_view(), name="mp_callback"),
    path("mercadopago/desconectar/", views_mercadopago.DesconectarView.as_view(), name="mp_desconectar"),
    # Webhook de los pagos (nunca lee `request.user`; lo completa B5).
    path("v1/webhook/mercadopago/<uuid:uuid_webhook>/", views_mercadopago.webhook, name="mp_webhook"),
    # Alumno
    path("", views.CatalogoView.as_view(), name="catalogo"),
    path("productos/<int:pk>/denunciar/", views.DenunciarView.as_view(), name="denunciar"),
    path("carrito/", views.CarritoView.as_view(), name="carrito"),
    path("carrito/confirmar/", views.CheckoutView.as_view(), name="checkout"),
    path("mis-pedidos/", views.MisPedidosView.as_view(), name="mis_pedidos"),
    path("mis-pedidos/<int:pk>/", views.MiPedidoView.as_view(), name="mi_pedido"),
    path("mis-pedidos/<int:pk>/comprobante/", views.MiPedidoComprobanteView.as_view(), name="mi_pedido_comprobante"),
    path("mis-pedidos/<int:pk>/cancelar/", views.MiPedidoCancelarView.as_view(), name="mi_pedido_cancelar"),
    path("mis-pedidos/<int:pk>/pedir-cancelacion/", views.MiPedidoPedirCancelacionView.as_view(), name="mi_pedido_pedir_cancelacion"),
    # Adonde vuelve el navegador desde Mercado Pago (alumno dueño del pedido).
    path("mercadopago/retorno/<uuid:referencia>/", views.RetornoMercadoPagoView.as_view(), name="mp_retorno"),
    # Foto pública (nombre único: `URLS_SIN_BLOQUEO` compara sin namespace)
    path(
        "productos/<int:pk>/fotos/<int:foto_pk>/",
        views.FotoProductoView.as_view(),
        name="tienda_foto_producto",
    ),
]
