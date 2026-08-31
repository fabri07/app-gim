"""
Forms de gestión de rutinas (Fase 2).

`RutinaPlantillaForm` y `RutinaPlantillaItemForm` heredan de
`TenantScopedModelForm`: cierran automáticamente el hueco de FK-injection en
cualquier campo `ModelChoice*Field` cuyo modelo sea `TenantOwnedModel` (en
`RutinaPlantillaItemForm`, el campo `ejercicio` -- ver docstring de
`core.forms.TenantScopedModelForm`).

`AsignarRutinaForm` es un `forms.Form` plano (no `ModelForm`): `RutinaAsignada`
se crea vía `RutinaAsignada.crear_desde_plantilla`, no vía `form.save()` (ver
ROADMAP Fase 1). Por eso el scoping de sus dos `ModelChoiceField` se hace a
mano acá, replicando lo que `TenantScopedModelForm` hace automáticamente para
un `ModelForm`.
"""

from django import forms

from alumnos.models import Alumno
from core.forms import TenantScopedModelForm
from ejercicios.models import Ejercicio
from rutinas import services
from rutinas.models import (
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)


class RutinaPlantillaForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantilla
        fields = ["nombre", "objetivo", "nivel", "dias_por_semana", "activa"]


class RutinaPlantillaItemForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantillaItem
        fields = [
            "ejercicio",
            "semana",
            "dia",
            "dia_nombre",
            "orden",
            "bloque",
            "series",
            "repeticiones",
            "kilos",
            "descanso",
            "notas",
        ]


class AsignarRutinaForm(forms.Form):
    """Elegir alumno + plantilla + fecha de inicio para generar el snapshot
    (ROADMAP Fase 2 §5). Los querysets de ambos campos se acotan al gimnasio
    del staff que asigna, igual que haría `TenantScopedModelForm` en un
    `ModelForm` -- acá se hace a mano porque este form no lo es."""

    alumno = forms.ModelChoiceField(queryset=Alumno.objects.none())
    plantilla = forms.ModelChoiceField(queryset=RutinaPlantilla.objects.none())
    fecha_inicio = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, gimnasio, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["alumno"].queryset = Alumno.objects.for_gimnasio(
            gimnasio
        ).filter(estado=Alumno.Estado.ACTIVO)
        self.fields["plantilla"].queryset = RutinaPlantilla.objects.for_gimnasio(
            gimnasio
        ).filter(activa=True)


