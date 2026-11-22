from django.http import Http404
from django.views.generic import TemplateView

from reconocimientos.ranking import calcular_ranking
from tenants.mixins import AlumnoRequiredMixin


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
    ir al login. Sin ficha de alumno (`self.alumno is None`) se muestra el
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
