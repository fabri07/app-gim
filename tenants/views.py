"""
Vista de inicio (dashboard) tras login, personalización del gimnasio y landing
pública.

El alta de gimnasios NO vive acá: el registro self-serve se cerró y ahora se
hace con `manage.py crear_gimnasio` (ver `tenants/services.py`).
"""

import logging
from datetime import date, datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model, login, views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views import View
from django.views.generic import DetailView, TemplateView, UpdateView
from django.views.generic.detail import SingleObjectMixin

from core.mixins import TenantScopedMixin
from tenants import google_login, paisaje_matching, suplantacion
from tenants.forms import GimnasioForm, ResetPasswordStaffForm
from tenants.mixins import StaffRequiredMixin
from tenants.models import Gimnasio, Perfil, doodle_static_url

logger = logging.getLogger(__name__)


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
        from rutinas.models import RutinaAsignada

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
        rutina_actual = RutinaAsignada.vigente_de(alumno=alumno)

        dias_disponibles = []
        if rutina_actual is not None:
            # Días reales con ejercicios cargados -- no `dias_por_semana` de
            # la plantilla original, que puede no coincidir si se cargó de
            # forma parcial. Cada botón lleva a RutinaMiDiaDetailView, que
            # muestra las 4 semanas de ESE día.
            # `{numero, nombre}` y no enteros pelados: el nombre del día
            # ("Tren superior · Core") viene del plan del entrenador y es lo
            # que le dice al alumno qué va a entrenar antes de entrar. El
            # `{% url %}` sigue necesitando el entero.
            nombres = {}
            for numero, nombre in rutina_actual.items.values_list(
                "dia", "dia_nombre"
            ):
                if nombre and numero not in nombres:
                    nombres[numero] = nombre
            dias_disponibles = [
                {"numero": numero, "nombre": nombres.get(numero, "")}
                for numero in sorted(
                    set(rutina_actual.items.values_list("dia", flat=True))
                )
            ]

        return {
            "alumno": alumno,
            "rutina_actual": rutina_actual,
            "dias_disponibles": dias_disponibles,
            "mensualidad_actual": alumno.pagos.filter(
                mes=hoy.month, anio=hoy.year
            ).first(),
            "fecha_limite_pago": date(
                hoy.year, hoy.month, perfil.gimnasio.dia_vencimiento_pago
            ),
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

        # `localdate()` y NO `now().date()`: `now()` es UTC y `TIME_ZONE` es
        # `America/Argentina/Buenos_Aires`, así que entre las 21:00 y las
        # 23:59 la fecha UTC ya es la de mañana. Con el corte en UTC, el
        # último día del mes después de las 21:00 «Pagos del mes» mostraba las
        # cuotas del mes SIGUIENTE (ninguna: el cron todavía no las generó) y
        # «Alumnos con rutina» contaba planes que arrancan mañana, mientras el
        # portal del alumno —que usa `vigente_de`, con fecha local— decía que
        # no tenía rutina.
        hoy = timezone.localdate()

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
            # Alumnos ACTIVOS con un plan vigente, no filas de rutina: desde
            # que las rutinas ya no se archivan, contar `activa=True` sumaría
            # todo el histórico (un alumno con 5 ciclos valdría 5) y el número
            # solo podría subir. El filtro por `estado` evita además que un
            # alumno dado de baja siga contando para siempre, y alinea el KPI
            # con el de "alumnos activos" de acá arriba.
            "alumnos_con_rutina_count": Alumno.objects.for_gimnasio(gimnasio)
            .filter(
                estado=Alumno.Estado.ACTIVO,
                rutinas_asignadas__activa=True,
                rutinas_asignadas__fecha_inicio__lte=hoy,
            )
            .distinct()
            .count(),
            # Planes que se terminan esta semana. Va en el dashboard porque
            # es lo único que el staff ve sin ir a buscarlo: si el aviso
            # viviera solo en la ficha de cada alumno, habría que sospechar
            # primero para encontrarlo.
            "planes_por_vencer": RutinaAsignada.por_vencer_de(gimnasio=gimnasio),
            # Solo broadcasts en el dashboard del staff: las personales son de
            # un alumno puntual, no del panel de gestión (Parte B).
            "ultimas_novedades": Novedad.objects.for_gimnasio(gimnasio)
            .visibles()
            .filter(alumno__isnull=True)[:5],
            # Analítica (subproyecto 4): asistencia, género, RPE por
            # ejercicio, ejercicios más asignados (general y por género).
            # Indicadores temporales (2026-09-02): el panel era todo agregado
            # histórico, así que no mostraba si el gimnasio crece o se vacía.
            **analitica.indicadores_del_momento(gimnasio),
            "altas_y_bajas": analitica.altas_y_bajas_por_mes(gimnasio),
            "asistencia_semanal": analitica.asistencia_por_semana(gimnasio),
            "asistencia_diaria": analitica.asistencia_diaria(gimnasio),
            "ingresos_mensuales": analitica.ingresos_por_mes(gimnasio),
            "asistencia": analitica.asistencia_por_dia_y_hora(gimnasio),
            "genero_stats": analitica.distribucion_por_genero(gimnasio),
            "rpe_por_ejercicio": analitica.rpe_por_ejercicio(gimnasio),
            "ejercicios_mas_asignados": analitica.ejercicios_mas_asignados(gimnasio),
            "ejercicios_mas_asignados_por_genero": analitica.ejercicios_mas_asignados_por_genero(
                gimnasio
            ),
        }


