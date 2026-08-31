"""
Vistas de gestión (Fase 2) de rutinas: CRUD de plantillas, alta/edición/borrado
de sus items, duplicar plantilla, y asignación de una plantilla a un alumno.

Todas son solo para staff (`StaffRequiredMixin`) y siempre acotadas al
gimnasio del usuario. Para `RutinaPlantilla` y `RutinaAsignada` (ambas
`TenantOwnedModel`) eso lo da `TenantScopedMixin.get_queryset()` de siempre.

Los items (`RutinaPlantillaItem`) NO son `TenantOwnedModel` -- no tienen
`for_gimnasio()` propio (ver docstring de `rutinas.models`). El aislamiento de
tenant para sus vistas se logra resolviendo PRIMERO la `RutinaPlantilla`
padre con `RutinaPlantilla.objects.for_gimnasio(gimnasio)`: si la plantilla es
de otro gimnasio, eso ya devuelve 404 antes de tocar ningún item.
`ItemPlantillaMixin` centraliza esa resolución.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from rutinas import progreso, services
from rutinas.agrupacion import listar_ejercicios_del_dia
from rutinas.forms import (
    AgregarEjercicioAsignadoForm,
    AsignarRutinaForm,
    RutinaAsignadaItemForm,
    RutinaPlantillaForm,
    RutinaPlantillaItemForm,
)
from rutinas.models import (
    SEMANAS_POR_CICLO,
    RutinaAsignada,
    RutinaAsignadaDiaCompletado,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)
from rutinas.pdf import generar_pdf_rutina_asignada
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin


# ---------------------------------------------------------------------------
# Plantillas
# ---------------------------------------------------------------------------


class RutinaPlantillaListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = RutinaPlantilla
    template_name = "rutinas/plantilla_list.html"
    context_object_name = "plantillas"


class RutinaPlantillaCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = RutinaPlantilla
    form_class = RutinaPlantillaForm
    template_name = "rutinas/plantilla_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Plantilla creada correctamente.")
        return response

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.object.pk])


class RutinaPlantillaUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = RutinaPlantilla
    form_class = RutinaPlantillaForm
    template_name = "rutinas/plantilla_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Plantilla actualizada correctamente.")
        return response

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.object.pk])


class RutinaPlantillaDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    model = RutinaPlantilla
    template_name = "rutinas/plantilla_detail.html"
    context_object_name = "plantilla"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Ya vienen ordenados por dia/orden (Meta.ordering del modelo).
        context["items"] = self.object.items.select_related("ejercicio").all()
        return context


class RutinaPlantillaDuplicarView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """POST-only: crea una copia independiente de la plantilla (y sus items)
    vía `RutinaPlantilla.duplicar()`. GET no está permitido -- duplicar es una
    acción que muta datos, no una vista de solo lectura (ver ROADMAP Fase 2
    §4, "duplicar rutina existente")."""

    model = RutinaPlantilla

    def post(self, request, *args, **kwargs):
        plantilla = self.get_object()
        copia = plantilla.duplicar()
        messages.success(request, f'Se duplicó "{plantilla.nombre}".')
        return redirect("rutinas:plantilla_detalle", pk=copia.pk)


# ---------------------------------------------------------------------------
# Items de una plantilla
# ---------------------------------------------------------------------------


class ItemPlantillaMixin(StaffRequiredMixin, TenantScopedMixin):
    """Mixin común a los views de `RutinaPlantillaItem`.

    Resuelve la `RutinaPlantilla` padre (URL kwarg `plantilla_pk`) acotada al
    gimnasio del staff logueado -- ESE lookup es lo que aísla por tenant,
    porque 404ea antes de que se llegue a consultar ningún item. A partir de
    ahí se opera siempre sobre `self.plantilla.items`, nunca sobre
    `RutinaPlantillaItem.objects` directo (que no tiene scoping propio).
    """

    def get_plantilla(self):
        if not hasattr(self, "_plantilla"):
            self._plantilla = get_object_or_404(
                RutinaPlantilla.objects.for_gimnasio(self.gimnasio),
                pk=self.kwargs["plantilla_pk"],
            )
        return self._plantilla

    @property
    def plantilla(self):
        return self.get_plantilla()

    def get_queryset(self):
        # Reemplaza a TenantScopedMixin.get_queryset(): RutinaPlantillaItem no
        # es TenantOwnedModel y no tiene `for_gimnasio()`.
        return self.plantilla.items.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["plantilla"] = self.plantilla
        return context

    def get_success_url(self):
        return reverse("rutinas:plantilla_detalle", args=[self.plantilla.pk])


class RutinaPlantillaItemCreateView(ItemPlantillaMixin, CreateView):
    model = RutinaPlantillaItem
    form_class = RutinaPlantillaItemForm
    template_name = "rutinas/item_form.html"

    def form_valid(self, form):
        form.instance.rutina = self.plantilla
        self.object = form.save()
        messages.success(self.request, "Ejercicio agregado a la plantilla.")
        return redirect(self.get_success_url())


class RutinaPlantillaItemUpdateView(ItemPlantillaMixin, UpdateView):
    model = RutinaPlantillaItem
    form_class = RutinaPlantillaItemForm
    template_name = "rutinas/item_form.html"

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Ejercicio actualizado.")
        return redirect(self.get_success_url())


class RutinaPlantillaItemDeleteView(ItemPlantillaMixin, View):
    """POST-only: no hay página de confirmación por GET, el botón de borrar
    ya es la confirmación (ver template `plantilla_detail.html`)."""

    def post(self, request, *args, **kwargs):
        item = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        item.delete()
        messages.success(request, "Ejercicio eliminado de la plantilla.")
        return redirect(self.get_success_url())


# ---------------------------------------------------------------------------
# Asignación de rutina
# ---------------------------------------------------------------------------


class AsignarRutinaView(StaffRequiredMixin, TenantScopedMixin, FormView):
    """`RutinaAsignada` no se crea vía `form.save()` (no es un `ModelForm`):
    la crea `RutinaAsignada.crear_desde_plantilla`. Igual se mezcla con
    `TenantScopedMixin` porque su `get_form_kwargs()` inyecta `gimnasio` sin
    importar qué tipo de form sea -- solo que no se usa su `get_queryset()`
    (no aplica a un `FormView`). `form_valid()` está completamente reescrito
    y no llama a `super()`, que asume un `ModelForm` con `form.instance`."""

    form_class = AsignarRutinaForm
    template_name = "rutinas/asignar_form.html"

    def get_initial(self):
        # Prefill opcional desde el link "Asignar a un alumno" del detalle de
        # una plantilla (?plantilla=<pk>) -- pura comodidad de UX, el form
        # sigue validando que la plantilla exista dentro del queryset scopeado.
        initial = super().get_initial()
        plantilla_pk = self.request.GET.get("plantilla")
        if plantilla_pk:
            initial["plantilla"] = plantilla_pk
        return initial

    def form_valid(self, form):
        asignada = RutinaAsignada.crear_desde_plantilla(
            gimnasio=self.gimnasio,
            alumno=form.cleaned_data["alumno"],
            plantilla=form.cleaned_data["plantilla"],
            fecha_inicio=form.cleaned_data["fecha_inicio"],
        )
        messages.success(self.request, "Rutina asignada correctamente.")
        return redirect("rutinas:asignada_detalle", pk=asignada.pk)


class RutinaAsignadaDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """El plan del alumno, agrupado por día igual que lo ve él.

    Hasta 2026-08-31 esta vista listaba TODOS los items en una sola tabla
    plana (una fila por ejercicio Y por semana, ~128 filas en una rutina
    real). Se reagrupó al sumarle la edición: "quitar" actúa sobre las 4
    semanas, y en la tabla plana ese botón habría aparecido repetido en las 4
    filas, cada una prometiendo borrar solo la suya. Reusar
    `listar_ejercicios_del_dia` -- el punto único de esta lógica, que ya
    alimentaba el portal del alumno y el PDF -- hace además que el staff y el
    alumno vean exactamente la misma grilla.
    """

    model = RutinaAsignada
    template_name = "rutinas/asignada_detail.html"
    context_object_name = "asignada"

    def get_queryset(self):
        return super().get_queryset().select_related("alumno")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Una sola query trae TODOS los items; el agrupado por día y el cruce
        # con la adherencia se hacen en Python sobre esa lista. El costo en
        # queries no puede crecer con la cantidad de items ni de días -- hay
        # un test de escala que lo fija.
        items = list(self.object.items.all())
        semana_actual = self.object.semana_actual
        semanas = list(range(1, SEMANAS_POR_CICLO + 1))

        previstas = progreso.sesiones_previstas_de(items)
        entrenadas = progreso.sesiones_entrenadas(self.object)
        context["adherencia"] = progreso.adherencia_de_rutina(
            self.object, previstas=previstas, entrenadas=entrenadas
        )

        dias = []
        for numero in sorted({item.dia for item in items}):
            del_dia = [item for item in items if item.dia == numero]
            ejercicios = progreso.anotar_senales(
                listar_ejercicios_del_dia(
                    del_dia, semanas=semanas, semana_actual=semana_actual
                )
            )
            dias.append(
                {
                    "numero": numero,
                    # Misma regla "gana la semana más baja" que ya usan
                    # `agrupacion.py` y `RutinaMiDiaDetailView` para este
                    # campo denormalizado.
                    "nombre": next(
                        (
                            item.dia_nombre
                            for item in sorted(del_dia, key=lambda i: i.semana)
                            if item.dia_nombre
                        ),
                        "",
                    ),
                    "semanas_meta": [
                        {
                            "numero": semana,
                            "es_actual": semana == semana_actual,
                            "tiene_items": (numero, semana) in previstas,
                            "entrenada": (numero, semana) in entrenadas,
                        }
                        for semana in semanas
                    ],
                    "ejercicios": ejercicios,
                }
            )
        context["dias"] = dias

        # Aviso de rutinas activas duplicadas. No arregla el bug (nadie setea
        # `activa=False` al asignar una rutina nueva, ver ISSUES.md), pero lo
        # vuelve visible justo donde importa: sin esto el entrenador puede
        # estar editando una rutina que el alumno no ve, porque el portal
        # muestra la más reciente por `Meta.ordering`.
        context["activas_del_alumno"] = self.object.alumno.rutinas_asignadas.filter(
            activa=True
        ).count()
        return context


# ---------------------------------------------------------------------------
# Edición de la rutina asignada (los items del snapshot)
# ---------------------------------------------------------------------------


class ItemAsignadaMixin(StaffRequiredMixin, TenantScopedMixin):
    """Mismo mecanismo de aislamiento que `ItemPlantillaMixin`, para el otro
    par de modelos.

    `RutinaAsignadaItem` tampoco es `TenantOwnedModel` y no tiene
    `for_gimnasio()` propio: se resuelve PRIMERO la `RutinaAsignada` padre
    (URL kwarg `asignada_pk`) acotada al gimnasio del staff, y ESE lookup es
    lo que aísla, porque 404ea antes de que se llegue a consultar ningún item.
    De ahí en más se opera siempre sobre `self.asignada.items`, nunca sobre
    `RutinaAsignadaItem.objects` (que no tiene scoping propio).
    """

    def get_asignada(self):
        if not hasattr(self, "_asignada"):
            self._asignada = get_object_or_404(
                RutinaAsignada.objects.for_gimnasio(self.gimnasio).select_related(
                    "alumno"
                ),
                pk=self.kwargs["asignada_pk"],
            )
        return self._asignada

    @property
    def asignada(self):
        return self.get_asignada()

    def get_queryset(self):
        # Reemplaza a TenantScopedMixin.get_queryset(): RutinaAsignadaItem no
        # es TenantOwnedModel y no tiene `for_gimnasio()`.
        return self.asignada.items.all()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["asignada"] = self.asignada
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["asignada"] = self.asignada
        context["adherencia"] = progreso.adherencia_de_rutina(self.asignada)
        return context

    def get_success_url(self):
        return reverse("rutinas:asignada_detalle", args=[self.asignada.pk])


class RutinaAsignadaItemUpdateView(ItemAsignadaMixin, UpdateView):
    """Editar un ejercicio de una semana. La escritura la hace el servicio:
    nombre y video van a las 4 semanas, el resto solo a esta."""

    model = RutinaAsignadaItem
    form_class = RutinaAsignadaItemForm
    template_name = "rutinas/asignada_item_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Lo que el alumno reportó en ESTE ejercicio, semana por semana: es la
        # información con la que el entrenador decide si subir o bajar la
        # carga, y hasta ahora no la veía en ningún lado.
        context["historial"] = progreso.historial_rpe(
            services.hermanos(self.asignada, self.object)
        )
        return context

    def form_valid(self, form):
        datos = form.cleaned_data
        try:
            semanas = services.editar_ejercicio_asignado(
                asignada=self.asignada,
                item=self.object,
                ejercicio_nombre=datos["ejercicio_nombre_snapshot"],
                ejercicio_video=datos["ejercicio_video_snapshot"],
                series=datos["series"],
                repeticiones=datos["repeticiones"],
                kilos=datos["kilos"],
                descanso=datos["descanso"],
                notas=datos["notas"],
                bloque=datos["bloque"],
            )
        except services.NombreDuplicadoEnElDia:
            # El form ya valida esto; se revalida bajo lock en el servicio por
            # si dos ediciones entran a la vez. Si igual llegó acá, se muestra
            # como error de campo en vez de un 500.
            form.add_error(
                "ejercicio_nombre_snapshot",
                "Ya hay otro ejercicio con ese nombre en este día.",
            )
            return self.form_invalid(form)

        messages.success(
            self.request,
            f"Se actualizó «{datos['ejercicio_nombre_snapshot']}». El nombre y el "
            f"video se aplicaron a las {semanas} semanas del ciclo; series, "
            f"kilos y notas, solo a la semana {self.object.semana}.",
        )
        return redirect(self.get_success_url())


class RutinaAsignadaItemCreateView(ItemAsignadaMixin, CreateView):
    """Sumar un ejercicio de la biblioteca a un día. Crea una fila por cada
    semana que ese día ya tiene."""

    model = RutinaAsignadaItem
    form_class = AgregarEjercicioAsignadoForm
    template_name = "rutinas/asignada_item_form.html"

    @property
    def dia(self):
        dia = self.kwargs["dia"]
        # 404 y no 403: ese día "no existe" en esta rutina. Mismo criterio que
        # `RutinaMiDiaDetailView` y `RutinaAsignadaDiaCompletadoToggleView`.
        if not self.asignada.items.filter(dia=dia).exists():
            raise Http404("La rutina no tiene ese día.")
        return dia

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["dia"] = self.dia
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["dia"] = self.dia
        return context

    def form_valid(self, form):
        datos = form.cleaned_data
        try:
            creados = services.agregar_ejercicio_asignado(
                asignada=self.asignada,
                dia=self.dia,
                ejercicio=datos["ejercicio"],
                series=datos["series"],
                repeticiones=datos["repeticiones"],
                kilos=datos["kilos"],
                descanso=datos["descanso"],
                notas=datos["notas"],
                bloque=datos["bloque"],
            )
        except services.NombreDuplicadoEnElDia:
            form.add_error("ejercicio", "Ese ejercicio ya está en este día.")
            return self.form_invalid(form)

        messages.success(
            self.request,
            f"Se agregó «{datos['ejercicio'].nombre}» al día {self.dia} en "
            f"{len(creados)} semanas.",
        )
        return redirect(self.get_success_url())


class RutinaAsignadaItemDeleteView(ItemAsignadaMixin, View):
    """POST-only: no hay página de confirmación por GET, el botón ya es la
    confirmación y dice explícitamente que borra las 4 semanas (mismo criterio
    que `RutinaPlantillaItemDeleteView`)."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        item = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        nombre = item.ejercicio_nombre_snapshot
        borradas = services.quitar_ejercicio_asignado(
            asignada=self.asignada, item=item
        )
        messages.success(
            request,
            f"Se quitó «{nombre}» del día {item.dia} en {borradas} semanas.",
        )
        return redirect(self.get_success_url())


