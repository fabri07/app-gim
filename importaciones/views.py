"""Vistas de gestión del importador (Proyecto 2). Mismo patrón que
`rutinas/views.py`: StaffRequiredMixin + TenantScopedMixin, vistas finas
que delegan toda la lógica a `services.py`."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import FormView, View

from core.mixins import TenantScopedMixin
from ejercicios.models import Ejercicio
from importaciones.forms import (
    HojaMetadataFormSet,
    ResolucionEjercicioFormSet,
    ResolucionesJSONForm,
    SubirBibliotecaForm,
    SubirPlantillasForm,
)
from importaciones.models import Importacion
from importaciones.services import (
    ImportacionInvalida,
    confirmar_importacion_biblioteca,
    confirmar_importacion_plantillas,
    previsualizar_importacion_biblioteca,
    previsualizar_importacion_plantillas,
)
from tenants.mixins import StaffRequiredMixin


class SubirPlantillasView(StaffRequiredMixin, TenantScopedMixin, FormView):
    form_class = SubirPlantillasForm
    template_name = "importaciones/plantillas_subir.html"

    def form_valid(self, form):
        try:
            importacion = previsualizar_importacion_plantillas(
                gimnasio=self.gimnasio,
                archivo=form.cleaned_data["archivo"],
                usuario=self.request.user,
            )
        except ImportacionInvalida as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("importaciones:plantillas_preview", pk=importacion.pk)


class PreviewPlantillasView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/plantillas_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.PLANTILLAS,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _formsets_iniciales(self, importacion):
        resultado = importacion.resultado
        hojas_initial = [
            {
                "nombre_hoja": h["nombre_hoja"],
                # Una hoja sin items (p. ej. excluida por falta de columna
                # requerida, `motivo_exclusion` != None) no debe venir
                # pre-tildada -- confirmar así crearía una `RutinaPlantilla`
                # vacía en silencio (fix post-review, hallazgo 2).
                "incluir": not h.get("motivo_exclusion"),
                "objetivo": "",
                "nivel": "",
            }
            for h in resultado["hojas"]
        ]
        ejercicios_initial = [
            {
                "nombre_normalizado": nombre,
                "accion": "usar_existente" if info["tipo"] in ("exacto", "ambiguo") else "crear_nuevo",
                "ejercicio_existente_id": info.get("ejercicio_id") or info.get("candidato_id"),
            }
            for nombre, info in resultado["ejercicios_distintos"].items()
            if info["tipo"] != "exacto"  # los exactos no requieren decisión del staff
        ]
        hoja_formset = HojaMetadataFormSet(initial=hojas_initial, prefix="form")
        ejercicio_formset = ResolucionEjercicioFormSet(initial=ejercicios_initial, prefix="ejercicios")
        return hoja_formset, ejercicio_formset

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        hoja_formset, ejercicio_formset = self._formsets_iniciales(importacion)
        return self.render(request, importacion, hoja_formset, ejercicio_formset)

    def _nombre_original(self, resultado, nombre_normalizado):
        # `ejercicios_distintos` está keyeado por nombre NORMALIZADO
        # (lowercase, sin tildes) -- para mostrarle al staff el nombre tal
        # como lo escribió en el Excel hay que ir a buscarlo a la primera
        # fila de `hojas[].items` que matchee (mismo lookup que ya hace
        # `confirmar_importacion_plantillas` para crear el `Ejercicio`).
        return next(
            (
                item["ejercicio_original"]
                for hoja in resultado["hojas"]
                for item in hoja["items"]
                if item["ejercicio_normalizado"] == nombre_normalizado
            ),
            nombre_normalizado,
        )

    def _ejercicios_con_form(self, importacion, ejercicio_formset):
        # Empareja cada form del formset con SU entrada de
        # `ejercicios_distintos` (nombre original, candidato sugerido y
        # score si es un match ambiguo) -- antes el template solo mostraba
        # el nombre normalizado y el form crudo, sin ese contexto (fix
        # post-review, hallazgo 6). Se busca por valor de campo (no por
        # índice) para que funcione tanto con el formset recién armado
        # desde `initial` (GET) como con uno reconstruido desde
        # `request.POST` tras un error de validación.
        resultado = importacion.resultado
        ejercicios_distintos = resultado["ejercicios_distintos"]
        filas = []
        for f in ejercicio_formset.forms:
            nombre_normalizado = f["nombre_normalizado"].value()
            info = dict(ejercicios_distintos.get(nombre_normalizado, {}))
            info["nombre_original"] = self._nombre_original(resultado, nombre_normalizado)
            filas.append((info, f))
        return filas

    def render(self, request, importacion, hoja_formset, ejercicio_formset):
        # `hoja_formset.forms` preserva el orden de `hojas_initial`, que a
        # su vez preserva el orden de `resultado["hojas"]` -- zippearlos es
        # lo que le permite al template mostrar cada form junto a SU hoja
        # (indexar a mano, ej. `hoja_formset.forms.0` dentro de un loop,
        # siempre traería el primer form sin importar qué hoja se está
        # renderizando).
        return render(request, self.template_name, {
            "importacion": importacion,
            "hojas_con_form": list(zip(importacion.resultado["hojas"], hoja_formset.forms)),
            "hoja_formset": hoja_formset,
            "ejercicio_formset": ejercicio_formset,
            "ejercicios_con_form": self._ejercicios_con_form(importacion, ejercicio_formset),
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        hoja_formset = HojaMetadataFormSet(request.POST, prefix="form")
        ejercicio_formset = ResolucionEjercicioFormSet(request.POST, prefix="ejercicios")

        if not (hoja_formset.is_valid() and ejercicio_formset.is_valid()):
            return self.render(request, importacion, hoja_formset, ejercicio_formset)

        decisiones = {
            "hojas": [
                {"incluir": f["incluir"], "objetivo": f["objetivo"], "nivel": f["nivel"]}
                for f in hoja_formset.cleaned_data
            ],
            "ejercicios": {
                **{
                    nombre: {"accion": "usar_existente", "ejercicio_id": info["ejercicio_id"]}
                    for nombre, info in importacion.resultado["ejercicios_distintos"].items()
                    if info["tipo"] == "exacto"
                },
                **{
                    f["nombre_normalizado"]: {
                        "accion": f["accion"],
                        "ejercicio_id": f["ejercicio_existente_id"],
                        "grupo_muscular": f["grupo_muscular"],
                    }
                    for f in ejercicio_formset.cleaned_data
                },
            },
        }

        try:
            plantillas = confirmar_importacion_plantillas(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self.render(request, importacion, hoja_formset, ejercicio_formset)

        messages.success(request, f"Se crearon {len(plantillas)} plantilla(s).")
        return redirect("rutinas:plantilla_listado")


class SubirBibliotecaView(StaffRequiredMixin, TenantScopedMixin, FormView):
    form_class = SubirBibliotecaForm
    template_name = "importaciones/biblioteca_subir.html"

    def form_valid(self, form):
        try:
            importacion = previsualizar_importacion_biblioteca(
                gimnasio=self.gimnasio,
                archivo=form.cleaned_data["archivo"],
                usuario=self.request.user,
            )
        except ImportacionInvalida as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("importaciones:biblioteca_preview", pk=importacion.pk)


class PreviewBibliotecaView(StaffRequiredMixin, TenantScopedMixin, View):
    template_name = "importaciones/biblioteca_preview.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.BIBLIOTECA,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _pendientes(self, importacion):
        return [
            item for item in importacion.resultado["items"]
            if item["match"]["tipo"] != "exacto" and not item["grupo_muscular_resuelto"]
        ]

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        return self._render(request, importacion, ResolucionesJSONForm())

    def _render(self, request, importacion, form):
        return render(request, self.template_name, {
            "importacion": importacion,
            "pendientes": self._pendientes(importacion),
            "grupo_muscular_choices": Ejercicio.GrupoMuscular.choices,
            "form": form,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        form = ResolucionesJSONForm(request.POST)
        if not form.is_valid():
            return self._render(request, importacion, form)

        resueltos_a_mano = form.cleaned_data["resoluciones"]
        faltantes = [
            item["nombre_original"] for item in self._pendientes(importacion)
            if item["nombre_normalizado"] not in resueltos_a_mano
        ]
        if faltantes:
            form.add_error(
                None, f"Falta resolver el grupo muscular de: {', '.join(faltantes)}.",
            )
            return self._render(request, importacion, form)

        decisiones = {"items": {
            item["nombre_normalizado"]: {
                "incluir": item["match"]["tipo"] != "exacto",
                "grupo_muscular": (
                    item["grupo_muscular_resuelto"]
                    or resueltos_a_mano.get(item["nombre_normalizado"])
                ),
            }
            for item in importacion.resultado["items"]
        }}

        try:
            creados = confirmar_importacion_biblioteca(
                importacion=importacion, gimnasio=self.gimnasio, decisiones=decisiones,
            )
        except ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return self._render(request, importacion, form)

        messages.success(request, f"Se crearon {len(creados)} ejercicio(s).")
        return redirect("ejercicios:listado")


class DescartarImportacionView(StaffRequiredMixin, TenantScopedMixin, View):
    def post(self, request, *args, **kwargs):
        importacion = get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=kwargs["pk"], estado=Importacion.Estado.EN_REVISION,
        )
        importacion.estado = Importacion.Estado.DESCARTADA
        importacion.save(update_fields=["estado"])
        messages.success(request, "Importación descartada.")
        if importacion.tipo == Importacion.Tipo.BIBLIOTECA:
            return redirect("importaciones:biblioteca_subir")
        return redirect("importaciones:plantillas_subir")
