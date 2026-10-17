from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from PIL import Image as _Image

from core.forms import TenantScopedModelForm
from pagos.models import EXTENSIONES_COMPROBANTE_PERMITIDAS
from tienda.fotos import preparar_foto
from tienda.models import MAX_LARGO_MOTIVO, CategoriaProducto, ConfiguracionTienda, Producto, Variante


class CategoriaProductoForm(TenantScopedModelForm):
    """Valida a mano el nombre único por gimnasio: `gimnasio` no es campo del
    form, así que el `UniqueConstraint` explotaría como `IntegrityError` (500).
    Mismo patrón que `ejercicios/forms.py` (`clean_nombre` de la categoría)."""

    class Meta:
        model = CategoriaProducto
        fields = ["nombre", "orden"]

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        chocan = CategoriaProducto.objects.for_gimnasio(self.gimnasio).filter(nombre__iexact=nombre)
        if self.instance.pk:
            chocan = chocan.exclude(pk=self.instance.pk)
        if chocan.exists():
            raise forms.ValidationError("Ya hay una categoría con ese nombre.")
        return nombre


class ProductoForm(TenantScopedModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "categoria", "descripcion", "precio", "foto", "publicado", "orden"]
        widgets = {"foto": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"})}

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "content_type"):  # recién subida, no la guardada
            return preparar_foto(foto)
        return foto


class VarianteForm(forms.ModelForm):
    """`stock` NO es un campo del form: escribirlo en forma absoluta pisaría
    las reservas que entren mientras la pantalla está abierta. En el alta se
    carga como `stock_inicial`; después sólo con «ajuste ±»."""

    stock_inicial = forms.IntegerField(
        min_value=0, max_value=100000, required=False, label="Stock inicial"
    )

    class Meta:
        model = Variante
        fields = ["nombre", "activa"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["stock_inicial"]

    def save(self, commit=True):
        variante = super().save(commit=False)
        if variante.pk is None:
            variante.stock = self.cleaned_data.get("stock_inicial") or 0
            if commit:
                variante.save()
        elif commit:
            variante.save(update_fields=["nombre", "activa"])
        return variante


class BaseVarianteFormSet(BaseInlineFormSet):
    def clean(self):
        # Primero las reglas propias: el `validate_unique` de Django (que corre
        # en `super().clean()`) contaría como duplicados los dos extras vacíos
        # y tapa nuestros mensajes con «información duplicada en nombre».
        if any(self.errors):
            return
        vivas = [
            f for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
            and (f.instance.pk or f.cleaned_data.get("nombre") or f.cleaned_data.get("stock_inicial") is not None)
        ]
        if not vivas:
            raise forms.ValidationError("El producto tiene que tener al menos una variante.")
        nombres = [f.cleaned_data.get("nombre", "").strip().lower() for f in vivas]
        if len(nombres) != len(set(nombres)):
            raise forms.ValidationError("Hay dos variantes con el mismo nombre.")
        super().clean()

    def _should_delete_form(self, form):
        # Una variante con ventas no se borra (ItemPedido la protege): se
        # desactiva. El template no muestra la casilla en ese caso; esto es el
        # guard por si se postea a mano.
        borrar = super()._should_delete_form(form)
        if borrar and form.instance.pk and form.instance.items.exists():
            form.instance.activa = False
            return False
        return borrar


VarianteFormSet = inlineformset_factory(
    Producto, Variante, form=VarianteForm, formset=BaseVarianteFormSet,
    extra=2, min_num=0, max_num=50, can_delete=True,
)


class AjusteStockForm(forms.Form):
    delta = forms.IntegerField(
        min_value=-100000, max_value=100000,
        label="Sumar o restar unidades",
        help_text="Ej.: 10 si llegaron 10; -1 si se rompió una.",
    )


class ConfiguracionTiendaForm(TenantScopedModelForm):
    class Meta:
        model = ConfiguracionTienda
        fields = ["publicada", "horas_reserva_transferencia", "max_pedidos_activos", "instrucciones_retiro"]


class MotivoForm(forms.Form):
    motivo = forms.CharField(max_length=MAX_LARGO_MOTIVO, required=False, label="Motivo")


TAMANIO_MAXIMO_COMPROBANTE = 5 * 1024 * 1024


class ComprobanteForm(forms.Form):
    comprobante = forms.FileField(
        label="Comprobante de la transferencia",
        help_text="Una captura o foto del comprobante (JPG o PNG, hasta 5 MB).",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

    def clean_comprobante(self):
        archivo = self.cleaned_data["comprobante"]
        extension = archivo.name.rsplit(".", 1)[-1].lower() if "." in archivo.name else ""
        if extension not in EXTENSIONES_COMPROBANTE_PERMITIDAS:
            raise forms.ValidationError("Subí una imagen JPG o PNG.")
        if archivo.size > TAMANIO_MAXIMO_COMPROBANTE:
            raise forms.ValidationError("El archivo pesa más de 5 MB.")
        try:
            imagen = _Image.open(archivo)
            formato = imagen.format
            imagen.verify()
        except Exception:
            raise forms.ValidationError("El archivo no es una imagen válida.")
        # Un GIF/WebP renombrado .png pasa `verify()`: el formato real manda.
        if formato not in ("JPEG", "PNG"):
            raise forms.ValidationError("Subí una imagen JPG o PNG.")
        archivo.seek(0)
        return archivo
