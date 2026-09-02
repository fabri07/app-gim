"""Vistas de gestión (Fase 2) de la biblioteca de ejercicios de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). No hay vista de borrado: `activo=False` es la forma de
"retirar" un ejercicio de uso activo sin romper `RutinaAsignada` items que lo
referencian con `on_delete=PROTECT` (ver docstring de `Ejercicio`).
"""

from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.db.models import Count, Q
from django.views.generic import CreateView, ListView, UpdateView

from core.mixins import TenantScopedMixin
from core.views import BorrarConExplicacionView
from tenants.mixins import StaffRequiredMixin
from ejercicios.forms import CategoriaEjercicioForm, EjercicioForm
from ejercicios.models import CategoriaEjercicio, Ejercicio


class EjercicioListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Ejercicio
    template_name = "ejercicios/ejercicio_list.html"
    context_object_name = "ejercicios"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("categoria")
        # El filtro viaja por id, no por texto: las categorías son por
        # gimnasio, así que un slug global ya no identifica nada.
        self.categoria_actual = None
        categoria_id = self.request.GET.get("categoria", "").strip()
        if categoria_id.isdigit():
            self.categoria_actual = (
                CategoriaEjercicio.objects.for_gimnasio(self.gimnasio)
                .filter(pk=categoria_id)
                .first()
            )
            if self.categoria_actual is not None:
                queryset = queryset.filter(categoria=self.categoria_actual)
        self.q = self.request.GET.get("q", "").strip()
        if self.q:
            queryset = queryset.filter(nombre__icontains=self.q)
        # Filtro por video. Un valor inesperado no filtra nada en vez de
        # romper: el parámetro viene de la URL y puede editarse a mano.
        self.video_actual = self.request.GET.get("video", "").strip()
        if self.video_actual == "sin":
            queryset = queryset.filter(url_video="")
        elif self.video_actual == "con":
            queryset = queryset.exclude(url_video="")
        else:
            self.video_actual = ""
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Todas, no solo las activas: el staff tiene que poder filtrar por una
        # categoría que desactivó para encontrar los ejercicios que quedaron
        # colgados de ella (mismo criterio que `MedioCobroListView`).
        context["categorias"] = CategoriaEjercicio.objects.for_gimnasio(
            self.gimnasio
        )
        context["categoria_actual"] = self.categoria_actual
        context["q_actual"] = self.q
        context["video_actual"] = self.video_actual
        # Sobre la BIBLIOTECA entera, no sobre el queryset filtrado: si el
        # conteo cambiara al filtrar, buscar un ejercicio que sí tiene video
        # diría "0 sin video" y parecería que está todo cargado.
        #
        # Una sola query con `Count(filter=...)`, no dos: este listado ya trae
        # la biblioteca completa de un gimnasio (748 ejercicios en el caso
        # real) y no es lugar para sumar consultas.
        totales = Ejercicio.objects.for_gimnasio(self.gimnasio).aggregate(
            sin_video=Count("pk", filter=Q(url_video="")),
            con_video=Count("pk", filter=~Q(url_video="")),
        )
        context["sin_video_count"] = totales["sin_video"]
        context["con_video_count"] = totales["con_video"]
        return context


class EjercicioCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Ejercicio
    form_class = EjercicioForm
    template_name = "ejercicios/ejercicio_form.html"
    success_url = reverse_lazy("ejercicios:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Ejercicio creado correctamente.")
        return response


class EjercicioUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Ejercicio
    form_class = EjercicioForm
    template_name = "ejercicios/ejercicio_form.html"
    success_url = reverse_lazy("ejercicios:listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Ejercicio actualizado correctamente.")
        return response


class EjercicioDeleteView(
    StaffRequiredMixin, TenantScopedMixin, BorrarConExplicacionView
):
    """`RutinaPlantillaItem.ejercicio` es `PROTECT`: un ejercicio que ya está
    en una plantilla no se borra, y no debería -- borrarlo dejaría la
    plantilla incompleta. La salida es `activo=False`, que lo saca de los
    desplegables sin tocar lo ya armado."""

    model = Ejercicio
    alternativa = (
        "Si ya no lo usás, editalo y destildá «Activo»: deja de aparecer al "
        "armar rutinas y las plantillas que lo usan siguen intactas."
    )

    def get_titulo(self):
        return f"Eliminar el ejercicio «{self.object.nombre}»"

    def get_mensaje_exito(self):
        return "Ejercicio eliminado."

    def get_url_exito(self):
        return reverse("ejercicios:listado")


class CategoriaListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Lista TODAS las categorías, activas e inactivas: el staff necesita ver
    las inactivas para poder reactivarlas (mismo criterio que
    `MedioCobroListView`)."""

    model = CategoriaEjercicio
    template_name = "ejercicios/categoria_list.html"
    context_object_name = "categorias"

    def get_queryset(self):
        return super().get_queryset().annotate(total_ejercicios=Count("ejercicios"))


class CategoriaCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = CategoriaEjercicio
    form_class = CategoriaEjercicioForm
    template_name = "ejercicios/categoria_form.html"
    success_url = reverse_lazy("ejercicios:categorias_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Categoría creada correctamente.")
        return response


class CategoriaUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """No hay `CategoriaDeleteView`: "eliminar" una categoría es editarla acá
    y destildar `activo`. Los ejercicios que ya la tienen la conservan; lo que
    cambia es que deja de ofrecerse para asignar."""

    model = CategoriaEjercicio
    form_class = CategoriaEjercicioForm
    template_name = "ejercicios/categoria_form.html"
    success_url = reverse_lazy("ejercicios:categorias_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Categoría actualizada correctamente.")
        return response
