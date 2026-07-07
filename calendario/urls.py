"""Rutas de la integración con Google Calendar (Parte C).

Namespace `calendario`. Todas las vistas son del alumno (`AlumnoRequiredMixin`):
conectar la cuenta, el callback de OAuth, desconectar y reintentar la sync.
"""

from django.urls import path

from calendario import views

app_name = "calendario"

urlpatterns = [
    path("conectar/", views.ConectarCalendarioView.as_view(), name="conectar"),
    path("callback/", views.CalendarioCallbackView.as_view(), name="callback"),
    path("desconectar/", views.DesconectarCalendarioView.as_view(), name="desconectar"),
    path("reintentar/", views.ReintentarSyncView.as_view(), name="reintentar"),
]
