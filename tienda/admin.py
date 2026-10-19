from django import forms
from django.contrib import admin
from django.forms import BaseInlineFormSet

from tienda.codigos import normalizar, validar_codigo
from tienda.models import (
    CategoriaProducto,
    ConfiguracionTienda,
    CuentaMercadoPago,
    ItemPedido,
    Pedido,
    Producto,
    Variante,
)
from tienda.variantes import clave, componer_nombre


class VarianteAdminForm(forms.ModelForm):
    """Como el del staff: el nombre se calcula SIEMPRE de los valores (sin
    valores `Variante.save()` no lo toca, y vaciar los dos dejaría el «M»
    viejo) y el código pasa por el mismo validador. El largo del nombre lo
    valida `Variante.clean()`."""

    class Meta:
        model = Variante
        fields = ["valor_1", "valor_2", "codigo", "activa"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Lo que hay en la base, antes de que `_post_clean` copie lo posteado.
        self._valores_guardados = (self.instance.valor_1, self.instance.valor_2)
        # Creada con nombre libre y sin valores (código viejo): se precarga,
        # así guardar el producto desde acá no la deja en «».
        if self.instance.pk and not self.instance.valor_1 and self.instance.nombre:
            self.initial["valor_1"] = self.instance.nombre

    def clean_codigo(self):
        codigo = normalizar(self.cleaned_data.get("codigo"))
        validar_codigo(codigo, pk=self.instance.pk)
        return codigo

    def has_changed(self):
        # Con la precarga, «S» posteado es igual al `initial` y Django no
        # guardaría la fila: se compara contra lo que hay en la base.
        if super().has_changed():
            return True
        datos = getattr(self, "cleaned_data", None)
        if not (self.instance.pk and datos):
            return False
        return (datos.get("valor_1", ""), datos.get("valor_2", "")) != self._valores_guardados

    def save(self, commit=True):
        variante = super().save(commit=False)
        variante.nombre = componer_nombre(variante.valor_1, variante.valor_2)
        if commit:
            variante.save()
        return variante


class VarianteAdminFormSet(BaseInlineFormSet):
    """La base ya no tiene constraint de nombre único (ver `tienda/0008`):
    los nombres calculados se comparan acá, entre las filas ACTIVAS del
    inline (que muestra todas las variantes del producto). Saltea las que se
    borran y las que tienen errores; una desactivada no choca (el form del
    staff permite una «» activa y otra desactivada)."""

    def clean(self):
        super().clean()
        vistos = set()
        for form in self.forms:
            if not getattr(form, "cleaned_data", None) or form.errors:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            if form.instance.pk is None and not form.has_changed():
                continue
            if not form.cleaned_data.get("activa"):
                continue
            nombre = componer_nombre(form.cleaned_data.get("valor_1"), form.cleaned_data.get("valor_2"))
            if clave(nombre) in vistos:
                raise forms.ValidationError(f"Hay dos variantes «{nombre}».")
            vistos.add(clave(nombre))


class VarianteInline(admin.TabularInline):
    model = Variante
    form = VarianteAdminForm
    formset = VarianteAdminFormSet
    extra = 0
    fields = ("valor_1", "valor_2", "codigo", "nombre", "stock", "activa")
    # Stock disponible: escribirlo en absoluto pisa reservas (ver models.py).
    # `nombre` se calcula de los valores. `codigo` se normaliza en
    # `Variante.save()` (también desde acá); la unicidad por gimnasio NO se
    # chequea en el admin (aceptado: lo usa sólo el superadmin).
    readonly_fields = ("nombre", "stock")


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
