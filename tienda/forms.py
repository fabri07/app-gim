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
    ItemPedido,
    Producto,
    Variante,
)
from tienda.variantes import MAXIMO_VARIANTES, clave, componer_nombre


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
    #: Se dibujan en la sección «Variantes», no en la general.
    CAMPOS_CARACTERISTICAS = ("caracteristica_1", "caracteristica_2")

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
        fields = [
            "nombre", "categoria", "descripcion", "precio", "publicado", "orden",
            "caracteristica_1", "caracteristica_2",
        ]
        labels = {
            "caracteristica_1": "Característica 1",
            "caracteristica_2": "Característica 2 (opcional)",
        }
        help_texts = {
            "caracteristica_1": "Talle, Color, Sabor, Peso… Vacía: el producto tiene una sola variante.",
        }
        widgets = {
            # `list`: el `<datalist>` de sugerencias del template. `data-*`:
            # el generador de `tienda_variantes.js` lee de acá.
            "caracteristica_1": forms.TextInput(attrs={
                "list": "sugerencias-caracteristicas", "autocomplete": "off",
                "data-caracteristica": "1",
            }),
            "caracteristica_2": forms.TextInput(attrs={
                "list": "sugerencias-caracteristicas", "autocomplete": "off",
                "data-caracteristica": "2",
            }),
        }

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
        fuera = self.CAMPOS_FOTOS + self.CAMPOS_CARACTERISTICAS
        return [campo for campo in self if campo.name not in fuera]

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
        # Si alguna de las dos queda con error, `add_error` la saca de
        # `cleaned_data` y la vista le avisa al formset que no confíe en ellas
        # (`caracteristicas_validas=False`): así no aparecen errores falsos en
        # todas las filas.
        if "caracteristica_1" not in self.errors and "caracteristica_2" not in self.errors:
            primera = cleaned.get("caracteristica_1", "")
            segunda = cleaned.get("caracteristica_2", "")
            if segunda and not primera:
                self.add_error("caracteristica_2", "Poné primero la característica 1.")
            elif segunda and clave(segunda) == clave(primera):
                self.add_error(
                    "caracteristica_2",
                    "Las dos características son la misma: poné otra o dejala vacía.",
                )
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
    """Una fila de variante: un valor por característica del producto, el
    código de barras y «Activa». `nombre` NO es campo: lo calcula `save()`
    de los valores («M · Negro», o «» sin características).

    `stock` tampoco: escribirlo en forma absoluta pisaría las reservas que
    entren mientras la pantalla está abierta. En el alta se carga como
    `stock_inicial`; después sólo con «ajuste ±».

    Llegan por `form_kwargs` (los arma el formset): `gimnasio` (unicidad del
    código), `caracteristicas` (las validadas del form del producto, para
    limpiar valores y reglas), `caracteristicas_validas`, `etiquetas` (lo
    que el staff tipeó, para las etiquetas del redibujo) y `fila_unica`."""

    stock_inicial = forms.IntegerField(
        min_value=0, max_value=100000, required=False, label="Stock inicial"
    )

    class Meta:
        model = Variante
        fields = ["valor_1", "valor_2", "codigo", "activa"]
        labels = {"codigo": "Código de barras"}
        help_texts = {"codigo": "Vacío: la app inventa uno."}
        widgets = {
            "valor_1": forms.TextInput(attrs={"data-valor": "1", "autocomplete": "off"}),
            "valor_2": forms.TextInput(attrs={"data-valor": "2", "autocomplete": "off"}),
            # `data-codigo-barras`: el lector USB manda Enter al final y
            # `tienda_variantes.js` lo convierte en «pasar al campo siguiente»
            # (si no, cada escaneo enviaría el form a medio cargar).
            "codigo": forms.TextInput(attrs={"data-codigo-barras": "", "autocomplete": "off"}),
        }

    def __init__(
        self, *args, gimnasio=None, caracteristicas=("", ""), caracteristicas_validas=True,
        etiquetas=None, fila_unica=False, **kwargs,
    ):
        self.gimnasio = gimnasio
        self.caracteristicas = tuple(caracteristicas)
        self.caracteristicas_validas = caracteristicas_validas
        self.fila_unica = fila_unica
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["stock_inicial"]
            # Creada por el código viejo durante el deploy (nombre libre, sin
            # valores): se precarga para que el staff no la retipee.
            if not self.instance.valor_1 and self.instance.nombre:
                self.initial["valor_1"] = self.instance.nombre
        etiquetas = tuple(etiquetas) if etiquetas is not None else self.caracteristicas
        # «Valor 1/2» sólo en un alta sin JS: el script pone el nombre de la
        # característica apenas se tipea.
        self.fields["valor_1"].label = etiquetas[0] or "Valor 1"
        self.fields["valor_2"].label = etiquetas[1] or "Valor 2"
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

    def clean(self):
        # Acá y no en el `clean` del formset: cuando corre ése, `_post_clean`
        # ya copió los valores a la instancia. Borrar una característica no
        # deja errores sobre campos que el JS esconde. Con una característica
        # inválida en el form del producto no se toca nada (no se sabe cuál
        # quedó vacía de verdad; ese POST no guarda).
        cleaned = super().clean()
        if self.caracteristicas_validas:
            primera, segunda = self.caracteristicas
            if not primera:
                cleaned["valor_1"] = ""
                cleaned["valor_2"] = ""
            elif not segunda:
                cleaned["valor_2"] = ""
        return cleaned

    def nombre_calculado(self):
        datos = getattr(self, "cleaned_data", None) or {}
        return componer_nombre(datos.get("valor_1", ""), datos.get("valor_2", ""))

    def has_changed(self):
        # La fila única de un alta sin características se guarda aunque nadie
        # la toque (si no, el producto quedaría sin variantes). Y una fila
        # cuyo nombre cambia sin que cambie lo posteado (se borró una
        # característica; una fila creada por el código viejo) también. El
        # `hasattr`: el `full_clean` del formset pregunta antes del `clean`.
        if super().has_changed() or self.fila_unica:
            return True
        return bool(
            self.instance.pk and hasattr(self, "cleaned_data")
            and self.nombre_calculado() != self.instance.nombre
        )

    def save(self, commit=True):
        variante = super().save(commit=False)
        # Siempre, también «»: sin valores `Variante.save()` no toca el
        # nombre, y vaciar los dos dejaría el «M» viejo.
        variante.nombre = self.nombre_calculado()
        if variante.pk is None:
            variante.stock = self.cleaned_data.get("stock_inicial") or 0
            if commit:
                variante.save()
        elif commit:
            # `codigo` vacío: `Variante.save()` inventa el interno de nuevo.
            variante.save(update_fields=["nombre", "valor_1", "valor_2", "codigo", "activa"])
        return variante