class RutinaAsignadaPdfView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Descarga en PDF de una rutina asignada -- pensado para cuando un
    alumno se queda sin acceso a su portal (p. ej. olvidó el celular) y el
    staff/dueño necesita imprimirle o pasarle el plan de otra forma. No es
    un `DetailView`: no renderiza un template, devuelve bytes binarios."""

    model = RutinaAsignada

    def get(self, request, *args, **kwargs):
        asignada = self.get_object()
        pdf_bytes = generar_pdf_rutina_asignada(asignada)
        filename = f"rutina-{slugify(str(asignada.alumno))}-{asignada.pk}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class RutinaAsignadaItemCalificarView(AlumnoRequiredMixin, View):
    """El alumno califica el RPE de un ejercicio de su rutina ACTIVA.

    Solo POST -- es una escritura, mismo criterio que
    `novedades.views.NovedadMarcarLeidaView`. El item se busca acotado a
    `rutina_asignada__alumno` Y `rutina_asignada__activa=True`: calificar una
    rutina vieja/cerrada no tiene sentido (el staff ya no la está ajustando
    en base a ese feedback), así que un intento contra un item de una
    asignación inactiva da 404, igual que uno de otro alumno -- no es un
    tema de permisos, es que ese item "no existe" para calificar desde la
    perspectiva del alumno.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        item = get_object_or_404(
            RutinaAsignadaItem.objects.filter(
                rutina_asignada__alumno=self.alumno,
                rutina_asignada__activa=True,
            ),
            pk=kwargs["pk"],
        )
        valor = request.POST.get("rpe", "")
        if valor not in RutinaAsignadaItem.RPE.values:
            messages.error(request, "Esa opción de RPE no es válida.")
            return redirect("rutinas:mi_dia_detalle", dia=item.dia)
        item.rpe = valor
        item.save(update_fields=["rpe"])
        messages.success(request, "Gracias, guardamos tu feedback.")
        return redirect("rutinas:mi_dia_detalle", dia=item.dia)


