from django.urls import path

from solicitudes.views import (
    SolicitarAccesoView,
    SolicitudEnviadaView,
    VerificarAccesoView,
)

app_name = "solicitudes"

urlpatterns = [
    path("", SolicitarAccesoView.as_view(), name="solicitar"),
    path("enviada/", SolicitudEnviadaView.as_view(), name="enviada"),
    path("verificar/<str:token>/", VerificarAccesoView.as_view(), name="verificar"),
]
