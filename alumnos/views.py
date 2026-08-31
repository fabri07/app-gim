"""Vistas de gestión (Fase 2) de los alumnos de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). El "activar/inactivar" es una acción POST-only
separada (nunca una vista de borrado): un `Alumno` nunca se borra, solo
cambia de estado (queda como historial para pagos y rutinas ya emitidos).

`CrearAccesoView`/`RegenerarPasswordView` reemplazan el magic-link original
del ROADMAP: el staff da de alta el acceso desde la ficha del alumno (ver
ISSUES.md 2026-07-01). El staff elige con qué dato entra el alumno (email o
teléfono) pero NO la contraseña: la genera la app y se muestra una sola vez.
Ninguna de las dos usa los hooks de form de `TenantScopedMixin`
(`get_form_kwargs`/`form_valid` esperan un `ModelForm` con `.instance`;
`CrearAccesoForm` es un `forms.Form` plano) — se maneja el form a mano, como
`AlumnoToggleEstadoView`, y se reutiliza `TenantScopedMixin` solo por
`get_queryset`/`self.gimnasio` (aislamiento de tenant vía `SingleObjectMixin.
get_object`).
"""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants.mixins import StaffRequiredMixin
from alumnos import identidad
from alumnos import services as servicios
from alumnos.forms import AlumnoForm, CrearAccesoForm
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


class AccesoListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Vista de conjunto de los accesos del gimnasio.

    Cuelga del listado de alumnos y no del nav: el nav ya tiene 8 ítems y hubo
    un esfuerzo deliberado por acortarlo de 10 a 8 (mismo criterio que el
    importador de Excel, que también se accede desde su listado).

    `select_related` no es una micro-optimización: cada fila lee el username y
    el último ingreso, que viven dos saltos más allá (`alumno.perfil.usuario`),
    así que sin esto la vista hace una query por alumno. Cubierto por
    `PanelAccesosTests.test_no_hace_una_query_por_alumno`.
    """

    model = Alumno
    template_name = "alumnos/acceso_list.html"
    context_object_name = "alumnos"

    def get_queryset(self):
        return super().get_queryset().select_related("perfil__usuario")


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
        from rutinas.models import RutinaAsignada

        context["pagos"] = self.object.pagos.all()
        context["rutina_actual"] = RutinaAsignada.vigente_de(alumno=self.object)
        context["rutina_proxima"] = RutinaAsignada.proxima_de(alumno=self.object)
        # El historial: hasta ahora la ficha era el ÚNICO acceso a una rutina
        # asignada y solo linkeaba la actual. Desde que los planes conviven en
        # vez de archivarse, sin esta lista no habría forma de llegar a los
        # anteriores.
        context["rutinas_del_alumno"] = self.object.rutinas_asignadas.all()[:10]
        return context


class AlumnoToggleEstadoView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Flip activo <-> inactivo. POST-only: muta estado, nunca debe
    dispararse con un GET (link, prefetch, etc).

    El acceso del alumno es un ESPEJO de su estado: dar de baja apaga
    `User.is_active` y reactivar lo devuelve. Esa sincronización NO se hace
    acá sino en `alumnos/signals.py::sincronizar_acceso_con_estado`, porque
    `estado` también se escribe desde el form de la ficha y desde
    `crear_acceso` — repetirla en cada vista garantiza que alguna se olvide.

    No hace falta invalidar sesiones a mano: `ModelBackend.get_user()` llama a
    `user_can_authenticate()` en CADA request, así que apagar `is_active`
    también mata la sesión que el alumno ya tuviera abierta.
    """

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        alumno.estado = (
            Alumno.Estado.ACTIVO
            if alumno.estado != Alumno.Estado.ACTIVO
            else Alumno.Estado.INACTIVO
        )
        # El `atomic` sigue haciendo falta aunque la sincronización se haya
        # mudado a la señal: son dos escrituras (el estado y el `is_active`
        # del receiver) y si la segunda falla no puede quedar commiteada la
        # primera — sería exactamente la divergencia que este frente elimina.
        with transaction.atomic():
            alumno.save(update_fields=["estado"])

        messages.success(
            request, f"{alumno} ahora está {alumno.get_estado_display().lower()}."
        )
        return redirect("alumnos:detalle", pk=alumno.pk)