class RutinaMiDiaDetailView(AlumnoRequiredMixin, View):
    """El alumno ve, para UN día de entrenamiento de su rutina activa, las
    4 semanas del ciclo lado a lado -- la vista inversa de la tabla de
    "esta semana" del dashboard (`HomeView._portal_alumno`), pensada para
    seguir la progresión de un mismo día semana a semana en vez de ver
    todos los días mezclados en una sola semana.

    404 si el alumno no tiene rutina activa, o si `dia` no es uno de los
    que esa rutina realmente tiene cargados -- mismo criterio que
    `RutinaAsignadaItemCalificarView`: no es un tema de permisos, ese día
    "no existe" desde la perspectiva de este alumno.
    """

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if self.alumno is None:
            raise Http404
        dia = kwargs["dia"]
        rutina_actual = self.alumno.rutinas_asignadas.filter(activa=True).first()
        if rutina_actual is None:
            raise Http404
        if dia not in set(rutina_actual.items.values_list("dia", flat=True)):
            raise Http404

        semanas_completadas = set(
            rutina_actual.dias_completados.filter(dia=dia).values_list(
                "semana", flat=True
            )
        )
        semanas_con_items = set(
            rutina_actual.items.filter(dia=dia).values_list("semana", flat=True)
        )

        semanas_meta = [
            {
                "numero": semana,
                "completada": semana in semanas_completadas,
                "es_actual": semana == rutina_actual.semana_actual,
                "tiene_items": semana in semanas_con_items,
            }
            for semana in range(1, SEMANAS_POR_CICLO + 1)
        ]

        ejercicios = listar_ejercicios_del_dia(
            rutina_actual.items.filter(dia=dia),
            semanas=[s["numero"] for s in semanas_meta],
            semana_actual=rutina_actual.semana_actual,
        )

        return render(
            request,
            "rutinas/mi_dia_detalle.html",
            {
                "rutina_actual": rutina_actual,
                "dia": dia,
                # `dia_nombre` está denormalizado en cada item; alcanza con el
                # primero que lo tenga cargado (los items de un mismo día
                # comparten el valor, lo escribe el importador de una).
                "dia_nombre": next(
                    (e["dia_nombre"] for e in ejercicios if e["dia_nombre"]), ""
                ),
                "semanas_meta": semanas_meta,
                "ejercicios": ejercicios,
                "rpe_choices": RutinaAsignadaItem.RPE.choices,
            },
        )


