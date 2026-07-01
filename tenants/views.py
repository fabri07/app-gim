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
    """Dashboard mínimo: confirma que el login y el scoping por gimnasio
    funcionan de punta a punta. El dashboard real (alumnos activos, pagos
    pendientes, etc.) es de Fase 2 del ROADMAP."""

    template_name = "tenants/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context["perfil"] = self.request.user.perfil
        except ObjectDoesNotExist:
            raise PermissionDenied(
                "Tu usuario no tiene un Perfil asociado a un Gimnasio."
            )
        return context
