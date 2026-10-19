from django.contrib import admin

from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    CuentaMercadoPago,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
)


class VarianteInline(admin.TabularInline):
    model = Variante
    extra = 0
    fields = ("nombre", "codigo", "stock", "activa")
    # Stock disponible: escribirlo en absoluto pisa reservas (ver models.py).
    # `codigo` se normaliza en `Variante.save()` (también desde acá); la
    # unicidad por gimnasio NO se chequea en el admin (aceptado: lo usa sólo
    # el superadmin).
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
    readonly_fields = (
        "estado", "total", "referencia_externa", "preference_id", "init_point",
        "payment_id", "estado_mp", "respuesta_mp", "revision_mp_pendiente",
    )
    raw_id_fields = ("alumno",)  # un select con los alumnos de TODA la plataforma no
    inlines = [ItemPedidoInline]


@admin.register(CategoriaProducto)
class CategoriaProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "orden")
    list_filter = ("gimnasio",)


@admin.register(ConfiguracionTienda)
class ConfiguracionTiendaAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "publicada", "horas_reserva_transferencia")


@admin.register(CuentaMercadoPago)
class CuentaMercadoPagoAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "estado", "es_de_prueba", "conectada_en")
    list_filter = ("estado", "es_de_prueba")
    # El admin mostraría los tokens DESCIFRADOS: fuera del formulario. Y como
    # no están en el form, el `save` no los reescribe con un valor ilegible.
    exclude = ("access_token", "refresh_token")
    readonly_fields = (
        "gimnasio", "mp_user_id", "token_expira", "uuid_webhook", "es_de_prueba",
        "estado", "conectada_por", "conectada_en", "desconectada_en",
    )

    def has_add_permission(self, request):
        return False  # se conecta por OAuth desde la configuración de la tienda

    def save_model(self, request, obj, form, change):
        # Un token ilegible (clave rotada) quedó en `None` al cargar: un
        # `save()` completo lo pisaría. Sólo las columnas que el form tiene.
        obj.save(update_fields=[f for f in form.changed_data if f not in self.exclude])
