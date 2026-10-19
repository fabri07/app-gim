from django import forms
from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db.models import Max
from django.forms import BaseInlineFormSet, inlineformset_factory
from PIL import Image as _Image

from core.forms import TenantScopedModelForm
from pagos.models import EXTENSIONES_COMPROBANTE_PERMITIDAS
from tienda.codigos import normalizar, validar_codigo
from tienda.fotos import preparar_foto
from tienda.models import (
    MAX_FOTOS_POR_PRODUCTO,
    MAX_LARGO_MOTIVO,
    CategoriaProducto,
    ConfiguracionTienda,
    CuentaMercadoPago,
    FotoProducto,
    Producto,
    Variante,
)


class OrdenOpcionalMixin:
    """«Orden» sólo decide la posición en el catálogo y en la base ya vale 0
    por defecto, pero el ModelForm hereda `required` del
    `PositiveSmallIntegerField` sin `blank` y lo marcaba obligatorio (ver
    ISSUES.md, 2026-10-18). Vacío o ausente es 0."""

    def _hacer_orden_opcional(self):
        campo = self.fields["orden"]
        campo.required = False
        campo.label = "Orden en el catálogo"
        campo.help_text = (
            "Opcional. Los de número más bajo se muestran primero; vacío es 0, "
            "y entre iguales van por orden alfabético."
        )

    def clean_orden(self):
        orden = self.cleaned_data.get("orden")
        return 0 if orden is None else orden