GIMNASIO_COOKIE_NOMBRE = "gimnasio_preferido"
GIMNASIO_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 año, mismo criterio que
                                                # SECURE_HSTS_SECONDS en config/settings.py
GIMNASIO_OMITIR_PARAM = "otro_gimnasio"


def setear_cookie_gimnasio(response, user):
    """Guarda en `response` el slug de `user.perfil.gimnasio` -- primera
    cookie propia del proyecto (fuera de sessionid/csrftoken de Django).
    Puramente cosmética: la próxima vez que este dispositivo llegue al
    login genérico sin sesión activa (`LoginView.get`), ve directo la
    estética de este gimnasio en vez del paisaje Bosque default.

    Defensivo ante `Perfil` inexistente -- en la práctica todo usuario que
    llega a `form_valid` por acá ya tiene uno, pero un superuser de
    `createsuperuser` no, y no debe romper el login por esto.

    `secure=not settings.DEBUG` replica el criterio ya usado para
    SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE (`config/settings.py`,
    `if not DEBUG:`). `samesite="Lax"` es explícito porque `set_cookie()`
    NO hereda SESSION_COOKIE_SAMESITE -- ese setting solo aplica al cookie
    de sesión vía SessionMiddleware.
    """
    try:
        slug = user.perfil.gimnasio.slug
    except ObjectDoesNotExist:
        return
    response.set_cookie(
        GIMNASIO_COOKIE_NOMBRE,
        slug,
        max_age=GIMNASIO_COOKIE_MAX_AGE,
        secure=not settings.DEBUG,
        httponly=True,
        samesite="Lax",
    )


def gimnasio_de_cookie(request):
    """Gimnasio activo apuntado por la cookie `gimnasio_preferido`, o
    `None` si no hay cookie, el slug no existe, o el gimnasio está inactivo.

    A diferencia de `gimnasio_activo_o_404`, un slug inválido acá NO es un
    error -- es una cookie vieja/corrupta (gimnasio dado de baja, o dato
    corrupto) que `LoginView.get` debe ignorar en silencio y de paso
    limpiar, no un 404."""
    slug = request.COOKIES.get(GIMNASIO_COOKIE_NOMBRE)
    if not slug:
        return None
    return Gimnasio.objects.filter(slug=slug, activo=True).first()


