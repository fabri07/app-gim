"""Vistas de asistencia (staff)."""

from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView, View

from alumnos.models import Alumno
from asistencia.forms import RegistrarAsistenciaForm
from asistencia.models import Asistencia
from asistencia.services import MAX_DIAS_ATRAS, dias_por_fuente
from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from turnos.models import Reserva


class MarcarAsistenciaReservaView(StaffRequiredMixin, TenantScopedMixin, View):
    """Marca (o quita) «asistió» de una reserva de la agenda. Sólo POST.

    La bandera se mira en `post` y NO en `dispatch` (un `dispatch` propio
    correría antes que el login y un anónimo vería un 404 en vez del login).
    La reserva se busca acotada al gimnasio: una de otro da 404.

    Los errores de uso (fecha futura, muy vieja, alumno inactivo) van como
    `messages.warning` + redirect, nunca 4xx: bajo el hx-boost global un 4xx
    es un click mudo."""

    http_method_names = ["post"]

    def post(self, request, pk):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        reserva = get_object_or_404(
            Reserva.objects.for_gimnasio(self.gimnasio).select_related("alumno"),
            pk=pk,
        )
        accion = request.POST.get("accion")
        if accion == "quitar":
            self._quitar(request, reserva)
        elif accion == "marcar":
            self._marcar(request, reserva)
        else:
            messages.warning(request, "No se entendió qué hacer con la asistencia.")
        return redirect(self._volver())

    def _fecha_valida(self, request, reserva):
        hoy = timezone.localdate()
        if reserva.fecha > hoy:
            messages.warning(
                request, "Todavía no pasó el turno: no se puede marcar la asistencia."
            )
            return False
        if reserva.fecha < hoy - timedelta(days=MAX_DIAS_ATRAS):
            messages.warning(
                request,
                f"Sólo se puede cargar asistencia de los últimos {MAX_DIAS_ATRAS} días.",
            )
            return False
        return True

    def _marcar(self, request, reserva):
        if not self._fecha_valida(request, reserva):
            return
        if reserva.alumno.estado != Alumno.Estado.ACTIVO:
            messages.warning(
                request, "El alumno está inactivo: no se le puede marcar asistencia."
            )
            return
        try:
            with transaction.atomic():
                _, creada = Asistencia.objects.get_or_create(
                    gimnasio=self.gimnasio,
                    alumno=reserva.alumno,
                    fecha=reserva.fecha,
                    defaults={
                        "origen": Asistencia.Origen.TURNO,
                        "hora": reserva.hora_inicio,
                        "registrada_por": request.user.perfil,
                    },
                )
        except IntegrityError:
            creada = False  # doble envío simultáneo: ya la marcó el otro
        if creada:
            messages.success(request, f"Asistencia de {reserva.alumno} marcada.")
        else:
            messages.info(request, f"{reserva.alumno} ya tenía la asistencia marcada.")

    def _quitar(self, request, reserva):
        if not self._fecha_valida(request, reserva):
            return
        Asistencia.objects.for_gimnasio(self.gimnasio).filter(
            alumno=reserva.alumno, fecha=reserva.fecha
        ).delete()
        messages.success(request, f"Asistencia de {reserva.alumno} quitada.")

    def _volver(self):
        url = reverse("turnos:agenda")
        try:
            semana = int(self.request.POST.get("semana", ""))
        except (TypeError, ValueError):
            return url
        return f"{url}?semana={semana}"


