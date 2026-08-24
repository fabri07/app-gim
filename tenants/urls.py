from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import TemplateView

from tenants.forms import ResetPasswordStaffForm
from tenants.views import (
    GimnasioLandingView,
    GimnasioLoginView,
    GimnasioUpdateView,
    GoogleLoginCallbackView,
    GoogleLoginRedirectView,
    HomeView,
    LoginView,
    LogoSugerirPaisajeView,
    StaffPasswordChangeDoneView,
    StaffPasswordChangeView,
    StaffPasswordResetConfirmView,
    SuplantarView,
    VolverDeSuplantacionView,
)

# No hay ruta de registro: el alta de gimnasios se cerró y se hace con
# `manage.py crear_gimnasio` (ver `tenants/services.py`).
#
# Este archivo NO define `app_name` y no hay que agregárselo: todas sus rutas
# se referencian sin namespace (`{% url 'home' %}`, `{% url 'login' %}`, ...)
# desde todo el proyecto, así que ponerlo rompería esas referencias en masa.
urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Frente C: login con Google para staff (coexiste con usuario+contraseña
    # de arriba). Ver `tenants/google_login.py`.
    path("accounts/google/", GoogleLoginRedirectView.as_view(), name="login_google"),
    path(
        "accounts/google/callback/",
        GoogleLoginCallbackView.as_view(),
        name="login_google_callback",
    ),
    # "Olvidé mi contraseña" -- SOLO staff/dueño (ResetPasswordStaffForm
    # filtra por Perfil.rol=STAFF; un alumno con email como identificador
    # nunca recibe el mail, ver tenants/forms.py). Nombres de ruta/vista
    # estándar de Django (no `include('django.contrib.auth.urls')` a
    # propósito, mismo criterio granular que el resto de este archivo).
    path(
        "accounts/password_reset/",
        auth_views.PasswordResetView.as_view(form_class=ResetPasswordStaffForm),
        name="password_reset",
    ),
    path(
        "accounts/password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        StaffPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # Staff YA LOGUEADO cambiando su propia contraseña de forma proactiva --
    # distinto del "olvidé mi contraseña" de arriba. Mismo criterio de
    # wireado manual, nunca `include('django.contrib.auth.urls')`.
    path(
        "accounts/password_change/",
        StaffPasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        StaffPasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path("gimnasio/editar/", GimnasioUpdateView.as_view(), name="gimnasio_editar"),
    # Página estática, sin vista propia -- no hay contexto dinámico que
    # justifique una clase en views.py. Pública a propósito (sin mixin de
    # auth): igual que la landing, cualquiera debería poder leerla sin
    # loguearse.
    path(
        "privacidad/",
        TemplateView.as_view(template_name="tenants/privacidad.html"),
        name="politica_privacidad",
    ),
    path(
        "gimnasio/logo/sugerir-paisaje/",
        LogoSugerirPaisajeView.as_view(),
        name="logo_sugerir_paisaje",
    ),
    # La ruta literal va antes de la que captura un parámetro (mismo criterio
    # que `alumnos/urls.py`). Con `<int:pk>` no hay conflicto real, pero el
    # orden evita el problema si algún día se cambia por un `<str:...>`.
    path(
        "suplantar/volver/",
        VolverDeSuplantacionView.as_view(),
        name="suplantacion_volver",
    ),
    path("suplantar/<int:pk>/", SuplantarView.as_view(), name="suplantar"),
    # La ruta de login va antes que la landing: aunque no hay ambigüedad real
    # (Django exige match completo, "g/<slug>/" no matchea "g/<slug>/login/"),
    # mismo criterio de "ruta más específica primero" que el resto del archivo.
    path("g/<slug:slug>/login/", GimnasioLoginView.as_view(), name="login_gimnasio"),
    path("g/<slug:slug>/", GimnasioLandingView.as_view(), name="landing_gimnasio"),
]
