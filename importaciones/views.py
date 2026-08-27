"""Vistas de gestión del importador (Proyecto 2). Mismo patrón que
`rutinas/views.py`: StaffRequiredMixin + TenantScopedMixin, vistas finas
que delegan toda la lógica a `services.py`."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import FormView, View

from core.mixins import TenantScopedMixin
from ejercicios.models import CategoriaEjercicio
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
        # `form_kwargs` es lo que hace llegar el gimnasio a cada form del
        # formset: sin eso el `ModelChoiceField` de categoría ofrecería las
        # categorías de todos los gimnasios (su queryset default es none(),
        # así que el síntoma sería un desplegable vacío, no una fuga).
        ejercicio_formset = ResolucionEjercicioFormSet(
            initial=ejercicios_initial,
            prefix="ejercicios",
            form_kwargs={"gimnasio": self.gimnasio},
        )
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
            # Las zonas del drag-and-drop: antes iteraban las choices del
            # primer form del formset, que era un catálogo global. Ahora son
            # las categorías del gimnasio.
            "categorias": CategoriaEjercicio.objects.for_gimnasio(
                self.gimnasio
            ).filter(activo=True),
            "ejercicios_con_form": self._ejercicios_con_form(importacion, ejercicio_formset),
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        hoja_formset = HojaMetadataFormSet(request.POST, prefix="form")
        ejercicio_formset = ResolucionEjercicioFormSet(
            request.POST,
            prefix="ejercicios",
            form_kwargs={"gimnasio": self.gimnasio},
        )

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
                        "categoria_id": f["categoria"].pk if f["categoria"] else None,
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
        # Un item pendiente puede necesitar UNA de las dos decisiones, o
        # ambas: `needs_accion` (match ambiguo -- usar existente o crear
        # nuevo) y/o `needs_categoria` (el archivo no traía categoría, o la
        # que traía no se pudo resolver). Se devuelven juntas en una sola
        # lista para que el template arme UNA fila por ejercicio pendiente,
        # no dos secciones separadas que dupliquen filas.
        #
        # `.get("categoria_resuelta")` y no `[...]`: las importaciones que
        # quedaron en revisión antes de esta feature tienen el `resultado`
        # con la forma vieja. La migración las descarta, pero el acceso
        # tolerante evita un 500 si alguna se escapa.
        pendientes = []
        for item in importacion.resultado["items"]:
            tipo = item["match"]["tipo"]
            needs_accion = tipo == "ambiguo"
            needs_categoria = tipo != "exacto" and not item.get("categoria_resuelta")
            if needs_accion or needs_categoria:
                pendientes.append({
                    **item,
                    "needs_accion": needs_accion,
                    "needs_categoria": needs_categoria,
                })
        return pendientes

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        return self._render(request, importacion, ResolucionesJSONForm())

    def _render(self, request, importacion, form):
        return render(request, self.template_name, {
            "importacion": importacion,
            "pendientes": self._pendientes(importacion),
            "categorias": CategoriaEjercicio.objects.for_gimnasio(
                self.gimnasio
            ).filter(activo=True),
            "form": form,
        })

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        form = ResolucionesJSONForm(request.POST)
        if not form.is_valid():
            return self._render(request, importacion, form)

        resoluciones = form.cleaned_data["resoluciones"]
        faltantes = []
        for item in importacion.resultado["items"]:
            tipo = item["match"]["tipo"]
            if tipo == "exacto":
                continue
            entrada = resoluciones.get(item["nombre_normalizado"], {})
            if tipo == "ambiguo":
                accion = entrada.get("accion")
                if accion not in ("usar_existente", "crear_nuevo"):
                    faltantes.append(item["nombre_original"])
                    continue
                if accion == "usar_existente":
                    continue  # no crea nada -> no necesita categoría
            # `sin_categoria` es una elección EXPLÍCITA del staff, no un
            # default silencioso: sin ella no habría forma de confirmar un
            # ejercicio cuya categoría todavía no existe (las que va a crear
            # esta misma importación), ni de importar a un gimnasio cuyo
            # catálogo está vacío -- ahí el desplegable no tiene ninguna
            # opción y la confirmación quedaba trabada para siempre.
            if (
                not item.get("categoria_resuelta")
                and not entrada.get("categoria_id")
                and not entrada.get("sin_categoria")
            ):
                faltantes.append(item["nombre_original"])
        if faltantes:
            form.add_error(None, f"Falta resolver: {', '.join(faltantes)}.")
            return self._render(request, importacion, form)

        decisiones = {"items": {
            item["nombre_normalizado"]: {
                "incluir": (
                    item["match"]["tipo"] != "exacto"
                    and not (
                        item["match"]["tipo"] == "ambiguo"
                        and resoluciones.get(item["nombre_normalizado"], {}).get("accion") == "usar_existente"
                    )
                ),
                # Solo lo que el staff eligió a mano: lo que resolvió el
                # importador ya viaja en `item["categoria_resuelta"]` y lo
                # lee el servicio. Mandar las dos cosas por acá obligaría a
                # decidir la precedencia en dos lugares distintos.
                "categoria_id": resoluciones.get(
                    item["nombre_normalizado"], {}
                ).get("categoria_id"),
                "sin_categoria": resoluciones.get(
                    item["nombre_normalizado"], {}
                ).get("sin_categoria", False),
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