class CategoriaProductoForm(OrdenOpcionalMixin, TenantScopedModelForm):
    """Valida a mano el nombre único por gimnasio: `gimnasio` no es campo del
    form, así que el `UniqueConstraint` explotaría como `IntegrityError` (500).
    Mismo patrón que `ejercicios/forms.py` (`clean_nombre` de la categoría)."""

    class Meta:
        model = CategoriaProducto
        fields = ["nombre", "orden"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hacer_orden_opcional()

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        chocan = CategoriaProducto.objects.for_gimnasio(self.gimnasio).filter(nombre__iexact=nombre)
        if self.instance.pk:
            chocan = chocan.exclude(pk=self.instance.pk)
        if chocan.exists():
            raise forms.ValidationError("Ya hay una categoría con ese nombre.")
        return nombre


class MultipleFileInput(forms.ClearableFileInput):
    """Receta de la documentación de Django 5.2 para elegir varios archivos."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        limpiar_uno = super().clean
        if isinstance(data, (list, tuple)):
            return [limpiar_uno(d, initial) for d in data]
        return [limpiar_uno(data, initial)]


class ProductoForm(OrdenOpcionalMixin, TenantScopedModelForm):
    """La galería va en el mismo form (sin un segundo formset): tres campos
    que no son del modelo, que `save()` ignora y guarda `guardar_fotos()`.
    `borrar_fotos` y `principal` se acotan en `__init__` a las fotos de ESTE
    producto: una foto de otro producto no es opción válida."""

    CAMPOS_FOTOS = ("fotos_nuevas", "borrar_fotos", "principal")

    fotos_nuevas = MultipleFileField(
        required=False,
        label="Agregar fotos",
        help_text=(
            f"Hasta {MAX_FOTOS_POR_PRODUCTO} fotos por producto, JPG o PNG. Podés elegir varias "
            "de una vez. En un producto nuevo, la primera es la principal."
        ),
        widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )
    borrar_fotos = forms.ModelMultipleChoiceField(
        queryset=FotoProducto.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Borrar",
    )
    principal = forms.ModelChoiceField(
        queryset=FotoProducto.objects.none(),
        required=False,
        empty_label=None,
        widget=forms.RadioSelect,
        label="Principal",
    )

    class Meta:
        model = Producto
        fields = ["nombre", "categoria", "descripcion", "precio", "publicado", "orden"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hacer_orden_opcional()
        # El related manager de un producto sin pk tira `ValueError`.
        if self.instance.pk:
            fotos = FotoProducto.objects.filter(producto=self.instance)
            self.fields["borrar_fotos"].queryset = fotos
            self.fields["principal"].queryset = fotos
            self.fields["principal"].initial = fotos.values_list("pk", flat=True).first()

    def campos_generales(self):
        return [campo for campo in self if campo.name not in self.CAMPOS_FOTOS]

    def clean_fotos_nuevas(self):
        preparadas = []
        for archivo in self.cleaned_data.get("fotos_nuevas") or []:
            if archivo is None:
                continue
            try:
                preparadas.append(preparar_foto(archivo))
            except forms.ValidationError as error:
                raise forms.ValidationError(f"{archivo.name}: {' '.join(error.messages)}")
        return preparadas

    def clean(self):
        cleaned = super().clean()
        existentes = self.fields["borrar_fotos"].queryset.count() if self.instance.pk else 0
        borradas = cleaned.get("borrar_fotos") or []
        nuevas = cleaned.get("fotos_nuevas") or []
        if existentes - len(borradas) + len(nuevas) > MAX_FOTOS_POR_PRODUCTO:
            self.add_error(
                "fotos_nuevas",
                f"Máximo {MAX_FOTOS_POR_PRODUCTO} fotos por producto: borrá alguna antes de agregar.",
            )
        # El radio «Principal» siempre viaja marcado (con la principal actual):
        # si esa misma foto se borra, no es un conflicto, es «sin principal
        # explícita»; manda la de menor posición que quede (o la nueva).
        principal = cleaned.get("principal")
        if principal is not None and principal in borradas:
            cleaned["principal"] = None
        return cleaned

    def guardar_fotos(self, producto):
        """Va dentro del `atomic()` de la vista, después de `save()`. Borra
        sólo las filas (el archivo queda en R2), agrega las nuevas al final en
        el orden elegido y renumera 0..n-1 (la principal elegida primero; sin
        principal explícita, o si se borró, queda el orden actual)."""
        borradas = self.cleaned_data.get("borrar_fotos") or []
        if borradas:
            FotoProducto.objects.filter(producto=producto, pk__in=[f.pk for f in borradas]).delete()
        nuevas = self.cleaned_data.get("fotos_nuevas") or []
        if nuevas:
            ultima = producto.fotos.aggregate(m=Max("posicion"))["m"]
            siguiente = 0 if ultima is None else ultima + 1
            for i, archivo in enumerate(nuevas):
                FotoProducto.objects.create(producto=producto, imagen=archivo, posicion=siguiente + i)
        principal = self.cleaned_data.get("principal")
        if principal is not None or borradas:
            fotos = list(producto.fotos.all())
            if principal is not None:
                fotos = [f for f in fotos if f.pk == principal.pk] + [f for f in fotos if f.pk != principal.pk]
            cambiadas = []
            for posicion, foto in enumerate(fotos):
                if foto.posicion != posicion:
                    foto.posicion = posicion
                    cambiadas.append(foto)
            if cambiadas:
                FotoProducto.objects.bulk_update(cambiadas, ["posicion"])


class VarianteForm(forms.ModelForm):
    """`stock` NO es un campo del form: escribirlo en forma absoluta pisaría
    las reservas que entren mientras la pantalla está abierta. En el alta se
    carga como `stock_inicial`; después sólo con «ajuste ±».

    `gimnasio` llega por `form_kwargs` (lo usa el formset para la unicidad del
    código); el form en sí sólo valida el formato."""

    stock_inicial = forms.IntegerField(
        min_value=0, max_value=100000, required=False, label="Stock inicial"
    )

    class Meta:
        model = Variante
        fields = ["nombre", "codigo", "activa"]
        widgets = {
            # `data-codigo-barras`: el lector USB manda Enter al final y
            # `tienda_variantes.js` lo convierte en «pasar al campo siguiente»
            # (si no, cada escaneo enviaría el form a medio cargar).
            "codigo": forms.TextInput(attrs={"data-codigo-barras": "", "autocomplete": "off"}),
        }

    def __init__(self, *args, gimnasio=None, **kwargs):
        self.gimnasio = gimnasio
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["stock_inicial"]
        # El largo se valida DESPUÉS de normalizar (`FORMATO` ya topea en 32):
        # un código pegado con espacios puede pasar de 32 y ser válido. Sin
        # esto el `max_length` del modelo lo rechazaba antes de `clean_codigo`,
        # y el `maxlength` del input lo cortaba en el navegador.
        campo = self.fields["codigo"]
        campo.max_length = None
        campo.validators = [v for v in campo.validators if not isinstance(v, MaxLengthValidator)]
        campo.widget.attrs.pop("maxlength", None)

    def clean_codigo(self):
        codigo = normalizar(self.cleaned_data.get("codigo"))
        validar_codigo(codigo, pk=self.instance.pk)
        return codigo

    def save(self, commit=True):
        variante = super().save(commit=False)
        if variante.pk is None:
            variante.stock = self.cleaned_data.get("stock_inicial") or 0
            if commit:
                variante.save()
        elif commit:
            # `codigo` vacío: `Variante.save()` inventa el interno de nuevo.
            variante.save(update_fields=["nombre", "codigo", "activa"])
        return variante


class BaseVarianteFormSet(BaseInlineFormSet):
    def clean(self):
        # El código antes del corte por errores: es un error de fila más, y
        # así se ve junto con los demás en el mismo intento.
        self._validar_codigos()
        # Primero las reglas propias: el `validate_unique` de Django (que corre
        # en `super().clean()`) contaría como duplicados los dos extras vacíos
        # y tapa nuestros mensajes con «información duplicada en nombre».
        if any(self.errors):
            return
        vivas = [
            f for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
            and (
                f.instance.pk or f.cleaned_data.get("nombre") or f.cleaned_data.get("codigo")
                or f.cleaned_data.get("stock_inicial") is not None
            )
        ]
        if not vivas:
            raise forms.ValidationError("El producto tiene que tener al menos una variante.")
        nombres = [f.cleaned_data.get("nombre", "").strip().lower() for f in vivas]
        if len(nombres) != len(set(nombres)):
            raise forms.ValidationError("Hay dos variantes con el mismo nombre.")
        super().clean()

    def _validar_codigos(self):
        """Único por gimnasio (sin mayúsculas: todo código se guarda
        normalizado) y único entre las filas. Una sola query para todas las
        filas.

        - Las filas que de verdad se borran no cuentan (liberan su código);
          las que tienen ventas sí, aunque se posteen «Quitar» (se conservan
          desactivadas).
        - Contra la base se excluyen las variantes del propio formset: así
          intercambiar dos códigos entre filas no da un falso duplicado.
        """
        filas = [
            f for f in self.forms
            if getattr(f, "cleaned_data", None) and f.cleaned_data.get("codigo")
            and not (self.can_delete and self._should_delete_form(f))
        ]
        if not filas:
            return
        por_codigo = {}
        for f in filas:
            por_codigo.setdefault(f.cleaned_data["codigo"], []).append(f)
        for codigo, mismas in por_codigo.items():
            if len(mismas) > 1:
                for f in mismas:
                    f.add_error("codigo", f"Hay dos variantes con el código {codigo}.")
        gimnasio = self.form_kwargs.get("gimnasio")
        if gimnasio is None:
            return
        propias = [f.instance.pk for f in self.forms if f.instance.pk]
        usados = set(
            Variante.objects.filter(producto__gimnasio=gimnasio, codigo__in=list(por_codigo))
            .exclude(pk__in=propias)
            .values_list("codigo", flat=True)
        )
        for codigo in usados:
            for f in por_codigo[codigo]:
                if "codigo" not in f.errors:
                    f.add_error("codigo", "Ese código ya lo usa otro producto de tu gimnasio.")

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
    CAMPOS_MP = ("acepta_transferencia", "acepta_mercadopago", "minutos_reserva_mp")

    class Meta:
        model = ConfiguracionTienda
        fields = [
            "publicada", "horas_reserva_transferencia", "max_pedidos_activos",
            "instrucciones_retiro", "acepta_transferencia", "acepta_mercadopago",
            "minutos_reserva_mp",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Con la bandera apagada (o en la demo) el form es el de siempre: sin
        # estos campos un POST viejo no los pisa ni el HTML los muestra.
        if not settings.TIENDA_MP_ENABLED or self.gimnasio.es_demo:
            for nombre in self.CAMPOS_MP:
                del self.fields[nombre]

    def clean(self):
        cleaned = super().clean()
        # Sólo si los campos están: con la bandera apagada no hay nada que validar.
        if "acepta_mercadopago" not in self.fields:
            return cleaned
        errores = self.errors
        transferencia = cleaned.get("acepta_transferencia")
        mercadopago = cleaned.get("acepta_mercadopago")
        if "acepta_transferencia" not in errores and "acepta_mercadopago" not in errores:
            if not transferencia and not mercadopago:
                self.add_error("acepta_transferencia", "Elegí al menos un medio de pago.")
            # Sólo al PRENDERLO exige la cuenta conectada: si ya estaba prendido y
            # la cuenta pasó a «reconectar», guardar otro campo no se bloquea.
            elif mercadopago and not self.initial.get("acepta_mercadopago"):
                conectada = CuentaMercadoPago.objects.for_gimnasio(self.gimnasio).filter(
                    estado=CuentaMercadoPago.Estado.CONECTADA
                ).exists()
                if not conectada:
                    self.add_error(
                        "acepta_mercadopago",
                        "Primero conectá tu cuenta de Mercado Pago.",
                    )
        return cleaned


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