@method_decorator(never_cache, name="dispatch")
class RegistrarAsistenciaView(StaffRequiredMixin, TenantScopedMixin, View):
    """Carga a mano un día de asistencia desde la ficha del alumno. Sólo POST.

    Un error del formulario NO es un 4xx (bajo hx-boost es un click mudo):
    se re-renderiza la ficha con 200 y la línea de error junto al campo. La
    marca es idempotente: la unicidad (gimnasio, alumno, fecha) la sostiene la
    base y un doble envío simultáneo no da 500."""

    http_method_names = ["post"]

    def post(self, request, pk):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        alumno = get_object_or_404(
            Alumno.objects.for_gimnasio(self.gimnasio), pk=pk
        )
        form = RegistrarAsistenciaForm(request.POST)
        if not form.is_valid():
            return self._ficha_con_errores(request, alumno, form)
        if alumno.estado != Alumno.Estado.ACTIVO:
            messages.warning(
                request, "El alumno está inactivo: no se le puede marcar asistencia."
            )
            return redirect(self._ficha_url(alumno))
        try:
            with transaction.atomic():
                _, creada = Asistencia.objects.get_or_create(
                    gimnasio=self.gimnasio,
                    alumno=alumno,
                    fecha=form.cleaned_data["fecha"],
                    defaults={
                        "origen": Asistencia.Origen.MANUAL,
                        "registrada_por": request.user.perfil,
                    },
                )
        except IntegrityError:
            creada = False
        if creada:
            messages.success(request, f"Asistencia de {alumno} registrada.")
        else:
            messages.info(request, f"{alumno} ya tenía ese día marcado.")
        return redirect(self._ficha_url(alumno))

    @staticmethod
    def _ficha_url(alumno):
        return reverse("alumnos:detalle", args=[alumno.pk]) + "#asistencia"

    def _ficha_con_errores(self, request, alumno, form):
        # Import tardío: `alumnos.views` no importa `asistencia` a nivel módulo.
        from alumnos.views import AlumnoDetailView

        vista = AlumnoDetailView()
        vista.setup(request, pk=alumno.pk)
        vista.object = alumno
        contexto = vista.get_context_data(object=alumno, asistencia_form=form)
        return vista.render_to_response(contexto)


class QuitarAsistenciaView(StaffRequiredMixin, TenantScopedMixin, View):
    """Quita una marca del staff. Sólo POST. Nunca toca ingresos del lector."""

    http_method_names = ["post"]

    def post(self, request, pk):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        marca = get_object_or_404(
            Asistencia.objects.for_gimnasio(self.gimnasio).select_related("alumno"),
            pk=pk,
        )
        alumno = marca.alumno
        marca.delete()
        messages.success(request, f"Asistencia de {alumno} quitada.")
        return redirect(RegistrarAsistenciaView._ficha_url(alumno))


#: Alumnos por página en «Pasar lista» y tope de ids que acepta un POST.
POR_PAGINA_LISTA = 200


