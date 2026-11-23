from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from core.mixins import TenantScopedMixin
from reconocimientos.forms import PremioForm
from reconocimientos import premios as servicio
from reconocimientos.honor import cuadro_de_honor
from reconocimientos.models import Premio, PremioOtorgado
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
        contexto["cuadro_de_honor"] = cuadro_de_honor(self.gimnasio)
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


#: Días hacia atrás que «Otorgados recientemente» mira (por fin de ciclo).
DIAS_OTORGADOS = 60


class PremiosCicloView(_PremiosStaffMixin, TemplateView):
    """Quiénes cerraron un ciclo con asistencia perfecta y qué se les dio.

    GET lista candidatos (con selector de premio) y otorgados recientes. POST
    `accion` ∈ `dar|anular|entregado`. Todo error de uso es redirect + mensaje
    (nunca 4xx: bajo `hx-boost` un 4xx es un click mudo) y nada se escribe; la
    revalidación real vive en `servicio.otorgar`, esta vista sólo parsea."""

    template_name = "reconocimientos/premios_ciclo.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        hoy = timezone.localdate()
        activos = list(Premio.objects.for_gimnasio(self.gimnasio).filter(activo=True))
        contexto["premios_activos"] = activos
        contexto["premio_por_defecto"] = next(
            (p.pk for p in activos if p.por_defecto), None
        )
        contexto["candidatos"] = servicio.candidatos(self.gimnasio, hoy=hoy)
        contexto["otorgados"] = (
            PremioOtorgado.objects.for_gimnasio(self.gimnasio)
            .filter(periodo_fin__gte=hoy - timedelta(days=DIAS_OTORGADOS))
            .select_related("alumno")
            .annotate(n_aplicaciones=Count("aplicaciones"))
        )
        return contexto

    def post(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        accion = request.POST.get("accion")
        if accion == "dar":
            self._dar(request)
        elif accion in ("anular", "entregado"):
            self._cambiar_estado(request, accion)
        else:
            messages.error(request, "No entendimos qué querías hacer.")
        return redirect("reconocimientos:premios_ciclo")

    @staticmethod
    def _entero(valor):
        """Id del POST, o None si no es un entero positivo que entre en un
        bigint (un `99999999999999999999` reventaría la query: 500)."""
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            return None
        return numero if 0 < numero < 2**63 else None

    def _dar(self, request):
        alumno_id = self._entero(request.POST.get("alumno"))
        premio_id = self._entero(request.POST.get("premio"))
        try:
            inicio = date.fromisoformat(request.POST.get("inicio", ""))
        except ValueError:
            inicio = None
        if alumno_id is None or premio_id is None or inicio is None:
            messages.error(request, "Faltan datos para dar el premio. Elegí un premio y probá de nuevo.")
            return
        otorgado, motivo = servicio.otorgar(
            self.gimnasio, alumno_id, inicio, premio_id, request.user.perfil
        )
        if otorgado is None:
            messages.error(request, motivo)
        elif motivo == servicio.MOTIVO_YA_OTORGADO:
            messages.info(request, motivo)
        else:
            nombre = f"{otorgado.alumno.nombre} {otorgado.alumno.apellido}"
            messages.success(request, f"Premio dado a {nombre}.")

    def _cambiar_estado(self, request, accion):
        otorgado_id = self._entero(request.POST.get("otorgado"))
        if accion == "anular":
            hecho = otorgado_id is not None and servicio.anular(self.gimnasio, otorgado_id)
            ok, no = "Premio anulado.", "No se pudo anular: ya estaba anulado o no existe."
        else:
            hecho = otorgado_id is not None and servicio.marcar_entregado(
                self.gimnasio, otorgado_id
            )
            ok, no = "Premio marcado como entregado.", "No se pudo marcar: ya estaba entregado o no es un obsequio."
        if hecho:
            messages.success(request, ok)
        else:
            messages.error(request, no)
