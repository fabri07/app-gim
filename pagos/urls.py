"""URLs de gestión de pagos mensuales (Fase 2 §6).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from pagos.views import (
    ConfirmarPagoView,
    MedioCobroCreateView,
    MedioCobroListView,
    MedioCobroUpdateView,
    PagoMensualListView,
)

app_name = "pagos"

urlpatterns = [
    path("", PagoMensualListView.as_view(), name="listado"),
    path("medios/", MedioCobroListView.as_view(), name="medios_listado"),
    path("medios/nuevo/", MedioCobroCreateView.as_view(), name="medios_crear"),
    path("medios/<int:pk>/editar/", MedioCobroUpdateView.as_view(), name="medios_editar"),
    path("<int:pk>/confirmar/", ConfirmarPagoView.as_view(), name="confirmar"),
]