class LoginView(auth_views.LoginView):
    """Sin `redirect_authenticated_user`, un usuario ya logueado que visita
    `/accounts/login/` ve el form de login superpuesto a su propia nav de
    staff y al fondo de su gimnasio -- Django no redirige a un usuario ya
    autenticado por default, solo renderiza el form igual (bug real, visto
    en producción). Vive acá como clase (no como kwarg inline en
    `tenants/urls.py`) para que `GimnasioLoginView` la herede sin duplicar
    el flag."""

    redirect_authenticated_user = True

    def get(self, request, *args, **kwargs):
        """Login genérico: si el visitante anónimo trae la cookie
        `gimnasio_preferido` de una visita anterior, lo manda derecho a
        `g/<slug>/login/` preservando `?next=`, para que vea la estética de
        SU gimnasio en vez del paisaje Bosque genérico.

        Solo corre acá, nunca en `GimnasioLoginView`:
        `getattr(self, "gimnasio", None)` es `None` únicamente en el login
        genérico -- `GimnasioLoginView.dispatch` ya resolvió `self.gimnasio`
        por el slug de la URL ANTES de que `get()` corra (y no overridea
        `get`, así que hereda este método), y ese slug explícito nunca debe
        ser pisado por la cookie.

        No toca `post()`: el submit del form (con o sin `gimnasio` en
        contexto) sigue yendo directo a `form_valid`/`form_invalid`, sin que
        esta lógica interfiera con el envío de credenciales.

        `?otro_gimnasio=1` (enlazado desde "¿No es tu gimnasio?" en
        `login.html`) desactiva el chequeo UNA vez y borra la cookie vieja
        -- sin esto, alguien que activamente quiere ver el login genérico
        quedaría en loop de redirect hacia el gimnasio que la cookie
        recuerda. Un slug de cookie inexistente/inactivo también se limpia
        acá (`cookie_invalida`): evita repetir la consulta a `Gimnasio` en
        cada visita futura con una cookie que ya sabemos que no sirve.

        Todo el bloque (chequeo Y borrado de cookie) queda adentro del
        `if getattr(self, "gimnasio", None) is None`: sin este scoping, un
        `GET g/<slug>/login/?otro_gimnasio=1` (URL escrita/copiada a mano,
        no generada por este template) borraría la cookie igual, aunque
        `GimnasioLoginView` nunca ofrece ese link -- contradice la garantía
        de que este mecanismo "solo corre en el login genérico".
        """
        if getattr(self, "gimnasio", None) is None:
            omitir = request.GET.get(GIMNASIO_OMITIR_PARAM) == "1"
            cookie_invalida = False
            if not omitir and GIMNASIO_COOKIE_NOMBRE in request.COOKIES:
                gimnasio = gimnasio_de_cookie(request)
                if gimnasio is not None:
                    return self._redirigir_a_login_gimnasio(gimnasio)
                cookie_invalida = True

            response = super().get(request, *args, **kwargs)
            if omitir or cookie_invalida:
                response.delete_cookie(GIMNASIO_COOKIE_NOMBRE)
            return response

        return super().get(request, *args, **kwargs)

    def _redirigir_a_login_gimnasio(self, gimnasio):
        """Redirect manual (no pasa por `RedirectURLMixin.get_success_url`,
        que es para DESPUÉS de loguearse) -- hay que propagar `?next=` a
        mano: `RedirectURLMixin.get_redirect_url()` solo lee `next` del
        request ACTUAL, no lo hereda de un redirect propio.

        Se usa `self.get_redirect_url()` (no `request.GET.get(...)` crudo)
        a propósito: valida el destino con
        `url_has_allowed_host_and_scheme` antes de reenviarlo -- sin esto,
        `?next=https://evil.com` viajaría sin validar hasta la URL de
        `login_gimnasio`, confiando en que ALGO río abajo lo vuelva a
        validar en vez de cortarlo acá, en el único lugar que lo reenvía."""
        url = reverse("login_gimnasio", args=[gimnasio.slug])
        next_url = self.get_redirect_url()
        if next_url:
            url = f"{url}?{urlencode({REDIRECT_FIELD_NAME: next_url})}"
        return redirect(url)

    def form_valid(self, form):
        """Tras loguearse, guarda qué gimnasio es el de este usuario en la
        cookie `gimnasio_preferido` (ver `setear_cookie_gimnasio`) para
        reconocerlo la próxima vez que entre por el login genérico.

        `GimnasioLoginView` hereda este método SIN overridearlo (no define
        su propio `form_valid`), así que un login que entra por
        `g/<slug>/login/` también deja la cookie -- confirmado por MRO:
        `GimnasioLoginView -> LoginView -> auth_views.LoginView`, y
        `GimnasioLoginView` solo pisa `dispatch`/`get_context_data`."""
        response = super().form_valid(form)
        setear_cookie_gimnasio(response, self.request.user)
        return response


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
        context["paletas"] = Gimnasio.PALETAS
        # doodle_static_url (no `static()` pelado) para no romper la única
        # pantalla donde el dueño puede sacarse de encima un doodle cuyo
        # archivo falta -- ver su docstring.
        context["doodle_svgs"] = {
            valor: doodle_static_url(valor) for valor, _ in Gimnasio.Doodle.choices
        }
        # Las miniaturas del selector se pintan con la MISMA URL que usa el
        # preview: si acá se construyera el path a mano ({% static 'img/doodles/' %}
        # + valor), HashedFilesMixin no hashea un path terminado en "/", así que
        # miniatura y preview terminarían apuntando a dos URLs distintas del
        # mismo asset y la miniatura quedaría sin cache-busting.
        context["doodle_opciones"] = [
            (radio, context["doodle_svgs"].get(str(radio.data["value"]), ""))
            for radio in context["form"]["fondo_doodle"]
        ]
        # El preview arranca reflejando lo que YA está guardado; sin esto, al
        # abrir la pantalla con fondo_tipo="imagen" la ventana caía al fallback
        # `none` de --preview-fondo-imagen y mostraba un tinte plano, mintiendo
        # sobre lo guardado hasta que el dueño volvía a elegir un archivo.
        gimnasio = self.object
        context["fondo_imagen_url"] = (
            gimnasio.fondo_imagen.url if gimnasio.fondo_imagen else ""
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Datos del gimnasio actualizados.")
        return response


class LogoSugerirPaisajeView(StaffRequiredMixin, View):
    """Frente 2 del rediseño de estética: analiza el logo recién elegido en
    `gimnasio_form.html` (antes de guardar) y devuelve el paisaje curado más
    parecido. Sugerencia pura -- no toca `Gimnasio.paleta`, el dueño sigue
    confirmando con "Guardar cambios" y puede cambiar el paisaje sugerido a
    mano, igual que hoy."""

    def post(self, request, *args, **kwargs):
        archivo = request.FILES.get("logo")
        if archivo is None:
            return JsonResponse({"error": "Falta el archivo del logo."}, status=400)
        paisaje = paisaje_matching.sugerir_paisaje(archivo)
        return JsonResponse({"paisaje": paisaje})


def gimnasio_activo_o_404(slug):
    """Resuelve un Gimnasio público por slug, 404 si no existe o está
    desactivado -- sin distinguir cuál de los dos casos es. Compartido entre
    `GimnasioLandingView` y `GimnasioLoginView` para que las dos vistas no
    puedan divergir en este criterio (antes vivía solo en
    `GimnasioLandingView.get_queryset`)."""
    return get_object_or_404(Gimnasio, slug=slug, activo=True)


class GimnasioLandingView(DetailView):
    """Landing pública de un gimnasio (subproyecto 5): la primera vista del
    proyecto sin ningún mixin de autenticación -- accesible por cualquiera,
    logueado o no.

    Sin subdominios por gimnasio (principio no negociable del proyecto): la
    URL se resuelve por `Gimnasio.slug`, que ya existía desde Fase 1 sin
    ningún uso público hasta ahora.

    `get_object` usa `gimnasio_activo_o_404` para que un gimnasio
    desactivado (o un slug que nunca existió) dé 404 -- no tiene sentido
    publicitar la landing de un gimnasio que ya no opera, y un 404 no revela
    si el slug alguna vez existió.

    No hay alta de leads propia ni formulario de contacto: el staff asigna
    usuario/contraseña a mano (ver `alumnos/views.py::CrearAccesoView`), así
    que un visitante nuevo no puede autoregistrarse como alumno -- la
    landing solo ofrece contactar al gimnasio (WhatsApp/Instagram/teléfono,
    campos que ya existían) o, si ya es alumno, ir al login de ESE gimnasio
    (`GimnasioLoginView`).
    """

    model = Gimnasio
    template_name = "tenants/landing.html"
    context_object_name = "gimnasio"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_object(self, queryset=None):
        return gimnasio_activo_o_404(self.kwargs["slug"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Import tardío de HorarioAtencion: mismo criterio que Alumno en
        # SuplantarView/HomeView -- `tenants` está antes que `turnos` en el
        # orden de dependencia de las apps.
        from turnos.models import HorarioAtencion

        horarios_por_dia = {}
        for horario in HorarioAtencion.objects.for_gimnasio(self.object):
            horarios_por_dia.setdefault(horario.dia_semana, []).append(horario)
        context["horarios_por_dia"] = [
            (horarios[0].get_dia_semana_display(), horarios)
            for horarios in horarios_por_dia.values()
        ]
        return context


class GimnasioLoginView(LoginView):
    """Login con la estética de UN gimnasio específico (resuelto por slug de
    URL, mismo patrón que `GimnasioLandingView`), enlazado desde su landing
    pública (`landing.html`).

    El slug es PURAMENTE estético -- el proyecto no tiene subdominios por
    gimnasio (principio no negociable), así que un alumno de OTRO gimnasio,
    o un miembro de staff, pueden loguearse igual desde esta URL: la
    autenticación sigue siendo el mismo `User`/`Perfil` de siempre, sin
    ninguna restricción adicional. Solo cambia qué `Gimnasio` se le pasa al
    template para pintar colores/logo/tipografía antes de loguearse.

    No hereda de `DetailView`/`SingleObjectMixin`: `LoginView` ya es una
    `FormView`, y mezclar dos jerarquías de vista genérica agrega
    complejidad sin necesidad real -- alcanza con resolver el gimnasio en
    `dispatch` y agregarlo al contexto.

    Hereda `redirect_authenticated_user=True` de `LoginView` sin duplicar el
    flag: un usuario ya logueado que visita esta URL también es redirigido
    a `home`, ignorando el slug (no tiene sentido re-loguearse, y el slug no
    debe influir a dónde va después de loguearse).
    """

    def dispatch(self, request, *args, **kwargs):
        self.gimnasio = gimnasio_activo_o_404(kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["gimnasio"] = self.gimnasio
        return context


_GOOGLE_LOGIN_STATE_KEY = "google_login_state"
_GOOGLE_LOGIN_STATE_TS_KEY = "google_login_state_ts"
_GOOGLE_LOGIN_VERIFIER_KEY = "google_login_verifier"
_GOOGLE_LOGIN_NEXT_KEY = "google_login_next"
_GOOGLE_LOGIN_STATE_MAX_SEGUNDOS = 600  # el state en sesión vale 10 minutos, mismo criterio que calendario


def _next_url_seguro(request, next_url):
    """Mismo chequeo que `RedirectURLMixin.get_redirect_url()` (usado por
    `LoginView`/`GimnasioLoginView`), pero aplicado a un valor que viene de
    SESIÓN (guardado en `GoogleLoginRedirectView`) en vez de leerlo de los
    parámetros del request actual -- el callback de Google no trae `next`,
    solo `code`/`state`/`error`."""
    if not next_url:
        return ""
    es_seguro = url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    return next_url if es_seguro else ""


class GoogleLoginRedirectView(View):
    """Arranca el flujo de login con Google: guarda `state`/`verifier`/`next`
    en sesión y manda al staff a Google. Sin mixin de auth -- lo usa
    justamente quien todavía NO está logueado. Mismo patrón que
    `ConectarCalendarioView` (`calendario/views.py`), pero sin
    `AlumnoRequiredMixin` ni chequeo de suplantación (no aplican acá: no hay
    sesión activa todavía)."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if not google_login.disponible():
            messages.info(request, "El login con Google no está disponible.")
            return redirect("login")

        next_url = _next_url_seguro(request, request.GET.get(REDIRECT_FIELD_NAME, ""))
        url, state, verifier = google_login.build_authorization_url()
        request.session[_GOOGLE_LOGIN_STATE_KEY] = state
        request.session[_GOOGLE_LOGIN_STATE_TS_KEY] = timezone.now().isoformat()
        request.session[_GOOGLE_LOGIN_VERIFIER_KEY] = verifier
        request.session[_GOOGLE_LOGIN_NEXT_KEY] = next_url
        return redirect(url)


class GoogleLoginCallbackView(View):
    """Callback de Google: valida el `state`, intercambia el `code`, busca
    una cuenta de staff activa con ese email y loguea -- **nunca crea una
    cuenta nueva** (la política de "no self-service" del proyecto se
    mantiene igual acá, ver `ISSUES.md` [2026-07-29]). Mismo patrón de
    validación de `state`/manejo de errores que `CalendarioCallbackView`."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        next_url = request.session.pop(_GOOGLE_LOGIN_NEXT_KEY, "")

        if request.GET.get("error"):
            messages.warning(request, "No se completó el login con Google (permiso cancelado).")
            return self._volver_al_login(next_url)

        if not self._state_valido(request):
            messages.error(request, "La conexión con Google expiró o es inválida. Probá de nuevo.")
            return self._volver_al_login(next_url)

        code = request.GET.get("code")
        state = request.GET.get("state")
        verifier = request.session.pop(_GOOGLE_LOGIN_VERIFIER_KEY, None)
        if not code:
            messages.error(request, "Google no devolvió el código de autorización.")
            return self._volver_al_login(next_url)

        try:
            email = google_login.verificar_identidad(code, state, verifier)
        except Exception as exc:  # noqa: BLE001 - no romper el login, mensaje genérico
            logger.warning("Fallo verificando identidad de Google: %s", type(exc).__name__)
            messages.error(
                request,
                "No se pudo verificar tu cuenta de Google. Probá de nuevo o "
                "iniciá sesión con tu usuario y contraseña.",
            )
            return self._volver_al_login(next_url)

        usuario = self._buscar_staff_activo(email)
        if usuario is None:
            # Mensaje genérico a propósito -- no distingue "no existe" de "es
            # alumno" de "está desactivado" (mismo criterio anti-enumeración
            # que el resto del proyecto): un email que NO es de un staff
            # activo nunca debe revelar por qué falló.
            messages.error(
                request,
                "No encontramos una cuenta de staff con ese email. Pedile al "
                "dueño del gimnasio que te dé de alta.",
            )
            return self._volver_al_login(next_url)

        login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
        response = redirect(next_url or reverse("home"))
        setear_cookie_gimnasio(response, usuario)
        return response

    def _buscar_staff_activo(self, email):
        User = get_user_model()
        usuario = User.objects.filter(username=email, is_active=True).first()
        if usuario is None:
            return None
        perfil = getattr(usuario, "perfil", None)
        if perfil is None or perfil.rol != Perfil.Rol.STAFF:
            return None
        return usuario

    def _state_valido(self, request) -> bool:
        esperado = request.session.pop(_GOOGLE_LOGIN_STATE_KEY, None)
        ts = request.session.pop(_GOOGLE_LOGIN_STATE_TS_KEY, None)
        recibido = request.GET.get("state")
        if not esperado or not recibido or esperado != recibido or not ts:
            return False
        try:
            emitido = datetime.fromisoformat(ts)
        except ValueError:
            return False
        return (timezone.now() - emitido).total_seconds() <= _GOOGLE_LOGIN_STATE_MAX_SEGUNDOS

    def _volver_al_login(self, next_url):
        url = reverse("login")
        if next_url:
            url = f"{url}?{urlencode({REDIRECT_FIELD_NAME: next_url})}"
        return redirect(url)


class StaffPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Último paso de "olvidé mi contraseña" (SOLO staff -- el filtro real
    vive en `ResetPasswordStaffForm.get_users`, acá no hace falta repetir
    ningún chequeo de rol: el token de Django ya está firmado para el
    usuario específico que pasó ese filtro al pedir el reset, no hay forma
    de forjar uno válido para otro usuario).

    `post_reset_login=True` loguea automático apenas confirma la
    contraseña nueva, sin pedirle que la vuelva a tipear para entrar.
    `backend` explícito, mismo criterio que suplantación y el login con
    Google (el proyecto no tiene `AUTHENTICATION_BACKENDS` custom, así que
    hay que decirle a `login()` cuál usar).
    """

    post_reset_login = True
    post_reset_login_backend = "django.contrib.auth.backends.ModelBackend"

    def get_user(self, uidb64):
        """El hash del token de Django no incluye `is_active` -- desactivar
        la cuenta DESPUÉS de pedir el reset no invalida un link ya emitido
        por sí solo. Mismo gotcha que `tenants/suplantacion.py::iniciar` y
        `GoogleLoginCallbackView` ya cubren explícitamente: `login()` de
        Django no revalida `is_active` por su cuenta. Devolver `None` acá
        hace que `dispatch()` trate el link como inválido, igual que un
        token vencido o forjado."""
        user = super().get_user(uidb64)
        if user is not None and not user.is_active:
            return None
        return user

    def form_valid(self, form):
        response = super().form_valid(form)
        setear_cookie_gimnasio(response, self.request.user)
        return response


class StaffPasswordChangeView(StaffRequiredMixin, auth_views.PasswordChangeView):
    """Permite que un staff YA LOGUEADO cambie su propia contraseña de
    forma proactiva -- distinto de "olvidé mi contraseña" (por email, para
    quien ya perdió el acceso) y de la regeneración que el staff hace sobre
    la cuenta de un ALUMNO (`alumnos/views.py`, staff-iniciado sobre otra
    cuenta). `StaffRequiredMixin` es lo que bloquea a un alumno con 403 --
    no alcanza con ocultar el link en la nav, la política del proyecto es
    que las contraseñas de alumno las controla siempre el staff, nunca el
    propio alumno.

    Django llama `update_session_auth_hash()` internamente al cambiar la
    contraseña (`PasswordChangeView.form_valid`), así que el usuario sigue
    autenticado después -- no hace falta overridear nada de eso acá."""

    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("password_change_done")


class StaffPasswordChangeDoneView(StaffRequiredMixin, auth_views.PasswordChangeDoneView):
    """Pantalla de confirmación tras `StaffPasswordChangeView`."""

    template_name = "registration/password_change_done.html"


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
