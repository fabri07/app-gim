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
from django.db.models import Max
from django.utils import timezone

from alumnos.models import Alumno
from core.forms import TenantScopedModelForm
from ejercicios.models import Ejercicio
from rutinas import services
from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)


class RutinaPlantillaForm(TenantScopedModelForm):
    class Meta:
        model = RutinaPlantilla
        fields = ["nombre", "objetivo", "nivel", "dias_por_semana", "activa"]


class RutinaPlantillaItemForm(TenantScopedModelForm):
    """Agrega o edita UN ejercicio de una plantilla.

    Recibe `plantilla` (lo inyecta `ItemPlantillaMixin.get_form_kwargs`)
    porque dos de sus reglas dependen de lo que ya hay cargado en ese día, y
    el `form.instance.rutina` recién se asigna en `form_valid`, después de
    validar:

    - **`orden` es opcional y se calcula al final del día** (`max + 1`).
      Es un número administrativo que el sistema puede deducir; obligarlo a
      tipearlo era la causa de que un cliente real guardara y la plantilla le
      quedara vacía. Misma regla que `services.agregar_ejercicio_asignado`
      para el otro flujo, y el mismo motivo para no renumerar insertando:
      reordenar está fuera de alcance.
    - **`dia_nombre` en blanco hereda el del día.** Está denormalizado por
      item (ver el modelo), y dejar el nuevo como el único sin etiqueta rompe
      la regla de lectura de `agrupacion.py` ("gana la semana más baja").

    `series` y `repeticiones` siguen obligatorios a propósito: son la
    prescripción del entrenamiento, no hay valor sensato que inventar, y un
    item sin ellas le llega al alumno como una fila vacía en el portal y en
    el PDF.
    """

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
        labels = {
            "dia": "Día",
            "dia_nombre": "Nombre del día",
            "kilos": "Kilos",
        }
        help_texts = {
            # El help_text del modelo dice "1..dias_por_semana": el nombre de
            # un campo del código, que no significa nada para un dueño de
            # gimnasio. Los `help_texts` del form pisan los del modelo.
            "dia": "Día 1, 2, 3... de la rutina (no el día de la semana).",
            "orden": "Posición dentro del día. Si lo dejás vacío, se agrega al final.",
            "dia_nombre": 'Opcional. Por ejemplo: "Tren superior · Core".',
        }

    def __init__(self, *args, plantilla=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plantilla = plantilla
        self.fields["orden"].required = False

    def _items_del_dia(self, dia):
        """Items ya cargados en ese día de esta plantilla, excluyendo el que
        se está editando (si no, editar sin tocar `orden` lo empujaría al
        final una y otra vez)."""
        if self.plantilla is None:
            return RutinaPlantillaItem.objects.none()
        queryset = self.plantilla.items.filter(dia=dia)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        return queryset

    def clean(self):
        cleaned_data = super().clean()
        dia = cleaned_data.get("dia")
        if dia is None:
            # `dia` ya tiene su propio error; sin él no hay día contra el cual
            # contar el orden ni del cual heredar el nombre.
            return cleaned_data

        del_dia = self._items_del_dia(dia)

        if cleaned_data.get("orden") is None:
            cleaned_data["orden"] = (
                del_dia.aggregate(Max("orden"))["orden__max"] or 0
            ) + 1

        if not cleaned_data.get("dia_nombre"):
            heredado = next(
                (
                    nombre
                    for nombre in del_dia.order_by("semana", "orden").values_list(
                        "dia_nombre", flat=True
                    )
                    if nombre
                ),
                "",
            )
            cleaned_data["dia_nombre"] = heredado

        return cleaned_data


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
        self.fields["alumno"].widget = SelectConPlanVigente(
            planes=self._planes_vigentes(gimnasio),
            choices=self.fields["alumno"].choices,
        )

    @staticmethod
    def _planes_vigentes(gimnasio):
        """`{alumno_id: (nombre del plan, fecha en que termina)}` en UNA query.

        Alimenta los `data-` de cada `<option>` para que el JS pueda sugerir
        la fecha de inicio sin ir al servidor por cada cambio del select.
        """
        hoy = timezone.localdate()
        vigentes = {}
        # Ordenado de menos a más reciente: al recorrer, cada alumno termina
        # quedándose con la ÚLTIMA que arrancó, que es el mismo criterio de
        # `RutinaAsignada.vigente_de`. Un solo recorrido, sin query por alumno.
        for rutina in (
            RutinaAsignada.objects.for_gimnasio(gimnasio)
            .filter(activa=True, fecha_inicio__lte=hoy)
            .order_by("fecha_inicio", "id")
        ):
            vigentes[rutina.alumno_id] = rutina
        return vigentes

    def clean(self):
        """Va en `clean()` y no en `clean_fecha_inicio()` porque necesita
        `cleaned_data["alumno"]`, que es otro campo: en un `clean_<campo>` eso
        depende del orden de declaración, que es frágil y no se documenta
        solo."""
        cleaned = super().clean()
        alumno = cleaned.get("alumno")
        fecha_inicio = cleaned.get("fecha_inicio")
        if not alumno or not fecha_inicio:
            return cleaned

        vigente = RutinaAsignada.vigente_de(alumno=alumno)
        if vigente is not None and fecha_inicio < vigente.fecha_inicio:
            # Una rutina que arranca antes que la vigente nunca sería elegida
            # (gana la más reciente): quedaría invisible en la base. El modelo
            # lo revalida, esto es para dar el mensaje en el campo.
            self.add_error(
                "fecha_inicio",
                f"{alumno} está haciendo «{vigente.nombre_snapshot}» desde el "
                f"{vigente.fecha_inicio:%d/%m/%Y}. Una rutina que arranque "
                f"antes de esa fecha nunca llegaría a verse.",
            )
        return cleaned


class SelectConPlanVigente(forms.Select):
    """`<select>` de alumnos donde cada `<option>` lleva la fecha sugerida.

    Existe para no agregar un endpoint: el form ya resolvió los planes
    vigentes en una query, así que el dato viaja en el HTML y un listener de
    `change` completa el campo de fecha. El proyecto NO usa swaps parciales de
    htmx en ningún lado (solo `hx-boost` global), así que introducir el primero
    para prellenar un input no se paga.
    """

    def __init__(self, *, planes, **kwargs):
        super().__init__(**kwargs)
        self.planes = planes

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        pk = getattr(value, "value", value)
        vigente = self.planes.get(pk)
        if vigente is not None:
            option["attrs"]["data-fecha-sugerida"] = (
                vigente.fecha_fin_prevista.isoformat()
            )
            option["attrs"]["data-aviso"] = (
                f"Está haciendo «{vigente.nombre_snapshot}» hasta el "
                f"{vigente.ultimo_dia:%d/%m/%Y}. Este plan arranca al día "
                f"siguiente; si ponés una fecha anterior, le cortás el ciclo."
            )
        return option


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
