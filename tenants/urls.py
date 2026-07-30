from django.contrib.auth import views as auth_views
from django.urls import path

from tenants.views import GimnasioLandingView, GimnasioUpdateView, HomeView

# No hay ruta de registro: el alta de gimnasios se cerró y se hace con
# `manage.py crear_gimnasio` (ver `tenants/services.py`).
urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("gimnasio/editar/", GimnasioUpdateView.as_view(), name="gimnasio_editar"),
    path("g/<slug:slug>/", GimnasioLandingView.as_view(), name="landing_gimnasio"),
]