class RutinaAsignadaItemForm(TenantScopedModelForm):
    """Edita UN ejercicio de UNA semana de una rutina ya asignada.

    Hereda de `TenantScopedModelForm` por CONTRATO, no por seguridad:
    `core.mixins.TenantScopedMixin.get_form_kwargs()` inyecta `gimnasio=` a
    cualquier form de la vista, y un `ModelForm` común reventaría con un
    `TypeError` por kwarg inesperado. El loop de scoping del padre no
    encuentra nada que acotar, porque `RutinaAsignadaItem` no tiene ninguna FK
    tenant-owned: es un snapshot de texto congelado.

    La vista NUNCA llama a `form.save()`: la escritura está partida entre los
    hermanos (nombre y video) y esta semana (el resto), y de eso se ocupa
    `services.editar_ejercicio_asignado`. Este form aporta el `initial`, los
    `max_length` del modelo y la validación de duplicado traducida a error de
    campo.

    `semana`, `dia`, `orden` y `rpe` quedan deliberadamente afuera: los tres
    primeros son estructura (mover y reordenar están fuera de alcance) y
    `rpe` es dato del ALUMNO -- esta es una pantalla de staff y no debe poder
    pisar lo que el alumno reportó.
    """

    class Meta:
        model = RutinaAsignadaItem
        fields = [
            "ejercicio_nombre_snapshot",
            "ejercicio_video_snapshot",
            "series",
            "repeticiones",
            "kilos",
            "descanso",
            "notas",
            "bloque",
        ]
        labels = {
            "ejercicio_nombre_snapshot": "Nombre del ejercicio",
            "ejercicio_video_snapshot": "Video",
        }
        help_texts = {
            "ejercicio_nombre_snapshot": "Se aplica a las 4 semanas del ciclo.",
            "ejercicio_video_snapshot": "Se aplica a las 4 semanas del ciclo.",
            "series": "Solo la semana que estás editando.",
            "repeticiones": "Solo la semana que estás editando.",
            "kilos": "Solo la semana que estás editando. Podés escribir "
            '"3 min" o "corporal" si el ejercicio no lleva peso.',
            "descanso": "Solo la semana que estás editando.",
            "notas": "Solo la semana que estás editando.",
            "bloque": "Solo la semana que estás editando.",
        }

    def __init__(self, *args, gimnasio, asignada, **kwargs):
        super().__init__(*args, gimnasio=gimnasio, **kwargs)
        self.asignada = asignada
        # El nombre ORIGINAL hay que capturarlo acá, antes de validar:
        # `ModelForm._post_clean` ya le puso a `self.instance` el valor nuevo,
        # así que leerlo de la instancia en `clean_*` daría el nombre entrante
        # y la validación de duplicado se excluiría a sí misma mal.
        self.nombre_original = self.instance.ejercicio_nombre_snapshot

    def clean_ejercicio_nombre_snapshot(self):
        nombre = (self.cleaned_data["ejercicio_nombre_snapshot"] or "").strip()
        if not nombre:
            raise forms.ValidationError("El nombre del ejercicio no puede estar vacío.")
        if not services.nombre_libre_en_el_dia(
            asignada=self.asignada,
            dia=self.instance.dia,
            nombre=nombre,
            excepto_nombre=self.nombre_original,
        ):
            raise forms.ValidationError(
                f"Ya hay otro ejercicio llamado «{nombre}» en el día "
                f"{self.instance.dia}. Si quedaran dos con el mismo nombre, el "
                "alumno los vería fusionados en una sola fila."
            )
        return nombre


class AgregarEjercicioAsignadoForm(TenantScopedModelForm):
    """Agrega un ejercicio de la biblioteca a un día de una rutina asignada.

    Es un `TenantScopedModelForm` sobre `RutinaAsignadaItem` (y no un
    `forms.Form` plano como `AsignarRutinaForm`) por dos beneficios concretos:
    el campo declarado `ejercicio` -- FK a `Ejercicio`, que SÍ es
    `TenantOwnedModel` -- queda acotado automáticamente al gimnasio, cerrando
    el hueco de FK-injection sin repetirlo a mano; y los campos de la semana
    heredan gratis los `max_length` del modelo (que en Postgres son un
    `DataError` si se desbordan, invisible en SQLite).

    `dia` no es campo del form: viene de la URL, así el staff no puede
    inventar un día que la rutina no tiene.

    La vista tampoco llama a `form.save()`: crear la fila de cada semana es
    trabajo de `services.agregar_ejercicio_asignado`.
    """

    ejercicio = forms.ModelChoiceField(
        queryset=Ejercicio.objects.filter(activo=True),
        label="Ejercicio de la biblioteca",
        help_text="Se copian su nombre, su video y su categoría a la rutina de "
        "este alumno. Después podés renombrarlo acá sin tocar la biblioteca.",
    )

    class Meta:
        model = RutinaAsignadaItem
        fields = ["series", "repeticiones", "kilos", "descanso", "notas", "bloque"]
        help_texts = {
            "kilos": 'Podés escribir "3 min" o "corporal" si no lleva peso.',
        }

    field_order = [
        "ejercicio",
        "series",
        "repeticiones",
        "kilos",
        "descanso",
        "bloque",
        "notas",
    ]

    def __init__(self, *args, gimnasio, asignada, dia, **kwargs):
        super().__init__(*args, gimnasio=gimnasio, **kwargs)
        self.asignada = asignada
        self.dia = dia

    def clean_ejercicio(self):
        ejercicio = self.cleaned_data["ejercicio"]
        if not services.nombre_libre_en_el_dia(
            asignada=self.asignada, dia=self.dia, nombre=ejercicio.nombre
        ):
            raise forms.ValidationError(
                f"«{ejercicio.nombre}» ya está en el día {self.dia} de esta "
                "rutina. Editá el que ya está en vez de agregarlo de nuevo."
            )
        return ejercicio
