"""Vistas de gestión del importador (Proyecto 2). Mismo patrón que
`rutinas/views.py`: StaffRequiredMixin + TenantScopedMixin, vistas finas
que delegan toda la lógica a `services.py`."""

import json

from django.contrib import messages
from django.http import HttpResponse
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
    construir_ejemplo_plantillas,
    hojas_elegidas,
    confirmar_importacion_biblioteca,
    confirmar_importacion_plantillas,
    previsualizar_importacion_biblioteca,
    previsualizar_importacion_plantillas,
)
from tenants.mixins import StaffRequiredMixin


class EjemploPlantillasView(StaffRequiredMixin, TenantScopedMixin, View):
    """Descarga un `.xlsx` de ejemplo listo para llenar.

    Se genera al vuelo (no es un binario versionado, que se desincronizaría
    del parser en silencio). `hx-boost="false"` en el link que lleva acá es
    obligatorio: htmx intercepta el click y se traga la descarga.
    """

    def get(self, request, *args, **kwargs):
        respuesta = HttpResponse(
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        )
        respuesta["Content-Disposition"] = (
            'attachment; filename="ejemplo-plan-de-entrenamiento.xlsx"'
        )
        construir_ejemplo_plantillas().save(respuesta)
        return respuesta


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
        return redirect("importaciones:plantillas_hojas", pk=importacion.pk)


