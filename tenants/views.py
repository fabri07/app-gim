"""
Vista de inicio (dashboard) tras login, personalización del gimnasio y landing
pública.

El alta de gimnasios NO vive acá: el registro self-serve se cerró y ahora se
hace con `manage.py crear_gimnasio` (ver `tenants/services.py`).
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants import suplantacion
from tenants.forms import GimnasioForm
from tenants.mixins import StaffRequiredMixin
from tenants.models import Gimnasio, Perfil


class HomeView(LoginRequiredMixin, TemplateView):
    """Página de inicio tras login.

    Para `staff` es el dashboard de Fase 2 §1 (alumnos activos, alumnos con
    pago pendiente, pagos del mes, rutinas activas, últimas novedades). Para
    `alumno` es el portal de Fase 3 (su rutina activa, el estado de su
    mensualidad del mes y las últimas novedades del gimnasio) — no calcula
    métricas de gestión, solo su propia información.

    Import tardío (dentro del método, no a nivel de módulo) de los modelos de
    `alumnos`/`ejercicios`/`rutinas`/`pagos`/`novedades`: `tenants` es una app
    de más abajo en el orden de dependencia (ver `config/settings.py`) y no
    debería depender de las apps de dominio a nivel de import — solo esta
    vista, que por definición agrega datos de todas ellas, las necesita.
    """

    template_name = "tenants/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            perfil = self.request.user.perfil
        except ObjectDoesNotExist:
            raise PermissionDenied(
                "Tu usuario no tiene un Perfil asociado a un Gimnasio."
            )
        context["perfil"] = perfil

        if perfil.rol == Perfil.Rol.STAFF:
            context.update(self._metricas_dashboard(perfil.gimnasio))
        elif perfil.rol == Perfil.Rol.ALUMNO:
            context.update(self._portal_alumno(perfil))
        return context

    @staticmethod
    def _portal_alumno(perfil):
        """Datos del portal de Fase 3: rutina activa, cuota del mes y novedades.

        `perfil.alumno` puede no existir todavía (un `Perfil` ALUMNO se puede
        crear antes de vincularlo a un `Alumno` concreto, o el vínculo se
        pudo perder por `on_delete=SET_NULL`); en ese caso no hay nada de
        dominio que mostrar, así que devolvemos `alumno=None` y el template
        renderiza un estado vacío en vez de 500.
        """
        from novedades.models import Novedad
        from pagos.models import MedioCobro
        from rutinas.models import RutinaAsignadaItem

        try:
            alumno = perfil.alumno
        except ObjectDoesNotExist:
            alumno = None

        if alumno is None:
            # Sin `Alumno` vinculado no hay a quién dirigirle personales: solo
            # los broadcasts del gimnasio (`alumno` nulo).
            return {
                "alumno": None,
                "rutina_actual": None,
                "mensualidad_actual": None,
                "ultimas_novedades": Novedad.objects.for_gimnasio(perfil.gimnasio)
                .visibles()
                .filter(alumno__isnull=True)[:5],
                "ids_novedades_leidas": set(),
            }

        hoy = timezone.localdate()
        rutina_actual = alumno.rutinas_asignadas.filter(activa=True).first()

        items_semana_actual = []
        semana_mostrada = None
        if rutina_actual is not None:
            semana_mostrada = rutina_actual.semana_actual
            items_semana_actual = list(
                rutina_actual.items.filter(semana=semana_mostrada)
            )
            if not items_semana_actual:
                # Rutinas de antes de la progresión semanal (o plantillas
                # nuevas todavía sin cargar más allá de semana 1) tienen
                # todos sus items en semana=1 (default del campo). Sin este
                # fallback, un alumno con `fecha_inicio` de hace una semana o
                # más vería la tabla vacía para siempre (`semana_actual`
                # clampea en 4 y no vuelve a bajar). `semana_mostrada` se
                # actualiza junto con el fallback para que el header
                # ("Semana N de 4") coincida SIEMPRE con lo que la tabla
                # realmente muestra.
                semana_mostrada = 1
                items_semana_actual = list(rutina_actual.items.filter(semana=1))

        return {
            "alumno": alumno,
            "rutina_actual": rutina_actual,
            "items_semana_actual": items_semana_actual,
            "semana_mostrada": semana_mostrada,
            "rpe_choices": RutinaAsignadaItem.RPE.choices,
            "mensualidad_actual": alumno.pagos.filter(
                mes=hoy.month, anio=hoy.year
            ).first(),
            "ultimas_novedades": Novedad.objects.for_gimnasio(perfil.gimnasio)
            .visibles()
            .para_alumno(alumno)[:5],
            "ids_novedades_leidas": set(
                alumno.novedades_leidas.values_list("novedad_id", flat=True)
            ),
            "medios_cobro": MedioCobro.objects.for_gimnasio(
                perfil.gimnasio
            ).filter(activo=True),
        }

    @staticmethod
    def _metricas_dashboard(gimnasio):
        from alumnos.models import Alumno
        from novedades.models import Novedad
        from pagos.models import PagoMensual
        from rutinas.models import RutinaAsignada
        from tenants import analitica

        hoy = timezone.now().date()

        return {
            "alumnos_activos_count": Alumno.objects.for_gimnasio(gimnasio)
            .filter(estado=Alumno.Estado.ACTIVO)
            .count(),
            "alumnos_pago_pendiente_count": Alumno.objects.for_gimnasio(gimnasio)
            .filter(pagos__estado=PagoMensual.Estado.PENDIENTE)
            .distinct()
            .count(),
            "pagos_del_mes": PagoMensual.objects.for_gimnasio(gimnasio).filter(
                mes=hoy.month, anio=hoy.year
            ),
            "rutinas_activas_count": RutinaAsignada.objects.for_gimnasio(gimnasio)
            .filter(activa=True)
            .count(),
            # Solo broadcasts en el dashboard del staff: las personales son de
            # un alumno puntual, no del panel de gestión (Parte B).
            "ultimas_novedades": Novedad.objects.for_gimnasio(gimnasio)
            .visibles()
            .filter(alumno__isnull=True)[:5],
            # Analítica (subproyecto 4): asistencia, género, RPE por ejercicio.
            "asistencia": analitica.asistencia_por_dia_y_hora(gimnasio),
            "genero_stats": analitica.distribucion_por_genero(gimnasio),
            "rpe_por_ejercicio": analitica.rpe_por_ejercicio(gimnasio),
        }


class GimnasioUpdateView(StaffRequiredMixin, UpdateView):
    """Personalización white-label (Fase 4): logo, colores, texto de
    bienvenida, contacto y redes. Sin pk en la URL a propósito -- no es
    "editar el gimnasio <pk>", es "editar MI gimnasio"; `get_object` ignora
    cualquier pk y siempre devuelve el del `Perfil` logueado, así que no
    hace falta `TenantScopedMixin` (no hay otro gimnasio que se pueda
    alcanzar por esta vista)."""

    form_class = GimnasioForm
    template_name = "tenants/gimnasio_form.html"
    success_url = reverse_lazy("gimnasio_editar")

    def get_object(self, queryset=None):
        return self.request.user.perfil.gimnasio

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Única fuente de verdad (Gimnasio.TIPOGRAFIA_FUENTES) expuesta al
        # preview en vivo del template -- evita que el JS reinvente el
        # mapeo tipografía -> familia CSS por su cuenta.
        context["tipografia_fuentes"] = Gimnasio.TIPOGRAFIA_FUENTES
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Datos del gimnasio actualizados.")
        return response


class GimnasioLandingView(DetailView):
    """Landing pública de un gimnasio (subproyecto 5): la primera vista del
    proyecto sin ningún mixin de autenticación -- accesible por cualquiera,
    logueado o no.

    Sin subdominios por gimnasio (principio no negociable del proyecto): la
    URL se resuelve por `Gimnasio.slug`, que ya existía desde Fase 1 sin
    ningún uso público hasta ahora.

    `get_queryset` filtra `activo=True` para que un gimnasio desactivado (o
    un slug que nunca existió) dé 404 -- no tiene sentido publicitar la
    landing de un gimnasio que ya no opera, y un 404 no revela si el slug
    alguna vez existió.

    No hay alta de leads propia ni formulario de contacto: el staff asigna
    usuario/contraseña a mano (ver `alumnos/views.py::CrearAccesoView`), así
    que un visitante nuevo no puede autoregistrarse como alumno -- la
    landing solo ofrece contactar al gimnasio (WhatsApp/Instagram/teléfono,
    campos que ya existían) o, si ya es alumno, ir al login de siempre.
    """

    model = Gimnasio
    template_name = "tenants/landing.html"
    context_object_name = "gimnasio"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return Gimnasio.objects.filter(activo=True)


class SuplantarView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Entrar como un alumno. POST-only: cambia quién sos en la sesión.

    El queryset sale de `TenantScopedMixin`, así que un alumno de otro
    gimnasio da 404 sin siquiera llegar al servicio. El resto de las reglas
    (que sea un alumno, que esté activo, que no sea una cuenta con
    privilegios, que no se anide) las valida `tenants/suplantacion.py`.

    Import tardío de `Alumno`: `tenants` está más abajo que `alumnos` en el
    orden de dependencia de las apps, mismo criterio que `HomeView`.
    """

    http_method_names = ["post"]

    def get_queryset(self):
        from alumnos.models import Alumno

        return Alumno.objects.for_gimnasio(self.gimnasio)

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        suplantacion.iniciar(request, alumno)
        messages.info(request, f"Estás viendo la app como {alumno}.")
        return redirect("home")


class VolverDeSuplantacionView(LoginRequiredMixin, View):
    """Volver a la cuenta del staff.

    NO usa `StaffRequiredMixin` a propósito: mientras dura la suplantación el
    usuario de la sesión es el ALUMNO, así que exigir rol staff dejaría al
    staff atrapado en la cuenta del alumno sin forma de salir.

    La autorización real la hace `suplantacion.volver()`, que revalida contra
    la base y es fail-closed.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        suplantacion.volver(request)
        messages.success(request, "Volviste a tu cuenta.")
        return redirect("home")
