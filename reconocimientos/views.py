from django.contrib import messages
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from core.mixins import TenantScopedMixin
from reconocimientos.forms import PremioForm
from reconocimientos.models import Premio
from reconocimientos.ranking import calcular_ranking
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin


def formatear_kilos(kilos):
    """80 -> «80», 20.5 -> «20,5»: sin ceros de más y con coma decimal."""
    texto = format(kilos.normalize(), "f")
    return texto.replace(".", ",")


def _presentar(fila):
    return {
        "puesto": fila.puesto,
        "nombre": fila.nombre,
        "kilos": formatear_kilos(fila.kilos),
        "es_propia": fila.es_propia,
    }


class RankingView(AlumnoRequiredMixin, TemplateView):
    """Ranking de kilos por ejercicio entre los compañeros del gimnasio.

    Sólo lectura (también suplantando). La bandera se mira en `get` y NO en
    `dispatch`: un `dispatch` propio correría antes que el de
    `AlumnoRequiredMixin`, sin `self.perfil`, y un anónimo daría 500 en vez de
    ir al login. Un alumno menor de 18 años recibe 200 con un cartel y sin
    bloques (`solo_mayores`). Sin ficha de alumno (`self.alumno is None`) se muestra el
    ranking sin fila propia.
    """

    template_name = "reconocimientos/ranking.html"

    def get(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        alumno = self.alumno
        # Los menores de 18 (o sin fecha de nacimiento) no participan: ven un
        # cartel y ni siquiera se calcula el ranking. Sin ficha, como siempre.
        if alumno is not None and not alumno.es_mayor_de_edad:
            contexto["alumno"] = alumno
            contexto["solo_mayores"] = True
            contexto["bloques"] = []
            contexto["participa"] = False
            return contexto
        bloques = [
            {
                "ejercicio": b.ejercicio,
                "participantes": b.participantes,
                "filas": [_presentar(f) for f in b.filas],
                "tu_fila": _presentar(b.tu_fila) if b.tu_fila else None,
            }
            for b in calcular_ranking(self.gimnasio, alumno=alumno)
        ]
        contexto["alumno"] = alumno
        contexto["bloques"] = bloques
        contexto["participa"] = any(
            b["tu_fila"] or any(f["es_propia"] for f in b["filas"]) for b in bloques
        )
        return contexto


class _PremiosStaffMixin(StaffRequiredMixin, TenantScopedMixin):
    """Pantallas del catálogo de premios (sólo staff). La bandera se mira en
    `get`/`post` y NO en `dispatch`, por lo mismo que en `RankingView`: el
    `dispatch` de `StaffRequiredMixin` tiene que correr primero (403 a un
    alumno, login a un anónimo). Apagada: 404, también para el staff."""

    model = Premio
    success_url = reverse_lazy("reconocimientos:premios")

    def get(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        return super().post(request, *args, **kwargs)


class PremiosListView(_PremiosStaffMixin, ListView):
    """Todos los premios del gimnasio, activos e inactivos (el staff tiene que
    ver los inactivos para reactivarlos)."""

    template_name = "reconocimientos/premios_list.html"
    context_object_name = "premios"


class PremioCreateView(_PremiosStaffMixin, CreateView):
    form_class = PremioForm
    template_name = "reconocimientos/premio_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Premio creado correctamente.")
        return response


class PremioUpdateView(_PremiosStaffMixin, UpdateView):
    """No hay vista de borrado: «eliminar» un premio es destildar `activo`,
    así los premios ya otorgados conservan a qué se refieren."""

    form_class = PremioForm
    template_name = "reconocimientos/premio_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Premio actualizado correctamente.")
        return response
