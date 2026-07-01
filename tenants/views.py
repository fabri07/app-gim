"""
Vista de registro: alta self-serve de un gimnasio nuevo (rol staff/dueño), y
vista de inicio (dashboard) tras login.

El registro crea User + Gimnasio + Perfil de forma ATÓMICA (si algo falla, no
queda un usuario sin gimnasio ni un gimnasio huérfano) y deja al usuario
logueado. Adaptado de ~/gestor-pedidos/tenants/views.py.
"""

from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import transaction
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import FormView, TemplateView

from tenants.forms import RegistroForm
from tenants.models import Gimnasio, Perfil


class RegisterView(FormView):
    form_class = RegistroForm
    template_name = "registration/register.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            nombre = form.cleaned_data["nombre_gimnasio"]
            gimnasio = Gimnasio.objects.create(
                nombre=nombre,
                slug=self._slug_disponible(nombre),
            )
            Perfil.objects.create(
                usuario=user, gimnasio=gimnasio, rol=Perfil.Rol.STAFF
            )
        login(self.request, user)
        return super().form_valid(form)

    @staticmethod
    def _slug_disponible(nombre):
        base = slugify(nombre) or "gimnasio"
        slug = base
        sufijo = 1
        while Gimnasio.objects.filter(slug=slug).exists():
            sufijo += 1
            slug = f"{base}-{sufijo}"
        return slug


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

        try:
            alumno = perfil.alumno
        except ObjectDoesNotExist:
            alumno = None

        if alumno is None:
            return {
                "alumno": None,
                "rutina_actual": None,
                "mensualidad_actual": None,
                "ultimas_novedades": Novedad.objects.for_gimnasio(
                    perfil.gimnasio
                ).visibles()[:5],
            }

        hoy = timezone.now().date()

        return {
            "alumno": alumno,
            "rutina_actual": alumno.rutinas_asignadas.filter(activa=True).first(),
            "mensualidad_actual": alumno.pagos.filter(
                mes=hoy.month, anio=hoy.year
            ).first(),
            "ultimas_novedades": Novedad.objects.for_gimnasio(
                perfil.gimnasio
            ).visibles()[:5],
        }

    @staticmethod
    def _metricas_dashboard(gimnasio):
        from alumnos.models import Alumno
        from novedades.models import Novedad
        from pagos.models import PagoMensual
        from rutinas.models import RutinaAsignada

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
            "ultimas_novedades": Novedad.objects.for_gimnasio(gimnasio).visibles()[:5],
        }
