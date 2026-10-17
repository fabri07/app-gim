from django.contrib import admin

from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
)


class VarianteInline(admin.TabularInline):
    model = Variante
    extra = 0
    # Stock disponible: escribirlo en absoluto pisa reservas (ver models.py).
    readonly_fields = ("stock",)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "precio", "publicado")
    list_filter = ("gimnasio", "publicado")
    inlines = [VarianteInline]


class ItemPedidoInline(admin.TabularInline):
    model = ItemPedido
    extra = 0
    readonly_fields = ("variante", "nombre", "precio_unitario", "cantidad")


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "gimnasio", "alumno", "estado", "total", "creado")
    list_filter = ("gimnasio", "estado")
    # `estado` y `total` de sólo lectura: las transiciones van por `services`
    # (UPDATE condicional + stock); editarlas acá desarma el stock reservado.
    readonly_fields = ("estado", "total")
    raw_id_fields = ("alumno",)  # un select con los alumnos de TODA la plataforma no
    inlines = [ItemPedidoInline]


@admin.register(CategoriaProducto)
class CategoriaProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "orden")
    list_filter = ("gimnasio",)


@admin.register(ConfiguracionTienda)
class ConfiguracionTiendaAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "publicada", "horas_reserva_transferencia")
