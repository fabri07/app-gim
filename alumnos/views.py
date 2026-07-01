"""Vistas de gestión (Fase 2) de los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El "activar/inactivar" es una acción POST-only
separada (nunca una vista de borrado): un `Alumno` nunca se borra, solo
cambia de estado (queda como historial para pagos y rutinas ya emitidos).

`CrearAccesoView`/`CambiarPasswordAlumnoView` (Fase 3) reemplazan el
magic-link original del ROADMAP: el staff asigna usuario/contraseña a mano
desde la ficha del alumno (ver ISSUES.md 2026-07-01). Ninguna usa los hooks
de form de `TenantScopedMixin` (`get_form_kwargs`/`form_valid` esperan un
`ModelForm` con `.instance`; `CrearAccesoForm`/`CambiarPasswordAlumnoForm`
son `forms.Form` planos) — se maneja el form a mano, como
`AlumnoToggleEstadoView`, y se reutiliza `TenantScopedMixin` solo por
`get_queryset`/`self.gimnasio` (aislamiento de tenant vía `SingleObjectMixin.
get_object`).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from tenants.models import Perfil
from alumnos.forms import AlumnoForm, CambiarPasswordAlumnoForm, CrearAccesoForm
from alumnos.models import Alumno


class AlumnoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Alumno
    template_name = "alumnos/alumno_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        queryset = super().get_queryset()
        self.estado_actual = self.request.GET.get("estado", "")
        if self.estado_actual in (Alumno.Estado.ACTIVO, Alumno.Estado.INACTIVO):
            queryset = queryset.filter(estado=self.estado_actual)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estado_actual"] = self.estado_actual
        return context


class AlumnoCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno creado correctamente.")
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


class AlumnoUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    model = Alumno
    form_class = AlumnoForm
    template_name = "alumnos/alumno_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Alumno actualizado correctamente.")
        return response

    def get_success_url(self):
        return reverse("alumnos:detalle", args=[self.object.pk])


class AlumnoDetailView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """La "ficha" del alumno: sus datos + secciones de solo lectura de sus
    pagos (app `pagos`) y su rutina activa (app `rutinas`)."""

    model = Alumno
    template_name = "alumnos/alumno_detail.html"
    context_object_name = "alumno"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pagos"] = self.object.pagos.all()
        context["rutina_actual"] = self.object.rutinas_asignadas.filter(
            activa=True
        ).first()
        return context


class AlumnoToggleEstadoView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Flip activo <-> inactivo. POST-only: muta estado, nunca debe
    dispararse con un GET (link, prefetch, etc)."""

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.estado == Alumno.Estado.ACTIVO:
            alumno.estado = Alumno.Estado.INACTIVO
        else:
            alumno.estado = Alumno.Estado.ACTIVO
        alumno.save(update_fields=["estado"])
        messages.success(
            request, f"{alumno} ahora está {alumno.get_estado_display().lower()}."
        )
        return redirect("alumnos:detalle", pk=alumno.pk)


class CrearAccesoView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Alta del login del alumno: solo para alumnos SIN `Alumno.perfil`
    todavía. Si ya tiene uno, redirige a la ficha sin tocar nada — para
    resetear una contraseña existente está `CambiarPasswordAlumnoView`."""

    model = Alumno
    template_name = "alumnos/acceso_form.html"

    def get(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        form = CrearAccesoForm(initial={"username": self._sugerir_username(alumno)})
        return self._render(request, alumno, form)

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        form = CrearAccesoForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            with transaction.atomic():
                user = get_user_model().objects.create_user(
                    username=username, password=password
                )
                perfil = Perfil.objects.create(
                    usuario=user, gimnasio=alumno.gimnasio, rol=Perfil.Rol.ALUMNO
                )
                alumno.perfil = perfil
                alumno.save(update_fields=["perfil"])
            messages.success(
                request,
                f"Acceso creado. Usuario: {username} — Contraseña: {password}. "
                "Comunicáselo a tu alumno; no vas a poder ver la contraseña de "
                "nuevo.",
            )
            return redirect("alumnos:detalle", pk=alumno.pk)
        return self._render(request, alumno, form)

    def _render(self, request, alumno, form):
        return render(
            request,
            self.template_name,
            {"form": form, "alumno": alumno, "modo": "crear"},
        )

    @staticmethod
    def _sugerir_username(alumno):
        """Mismo patrón que `tenants/views.py::RegisterView._slug_disponible`,
        adaptado a `User.username` (único global, no por gimnasio -- ver
        `CrearAccesoForm.clean_username`)."""
        base = slugify(f"{alumno.nombre}.{alumno.apellido}") or "alumno"
        User = get_user_model()
        username = base
        sufijo = 1
        while User.objects.filter(username=username).exists():
            sufijo += 1
            username = f"{base}{sufijo}"
        return username


class CambiarPasswordAlumnoView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Reseteo de contraseña: solo alcanzable si el alumno YA tiene
    `Alumno.perfil` (si no, no hay nada para resetear; usar
    `CrearAccesoView`)."""

    model = Alumno
    template_name = "alumnos/acceso_form.html"

    def get(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is None:
            messages.error(request, "Este alumno todavía no tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        return self._render(request, alumno, CambiarPasswordAlumnoForm())

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is None:
            messages.error(request, "Este alumno todavía no tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        form = CambiarPasswordAlumnoForm(request.POST)
        if form.is_valid():
            password = form.cleaned_data["password"]
            usuario = alumno.perfil.usuario
            usuario.set_password(password)
            usuario.save()
            messages.success(
                request,
                f"Contraseña actualizada. Nueva contraseña: {password}. "
                "Comunicáselo a tu alumno; no vas a poder ver la contraseña de "
                "nuevo.",
            )
            return redirect("alumnos:detalle", pk=alumno.pk)
        return self._render(request, alumno, form)

    def _render(self, request, alumno, form):
        return render(
            request,
            self.template_name,
            {"form": form, "alumno": alumno, "modo": "cambiar"},
        )
