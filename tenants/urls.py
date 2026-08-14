from django.contrib.auth import views as auth_views
from django.urls import path

from tenants.views import (
    GimnasioLandingView,
    GimnasioUpdateView,
    HomeView,
    LogoSugerirPaisajeView,
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
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("gimnasio/editar/", GimnasioUpdateView.as_view(), name="gimnasio_editar"),
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
    path("g/<slug:slug>/", GimnasioLandingView.as_view(), name="landing_gimnasio"),
]