def _render_credenciales(request, alumno, password, modo):
    """Pantalla de "esto se ve una sola vez", compartida por el alta y la
    regeneración.

    Vive a nivel de módulo y no como método de una de las dos vistas para que
    ninguna tenga que alcanzar dentro de la otra.

    `never_cache` no es paranoia: esta pantalla se abre en la computadora del
    mostrador del gimnasio, que es compartida. Sin `no-store`, la contraseña
    queda recuperable con el botón "atrás" del navegador después de que el
    staff siguió con otra cosa.
    """
    respuesta = render(
        request,
        "alumnos/acceso_credenciales.html",
        {
            "alumno": alumno,
            "usuario": alumno.perfil.usuario.username,
            "password": password,
            "modo": modo,
        },
    )
    add_never_cache_headers(respuesta)
    return respuesta


class CrearAccesoView(StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View):
    """Alta del login del alumno: solo para alumnos SIN `Alumno.perfil`.

    El POST exitoso NO redirige: renderiza la credencial en un 200. Es a
    propósito y no es un descuido de PRG. `messages` se serializa en la
    SESIÓN, que en este proyecto vive en la base de datos, así que mandar la
    contraseña por ahí la deja escrita en una tabla hasta que se renderiza.
    Un 200 directo la deja solo en esa respuesta.

    La contrapartida de romper PRG es que un F5 re-postea, y eso ya está
    cubierto: el guard de abajo redirige sin tocar nada cuando el alumno ya
    tiene acceso.
    """

    model = Alumno
    template_name = "alumnos/acceso_form.html"

    def get(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)
        form = CrearAccesoForm(initial=self._inicial(alumno))
        return self._render(request, alumno, form)

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is not None:
            messages.error(request, "Este alumno ya tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        form = CrearAccesoForm(request.POST)
        if form.is_valid():
            try:
                password = servicios.crear_acceso(
                    alumno,
                    form.cleaned_data["tipo"],
                    form.cleaned_data["identificador"],
                )
            except servicios.AccesoYaExiste:
                # Otro request ganó la carrera (doble submit). El dato que
                # cargó el staff estaba bien, así que no corresponde el
                # mensaje de "probá con el otro".
                messages.error(request, "Este alumno ya tiene un acceso creado.")
                return redirect("alumnos:detalle", pk=alumno.pk)
            except servicios.IdentificadorEnUso:
                # Mensaje deliberadamente genérico: confirmar que ese email ya
                # existe convertiría este form en un enumerador de usuarios de
                # toda la plataforma, y ahora los usuarios SON emails reales.
                form.add_error(
                    "identificador",
                    "No se puede usar ese dato. Probá con el otro: si pusiste "
                    "el email, cargá el teléfono, o al revés.",
                )
            else:
                alumno.refresh_from_db()
                return _render_credenciales(request, alumno, password, "crear")
        return self._render(request, alumno, form)

    @staticmethod
    def _inicial(alumno):
        """Precarga el dato de contacto que la ficha ya tiene, para que el
        staff no lo vuelva a tipear (y no lo tipee distinto)."""
        if alumno.email:
            return {"tipo": identidad.TIPO_EMAIL, "identificador": alumno.email}
        if alumno.telefono:
            return {"tipo": identidad.TIPO_TELEFONO, "identificador": alumno.telefono}
        return {}

    def _render(self, request, alumno, form):
        return render(request, self.template_name, {"form": form, "alumno": alumno})


class RegenerarPasswordView(
    StaffRequiredMixin, TenantScopedMixin, SingleObjectMixin, View
):
    """Contraseña nueva al azar para un alumno que YA tiene acceso.

    POST-only: muta credenciales, nunca debe dispararse con un GET (link,
    prefetch del navegador, etc). Mismo criterio que `AlumnoToggleEstadoView`.

    Expulsa al alumno de sus sesiones vivas como efecto colateral de que
    cambie el hash de la contraseña — ver `alumnos/services.py`.
    """

    model = Alumno
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        alumno = self.get_object()
        if alumno.perfil is None:
            messages.error(request, "Este alumno todavía no tiene un acceso creado.")
            return redirect("alumnos:detalle", pk=alumno.pk)

        password = servicios.regenerar_password(alumno)
        return _render_credenciales(request, alumno, password, "regenerar")
