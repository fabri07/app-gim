from django.urls import path

from ingreso.views import EventosApiView, PadronApiView

app_name = "ingreso"

urlpatterns = [
    # La versión va en la ruta desde el día 1: el instalador la deja cableada
    # en un equipo que no podemos actualizar.
    path("v1/eventos/", EventosApiView.as_view(), name="api_eventos"),
    path("v1/padron/", PadronApiView.as_view(), name="api_padron"),
]