class RutinaAsignadaDiaCompletadoToggleView(AlumnoRequiredMixin, View):
    """El alumno marca (o desmarca) como entrenado un día de una semana
    puntual del ciclo. Toggle y no solo "marcar": un click de más no debe
    dejar al alumno sin forma de deshacerlo.

    El chequeo de existencia es por `dia` Y `semana` (no solo `dia`): una
    semana del ciclo sin ningún ejercicio cargado para este día no debe
    poder marcarse como entrenada, aunque el día sí tenga ejercicios en
    otras semanas -- mismo criterio 404 que el resto de las vistas del
    alumno (`RutinaMiDiaDetailView`, `RutinaAsignadaItemCalificarView`)."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        dia = kwargs["dia"]
        semana = kwargs["semana"]
        rutina_actual = self.alumno.rutinas_asignadas.filter(activa=True).first()
        if rutina_actual is None or not rutina_actual.items.filter(
            dia=dia, semana=semana
        ).exists():
            raise Http404

        borrados, _ = RutinaAsignadaDiaCompletado.objects.filter(
            rutina_asignada=rutina_actual, dia=dia, semana=semana
        ).delete()
        if not borrados:
            RutinaAsignadaDiaCompletado.objects.create(
                rutina_asignada=rutina_actual, dia=dia, semana=semana
            )
        return redirect("rutinas:mi_dia_detalle", dia=dia)