class SeleccionHojasView(StaffRequiredMixin, TenantScopedMixin, View):
    """Paso intermedio: qué hojas del archivo son planes de entrenamiento.

    Un workbook real no trae solo el plan. El del primer cliente pago tiene 7
    hojas y 6 son auxiliares (`AUX` con 3206 filas, `Movilidad Articular` con
    1020, `Avatar`, `Logros`, `Carga de Datos`, `Plantilla - aux`); sin este
    paso el preview mostraba 7 tarjetas y el staff tenía que destildar 6.

    La elección se guarda como una lista de nombres dentro de
    `resultado["hojas_elegidas"]`: sin campo de modelo nuevo, sin estado
    nuevo, y **sin volver a abrir el archivo** -- la invariante que documenta
    `importaciones/models.py`. Ya está todo parseado desde el paso anterior.
    """

    template_name = "importaciones/plantillas_hojas.html"

    def get_importacion(self):
        return get_object_or_404(
            Importacion.objects.for_gimnasio(self.gimnasio),
            pk=self.kwargs["pk"],
            tipo=Importacion.Tipo.PLANTILLAS,
            estado=Importacion.Estado.EN_REVISION,
        )

    def _filas(self, importacion):
        elegidas = importacion.resultado.get("hojas_elegidas")
        for hoja in importacion.resultado["hojas"]:
            items = hoja["items"]
            # Por defecto se ofrecen tildadas solo las que de verdad parsearon
            # algo: es lo que hace que el staff no tenga que destildar seis
            # hojas auxiliares para llegar a la única que le importa.
            marcada = (
                hoja["nombre_hoja"] in elegidas if elegidas is not None else bool(items)
            )
            yield {
                "nombre_hoja": hoja["nombre_hoja"],
                "cantidad": len(items),
                "dias": hoja["dias_por_semana"],
                "semanas": len({i["semana"] for i in items}),
                "motivo_exclusion": hoja["motivo_exclusion"],
                "marcada": marcada,
            }

    def _contexto(self, importacion, **extra):
        filas = list(self._filas(importacion))
        return {
            "importacion": importacion,
            "filas": filas,
            # Sin ninguna hoja con ejercicios no hay nada que elegir: ofrecer
            # "Continuar" sería pedirle al staff algo que la pantalla no puede
            # dar, y cualquier POST volvería con "elegí al menos una hoja".
            "sin_hojas_importables": not any(f["cantidad"] for f in filas),
            **extra,
        }

    def get(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        return render(request, self.template_name, self._contexto(importacion))

    def post(self, request, *args, **kwargs):
        importacion = self.get_importacion()
        nombres_reales = {h["nombre_hoja"] for h in importacion.resultado["hojas"]}
        con_items = {
            h["nombre_hoja"] for h in importacion.resultado["hojas"] if h["items"]
        }
        # Se intersecta contra los nombres REALES en vez de confiar en el POST:
        # misma barrera que el re-fetch scopeado del resto del importador.
        elegidas = [
            n for n in request.POST.getlist("hojas") if n in nombres_reales and n in con_items
        ]

        if not elegidas:
            return render(request, self.template_name, self._contexto(
                importacion,
                error="Elegí al menos una hoja con ejercicios para poder seguir.",
            ))

        importacion.resultado = {**importacion.resultado, "hojas_elegidas": elegidas}
        importacion.save(update_fields=["resultado"])
        return redirect("importaciones:plantillas_preview", pk=importacion.pk)


def _agrupar_invalidas(hoja):
    """`[(fila_excel, [motivos])]`, en el orden en que aparecen.

    En una tabla con las semanas a lo ancho, una misma fila de Excel produce
    hasta un item por semana, así que puede fallar en más de una. Listarla
    repetida haría parecer que hay más problemas de los que hay.
    """
    agrupadas = {}
    for fila in hoja["filas_invalidas"]:
        agrupadas.setdefault(fila["fila_excel"], []).append(fila["motivo"])
    return list(agrupadas.items())


def _nombres_de_las_hojas_elegidas(resultado):
    """Nombres normalizados de los ejercicios de las hojas que se van a
    importar.

    `ejercicios_distintos` se calcula sobre el archivo entero, así que incluye
    los de las hojas que el staff dejó sin marcar. Pedirle que clasifique un
    ejercicio que no se va a crear es trabajo por nada -- y con un archivo de
    varias hojas auxiliares, mucho trabajo por nada.
    """
    return {
        item["ejercicio_normalizado"]
        for hoja in hojas_elegidas(resultado)
        for item in hoja["items"]
    }


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
            for h in hojas_elegidas(resultado)
        ]
        # Fuera de la comprehension: adentro se reconstruía el set entero una
        # vez POR EJERCICIO, o sea O(distintos x items) en una pantalla que ya
        # tiene el presupuesto de 30 s de gunicorn documentado como riesgo.
        nombres_a_resolver = _nombres_de_las_hojas_elegidas(resultado)
        ejercicios_initial = [
            {
                "nombre_normalizado": nombre,
                "accion": "usar_existente" if info["tipo"] in ("exacto", "ambiguo") else "crear_nuevo",
                "ejercicio_existente_id": info.get("ejercicio_id") or info.get("candidato_id"),
            }
            for nombre, info in resultado["ejercicios_distintos"].items()
            if info["tipo"] != "exacto"  # los exactos no requieren decisión del staff
            and nombre in nombres_a_resolver
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
            "hojas_con_form": list(zip(
                [
                    {**h, "invalidas_agrupadas": _agrupar_invalidas(h)}
                    for h in hojas_elegidas(importacion.resultado)
                ],
                hoja_formset.forms,
            )),
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
            # `nombre_hoja` viaja en un hidden del form y es lo que parea
            # cada decisión con SU hoja. Viene del cliente, así que
            # `confirmar_importacion_plantillas` lo revalida contra los
            # nombres reales de esta importación antes de usarlo.
            "hojas": [
                {
                    "nombre_hoja": f["nombre_hoja"],
                    "incluir": f["incluir"],
                    "objetivo": f["objetivo"],
                    "nivel": f["nivel"],
                }
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

    def _opciones_categoria(self, importacion):
        """Todo lo que el staff puede elegir para un pendiente: las
        categorías que el gimnasio YA tiene, más las que ESTA importación va
        a crear al confirmar.

        Las nuevas todavía no existen en la base (el preview no escribe), así
        que no tienen pk y viajan por nombre, con el prefijo `nueva:` para
        que el POST las pueda distinguir de un id. Sin ellas, un gimnasio que
        importa por primera vez -- catálogo vacío -- no tenía NINGUNA
        categoría donde ubicar una fila que el archivo trajo sin clasificar:
        la única salida era «Sin categoría» y arreglarlo después a mano, uno
        por uno (reporte del 2026-08-27, gimnasio "Vida Plena").
        """
        opciones = [
            {"valor": str(c.pk), "etiqueta": c.nombre}
            for c in CategoriaEjercicio.objects.for_gimnasio(
                self.gimnasio
            ).filter(activo=True)
        ]
        opciones += [
            {"valor": f"nueva:{nombre}", "etiqueta": nombre, "es_nueva": True}
            for nombre in importacion.resultado.get("categorias_a_crear", [])
        ]
        return opciones

    def _render(self, request, importacion, form):
        return render(request, self.template_name, {
            "importacion": importacion,
            "pendientes": self._pendientes(importacion),
            "opciones_categoria": self._opciones_categoria(importacion),
            # Lo que el staff ya había elegido, para que un POST rechazado no
            # le borre el trabajo: las decisiones viven solo en el blob JSON
            # que arma el JS, así que sin devolverlas los desplegables vuelven
            # todos a "---------". Con un archivo de 748 filas eso significa
            # rehacer decenas de elecciones por un único pendiente olvidado.
            "resoluciones_previas": self._resoluciones_previas(form),
            "form": form,
        })

    @staticmethod
    def _resoluciones_previas(form):
        """El dict de resoluciones tal como vino en el POST, o `{}` en un GET.

        Se lee de `form.data` y no de `cleaned_data` a propósito: el rechazo
        puede venir del propio `clean()` (JSON mal formado) o de la
        validación semántica de la vista, y solo el segundo caso deja algo
        en `cleaned_data`. El crudo del POST está en los dos.
        """
        try:
            datos = json.loads(form.data.get("resoluciones") or "{}")
        except (AttributeError, json.JSONDecodeError, TypeError):
            return {}
        return datos if isinstance(datos, dict) else {}

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
                and not entrada.get("categoria_nueva")
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
                # Una categoría que esta misma importación va a crear: no
                # tiene id todavía, así que se elige por nombre. El servicio
                # lo valida contra `categorias_a_crear` antes de crear nada.
                "categoria_nueva": resoluciones.get(
                    item["nombre_normalizado"], {}
                ).get("categoria_nueva"),
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
