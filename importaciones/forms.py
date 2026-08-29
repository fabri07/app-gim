"""Forms del importador (Proyecto 2). Los de subida son `forms.Form`
planos (no `ModelForm`, no hay modelo destino directo) que aceptan
`gimnasio` en `__init__` solo porque `TenantScopedMixin.get_form_kwargs()`
siempre lo inyecta -- mismo patrón que `AsignarRutinaForm` en
`rutinas/forms.py`. Los formsets de preview usan `forms.formset_factory`
(mecanismo idiomático de Django para N repeticiones de un sub-form)."""

import json

from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import BLANK_CHOICE_DASH

from ejercicios.models import CategoriaEjercicio
from rutinas.models import RutinaPlantilla


class SubirArchivoForm(forms.Form):
    archivo = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])]
    )

    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.gimnasio = gimnasio


class SubirPlantillasForm(SubirArchivoForm):
    pass


class SubirBibliotecaForm(SubirArchivoForm):
    pass


class HojaMetadataForm(forms.Form):
    nombre_hoja = forms.CharField(widget=forms.HiddenInput)
    incluir = forms.BooleanField(required=False, initial=True)
    objetivo = forms.CharField(max_length=120)
    # `BLANK_CHOICE_DASH` al frente evita que el navegador pre-seleccione
    # la primera choice real ("principiante") cuando el staff no toca el
    # <select> -- sin esto el HTML no tiene ninguna opción "sin elegir" y
    # el browser simplemente muestra/envía la primera de la lista (mismo
    # bug que `categoria` en `ResolucionEjercicioForm`, ver ahí).
    nivel = forms.ChoiceField(choices=BLANK_CHOICE_DASH + RutinaPlantilla.Nivel.choices)


HojaMetadataFormSet = forms.formset_factory(HojaMetadataForm, extra=0)


class ResolucionEjercicioForm(forms.Form):
    nombre_normalizado = forms.CharField(widget=forms.HiddenInput)
    accion = forms.ChoiceField(choices=[
        ("usar_existente", "Usar existente"),
        ("crear_nuevo", "Crear como nuevo"),
    ])
    ejercicio_existente_id = forms.IntegerField(required=False)
    # Constraint no negociable: "todo ejercicio nuevo requiere que el staff
    # lo elija en el preview, nunca un default silencioso". `empty_label`
    # (el equivalente de `BLANK_CHOICE_DASH` en un ModelChoiceField) evita
    # que el navegador pre-seleccione y mande la primera categoría real
    # aunque el staff nunca haya tocado el campo -- sin eso el guard de
    # `clean()` no llegaba a dispararse desde un POST real de navegador
    # (fix post-review, hallazgo 1).
    #
    # Es `ModelChoiceField` desde 2026-08-26: las categorías son por
    # gimnasio, así que el queryset se inyecta por `form_kwargs` del
    # formset. `queryset=none()` como default para que un form armado sin
    # `gimnasio` no ofrezca las categorías de todos los gimnasios.
    categoria = forms.ModelChoiceField(
        queryset=CategoriaEjercicio.objects.none(),
        required=False,
        empty_label="---------",
        label="Categoría",
    )

    def __init__(self, *args, gimnasio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if gimnasio is not None:
            self.fields["categoria"].queryset = CategoriaEjercicio.objects.for_gimnasio(
                gimnasio
            ).filter(activo=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("accion") == "crear_nuevo" and not cleaned.get("categoria"):
            self.add_error(
                "categoria", "Elegí una categoría para el ejercicio nuevo."
            )
        return cleaned


ResolucionEjercicioFormSet = forms.formset_factory(ResolucionEjercicioForm, extra=0)


class ResolucionesJSONForm(forms.Form):
    """Reemplaza a `ResolucionGrupoMuscularFormSet` (un form por ejercicio
    pendiente) por un único campo con las resoluciones serializadas en
    JSON -- así el conteo de campos del POST de confirmación de biblioteca
    no escala con la cantidad de ejercicios sin match (una biblioteca real
    puede traer miles; ver ISSUES.md [2026-07-28] y su seguimiento). El
    payload es
    {nombre: {"categoria_id": int|None, "categoria_nueva": str|None,
              "sin_categoria": bool|None, "accion": str|None}}
    -- "accion" (usar_existente/crear_nuevo) resuelve un match ambiguo;
    "categoria_id", "categoria_nueva" y "sin_categoria" resuelven (de forma
    excluyente entre sí) un ejercicio cuya categoría el importador no pudo
    deducir del archivo. Un mismo ejercicio pendiente puede necesitar la
    acción, una categoría, o las dos cosas a la vez (Tarea 14).

    `categoria_id` se valida acá solo como forma (que sea un entero): que
    pertenezca al gimnasio lo chequea `confirmar_importacion_biblioteca`
    contra la base, que es donde hay que hacerlo -- un id de otro tenant
    tiene que morir contra un queryset scopeado, no contra una lista que
    este form haya cacheado."""
    resoluciones = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self):
        # Validado en `clean()` (no en `clean_resoluciones`) para poder usar
        # `add_error(None, ...)`: un error de campo en `resoluciones` no se
        # ve en ningún lado (el campo es un `HiddenInput` y el template solo
        # renderiza `form.non_field_errors`) -- sin esto, un POST manipulado
        # o un bug del JS de serialización re-renderiza 200 sin ningún
        # mensaje visible para el staff (fix post-review, Tarea 13).
        cleaned = super().clean()
        crudo = cleaned.get("resoluciones") or "{}"
        try:
            datos = json.loads(crudo)
        except (json.JSONDecodeError, TypeError):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        if not isinstance(datos, dict):
            self.add_error(None, "Formato de resoluciones inválido.")
            return cleaned
        for clave, valor in datos.items():
            if not isinstance(clave, str) or not isinstance(valor, dict):
                self.add_error(None, "Formato de resoluciones inválido.")
                return cleaned
            categoria_id = valor.get("categoria_id")
            # `isinstance(True, int)` es True en Python: sin el guard de bool,
            # un `categoria_id: true` pasaría como si fuera el pk 1.
            if categoria_id is not None and (
                isinstance(categoria_id, bool) or not isinstance(categoria_id, int)
            ):
                self.add_error(None, "Categoría inválida.")
                return cleaned
            # Una categoría que esta misma importación va a crear todavía
            # no tiene pk, así que se elige por NOMBRE. Acá solo se valida
            # la forma (que sea texto): que ese nombre sea realmente uno de
            # los que trae el archivo lo chequea
            # `confirmar_importacion_biblioteca` contra
            # `resultado["categorias_a_crear"]` -- mismo criterio que
            # `categoria_id`, que se valida contra la base y no acá.
            categoria_nueva = valor.get("categoria_nueva")
            if categoria_nueva is not None and not isinstance(categoria_nueva, str):
                self.add_error(None, "Categoría inválida.")
                return cleaned
            sin_categoria = valor.get("sin_categoria")
            if sin_categoria is not None and not isinstance(sin_categoria, bool):
                self.add_error(None, "Categoría inválida.")
                return cleaned
            accion = valor.get("accion")
            if accion is not None and accion not in ("usar_existente", "crear_nuevo"):
                self.add_error(None, "Acción inválida.")
                return cleaned
        cleaned["resoluciones"] = datos
        return cleaned
