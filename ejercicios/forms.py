"""Forms de la biblioteca de ejercicios de un gimnasio.

`EjercicioForm` hereda de `TenantScopedModelForm`, que ya acota solo el FK
`categoria` al gimnasio del usuario. Acá se agregan las dos cosas que el mixin
no puede saber: que las categorías desactivadas no se ofrecen, y que el staff
puede crear una categoría sin salir de esta pantalla.
"""

from django import forms
from django.db.models import Q

from core.forms import TenantScopedModelForm
from ejercicios.models import CategoriaEjercicio, Ejercicio
from importaciones.parsing import normalizar_texto


class EjercicioForm(TenantScopedModelForm):
    categoria_nueva = forms.CharField(
        max_length=60,
        required=False,
        label="…o escribí una categoría nueva",
        help_text=(
            "Si la categoría que necesitás todavía no está en la lista, "
            "escribila acá y se crea sola."
        ),
    )

    class Meta:
        model = Ejercicio
        fields = ["nombre", "categoria", "descripcion", "url_video", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `TenantScopedModelForm` ya acotó el queryset al gimnasio; falta
        # sacar las desactivadas. La que el ejercicio YA tiene se conserva
        # aunque esté desactivada: editarle el video a un ejercicio viejo no
        # debería obligar a reclasificarlo.
        visibles = Q(activo=True)
        actual = self.instance.categoria_id if self.instance.pk else None
        if actual is not None:
            visibles |= Q(pk=actual)
        self.fields["categoria"].queryset = self.fields[
            "categoria"
        ].queryset.filter(visibles)
        self.fields["categoria"].label = "Categoría"

    def clean(self):
        cleaned = super().clean()
        categoria = cleaned.get("categoria")
        nueva = (cleaned.get("categoria_nueva") or "").strip()

        if categoria and nueva:
            raise forms.ValidationError(
                "Elegí una categoría de la lista o escribí una nueva, no las dos."
            )
        if not categoria and not nueva:
            raise forms.ValidationError(
                "Elegí una categoría de la lista o escribí una nueva."
            )
        return cleaned

    def save(self, commit=True):
        nueva = (self.cleaned_data.get("categoria_nueva") or "").strip()
        if nueva:
            # `get_or_create` por `nombre_normalizado` (la misma clave de la
            # UniqueConstraint): escribir "empuje" cuando ya existe "EMPUJE"
            # reusa la que hay en vez de chocar contra la constraint.
            categoria, _ = CategoriaEjercicio.objects.get_or_create(
                gimnasio=self.gimnasio,
                nombre_normalizado=normalizar_texto(nueva),
                defaults={"nombre": nueva},
            )
            self.instance.categoria = categoria
        return super().save(commit)


class CategoriaEjercicioForm(TenantScopedModelForm):
    """Alta/edición de una categoría. Incluye `activo` a propósito: no hay
    `DeleteView` -- "eliminar" una categoría es destildar `activo`, mismo
    patrón que `MedioCobro` y `Novedad`. Además `Ejercicio.categoria` es
    `on_delete=PROTECT`, así que borrar una categoría en uso no es siquiera
    posible sin reasignar antes cada ejercicio.
    """

    class Meta:
        model = CategoriaEjercicio
        fields = ["nombre", "orden", "activo"]

    def clean_nombre(self):
        """La `UniqueConstraint` es sobre `nombre_normalizado`, que el form no
        expone: sin este chequeo, renombrar una categoría a una que ya existe
        escrita distinto explota con un `IntegrityError` (un 500) en vez de
        un error de campo.
        """
        nombre = self.cleaned_data["nombre"]
        chocan = CategoriaEjercicio.objects.for_gimnasio(self.gimnasio).filter(
            nombre_normalizado=normalizar_texto(nombre)
        )
        if self.instance.pk:
            chocan = chocan.exclude(pk=self.instance.pk)
        if chocan.exists():
            raise forms.ValidationError(
                f"Ya tenés una categoría llamada «{chocan.first().nombre}». "
                "Las mayúsculas y los acentos no la hacen distinta."
            )
        return nombre