class BaseVarianteFormSet(BaseInlineFormSet):
    """Las filas de variantes del form de producto.

    Reglas (sobre las filas «vivas» y activas): sin `caracteristica_1`, como
    mucho una activa; con ella, cada activa con su valor (y el de la 2 si la
    hay); al menos una viva siempre; sin nombres repetidos entre activas (ni
    contra una desactivada sin ventas). La base ya no tiene constraint de
    nombre único (ver `tienda/0008`): esto es lo que lo frena.

    Ojo con el orden de Django: `full_clean` llama `has_changed()` y
    `_should_delete_form()` de cada fila ANTES de este `clean`, así que
    «viva» (`_es_viva`) es función pura del estado de la fila, nunca algo que
    calcule `clean`."""

    default_error_messages = {"too_many_forms": "Son más de %(num)s variantes."}

    def __init__(
        self, *args, caracteristicas=("", ""), caracteristicas_validas=True, etiquetas=None,
        con_ventas=None, **kwargs,
    ):
        self.caracteristicas = tuple(caracteristicas)
        self.caracteristicas_validas = caracteristicas_validas
        self.etiquetas = tuple(etiquetas) if etiquetas is not None else self.caracteristicas
        super().__init__(*args, **kwargs)
        # Variantes con ventas (no se borran: se desactivan). Lo arma la vista
        # con una query; si no llega, una query acá (nunca una por fila).
        if con_ventas is None:
            con_ventas = (
                set(
                    ItemPedido.objects.filter(variante__producto=self.instance)
                    .values_list("variante_id", flat=True)
                )
                if self.instance.pk else set()
            )
        self.con_ventas = set(con_ventas)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs.update(
            caracteristicas=self.caracteristicas,
            caracteristicas_validas=self.caracteristicas_validas,
            etiquetas=self.etiquetas,
        )
        # Alta (o producto sin variantes) sin características: la primera fila
        # es LA variante y se guarda aunque venga sin tocar. `empty_form` pasa
        # `index=None` (y ya trae su propio `empty_permitted`).
        if (
            index is not None and index == self.initial_form_count() == 0
            and self.caracteristicas_validas and not self.caracteristicas[0]
        ):
            kwargs.update(fila_unica=True, empty_permitted=False)
        return kwargs

    @staticmethod
    def _borrar_posteado(form):
        return bool((getattr(form, "cleaned_data", None) or {}).get("DELETE"))

    def _es_viva(self, form):
        """Fila que cuenta como variante: no borrada de verdad (una con ventas
        posteada «Quitar» se conserva desactivada, así que cuenta), con `pk` o
        con algún dato, o la fila única. Pura: sólo mira la fila."""
        datos = getattr(form, "cleaned_data", None) or {}
        if form.instance.pk:
            return not self._borrar_posteado(form) or form.instance.pk in self.con_ventas
        if self._borrar_posteado(form):
            return False
        if form.fila_unica:
            return True
        return bool(
            datos.get("valor_1") or datos.get("valor_2") or datos.get("codigo")
            or datos.get("stock_inicial") is not None
        )

    def _activa_efectiva(self, form):
        """Activa después de guardar: «Quitar» sobre una con ventas la
        desactiva aunque venga tildada."""
        return bool(form.cleaned_data.get("activa")) and not (
            self._borrar_posteado(form) and form.instance.pk in self.con_ventas
        )

    def clean(self):
        # El código antes del corte por errores: es un error de fila más, y
        # así se ve junto con los demás en el mismo intento.
        self._validar_codigos()
        if any(self.errors):
            return
        # Una característica con error en el form del producto: no se sabe
        # qué reglas aplicar; sólo corrieron código y largo. Ese POST no
        # guarda (el form del producto es inválido).
        if not self.caracteristicas_validas:
            return
        primera, segunda = self.caracteristicas
        vivas = [f for f in self.forms if self._es_viva(f)]
        if not vivas:
            raise forms.ValidationError("El producto tiene que tener al menos una variante.")
        activas = [f for f in vivas if self._activa_efectiva(f)]
        if not primera:
            if len(activas) > 1:
                raise forms.ValidationError(
                    "Sin características el producto tiene una sola variante. "
                    "Poné una, como Talle, para cargar varias."
                )
        else:
            for f in activas:
                if not f.cleaned_data.get("valor_1"):
                    f.add_error("valor_1", f"Completá el {primera} o desactivá esta fila.")
                if segunda and not f.cleaned_data.get("valor_2"):
                    f.add_error("valor_2", f"Completá el {segunda} o desactivá esta fila.")
            if any(self.errors):
                return
        self._validar_nombres(vivas, activas)
        super().clean()

    def _validar_nombres(self, vivas, activas):
        """Sin dos activas con el mismo nombre, ni una activa con el de una
        desactivada SIN ventas. Dos desactivadas nunca chocan, ni una activa
        con una desactivada con ventas: el catálogo sólo muestra activas y
        los pedidos van por `pk`, y así sacar una característica a un
        producto con ventas nunca deja un callejón sin salida."""
        dormidas = {
            clave(f.nombre_calculado())
            for f in vivas
            if f not in activas and f.instance.pk not in self.con_ventas
        }
        vistas = set()
        for f in activas:
            nombre = f.nombre_calculado()
            if not nombre:
                # Sólo pasa sin características (con ellas cada activa tiene
                # su valor): una activa y otra «» desactivada sin ventas que
                # sobra. Error general: los campos de valor están escondidos.
                if "" in dormidas:
                    raise forms.ValidationError(
                        "Sin características el producto tiene una sola variante: quitá la que sobra."
                    )
            elif clave(nombre) in vistas:
                f.add_error("valor_1", f"Hay dos variantes «{nombre}».")
            elif clave(nombre) in dormidas:
                f.add_error(
                    "valor_1",
                    f"Ya existe «{nombre}» desactivada: tildá Activa en esa fila y quitá esta.",
                )
            vistas.add(clave(nombre))

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
        borrar = super()._should_delete_form(form)
        if borrar and form.instance.pk and form.instance.pk in self.con_ventas:
            # Una variante con ventas no se borra (ItemPedido la protege): se
            # desactiva. El template no muestra la casilla en ese caso; esto
            # es el guard por si se postea a mano.
            form.instance.activa = False
            return False
        if borrar:
            return True
        # Una fila nueva sin datos (la extra con sólo «Activa» destildada, o
        # una sobrante con el valor limpiado) se descarta: si no, se guardaría
        # como una variante «». NUNCA una con errores: un código «AB» o un
        # stock «-1» en una fila nueva se perdería en silencio.
        return form.instance.pk is None and not form.errors and not self._es_viva(form)


VarianteFormSet = inlineformset_factory(
    Producto, Variante, form=VarianteForm, formset=BaseVarianteFormSet,
    # Una fila vacía siempre disponible (también sin JS); el generador la
    # reusa antes de clonar. El tope cuenta también las inactivas con ventas.
    extra=1, min_num=0, max_num=MAXIMO_VARIANTES, validate_max=True, can_delete=True,
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