@method_decorator(never_cache, name="dispatch")
class PasarListaView(StaffRequiredMixin, TenantScopedMixin, TemplateView):
    """Pasar lista de un día: un tilde por alumno activo.

    GET `?fecha=&q=&pagina=`; POST guarda. La bandera se mira en `get`/`post`
    (no en `dispatch`, que correría antes del login). Los errores de uso son
    `messages` + redirect, nunca 4xx.

    El POST sólo actúa sobre los ids de la página que se mostró (campo oculto
    `ids`, lista separada por comas): crea las marcas de los tildados que no
    la tenían y borra las de los destildados que sí. Los días del lector no
    son filas `Asistencia`, así que nunca se tocan (y esas filas ni llevan
    casilla)."""

    template_name = "asistencia/pasar_lista.html"

    def get(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        return super().get(request, *args, **kwargs)

    # --- fecha -----------------------------------------------------------
    def _fecha_o_error(self, texto):
        """`(fecha, None)` o `(None, mensaje)`."""
        try:
            fecha = datetime.strptime((texto or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            return None, "Esa fecha no es válida: elegí un día del calendario."
        hoy = timezone.localdate()
        if fecha > hoy:
            return None, "No se puede pasar lista de un día que todavía no pasó."
        if fecha < hoy - timedelta(days=MAX_DIAS_ATRAS):
            return None, (
                f"Sólo se puede cargar asistencia de los últimos {MAX_DIAS_ATRAS} días."
            )
        return fecha, None

    def _alumnos_activos(self, q=""):
        qs = Alumno.objects.for_gimnasio(self.gimnasio).filter(
            estado=Alumno.Estado.ACTIVO
        )
        q = (q or "").strip()
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(apellido__icontains=q))
        return qs.order_by("apellido", "nombre", "pk")

    # --- GET -------------------------------------------------------------
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        fecha = None
        texto = request.GET.get("fecha")
        if texto:
            fecha, error = self._fecha_o_error(texto)
            if error:
                messages.warning(request, error)
        fecha = fecha or timezone.localdate()
        q = (request.GET.get("q") or "").strip()

        paginador = Paginator(self._alumnos_activos(q), POR_PAGINA_LISTA)
        try:
            numero = int(request.GET.get("pagina", "1"))
        except ValueError:
            numero = 1
        pagina = paginador.get_page(numero)
        alumnos = list(pagina.object_list)

        ventanas = {a.pk: (fecha, fecha) for a in alumnos}
        lector, marcas = dias_por_fuente(self.gimnasio, ventanas)
        filas = []
        for a in alumnos:
            marcada = fecha in marcas[a.pk]
            filas.append(
                {
                    "alumno": a,
                    "marcada": marcada,
                    "por_lector": fecha in lector[a.pk] and not marcada,
                }
            )
        context.update(
            fecha=fecha,
            q=q,
            filas=filas,
            pagina=pagina,
            ids=",".join(str(a.pk) for a in alumnos),
            hoy=timezone.localdate(),
            desde=timezone.localdate() - timedelta(days=MAX_DIAS_ATRAS),
        )
        return context

    # --- POST ------------------------------------------------------------
    @staticmethod
    def _enteros(valores):
        pks = set()
        for texto in valores:
            for parte in str(texto).split(","):
                parte = parte.strip()
                if parte.isdigit():
                    pks.add(int(parte))
        return pks

    def post(self, request, *args, **kwargs):
        if not self.gimnasio.reconocimientos_habilitado:
            raise Http404
        fecha, error = self._fecha_o_error(request.POST.get("fecha"))
        if error:
            messages.warning(request, error)
            return redirect(self._volver(None))
        enviados = self._enteros(request.POST.getlist("ids"))
        tildados = self._enteros(request.POST.getlist("marcados")) & enviados
        # Sólo cuentan los activos del gimnasio: el resto de los ids se ignora.
        validos = set(
            Alumno.objects.for_gimnasio(self.gimnasio)
            .filter(estado=Alumno.Estado.ACTIVO, pk__in=enviados)
            .values_list("pk", flat=True)
        )
        tildados &= validos
        destildados = validos - tildados
        existentes = set(
            Asistencia.objects.for_gimnasio(self.gimnasio)
            .filter(fecha=fecha, alumno_id__in=validos)
            .values_list("alumno_id", flat=True)
        )
        nuevos = tildados - existentes
        quitar = destildados & existentes
        with transaction.atomic():
            if nuevos:
                # `ignore_conflicts`: un doble envío simultáneo no da 500.
                Asistencia.objects.bulk_create(
                    [
                        Asistencia(
                            gimnasio=self.gimnasio,
                            alumno_id=pk,
                            fecha=fecha,
                            origen=Asistencia.Origen.MANUAL,
                            registrada_por=request.user.perfil,
                        )
                        for pk in nuevos
                    ],
                    ignore_conflicts=True,
                )
            if quitar:
                Asistencia.objects.for_gimnasio(self.gimnasio).filter(
                    fecha=fecha, alumno_id__in=quitar
                ).delete()
        messages.success(
            request, f"{len(nuevos)} marcadas, {len(quitar)} quitadas."
        )
        return redirect(self._volver(fecha))

    def _volver(self, fecha):
        datos = {}
        if fecha is not None:
            datos["fecha"] = fecha.isoformat()
        q = (self.request.POST.get("q") or "").strip()
        if q:
            datos["q"] = q
        pagina = self.request.POST.get("pagina", "")
        if pagina.isdigit() and int(pagina) > 1:
            datos["pagina"] = pagina
        url = reverse("asistencia:pasar_lista")
        return f"{url}?{urlencode(datos)}" if datos else url
