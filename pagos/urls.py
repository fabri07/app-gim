"""URLs de gestión de pagos mensuales (Fase 2 §6).

No se incluye acá en `config/urls.py` -- eso queda a cargo de quien integre
todas las apps de dominio en una sola pasada.
"""

from django.urls import path

from pagos.views import (
    AlumnoComprobanteUpdateView,
    CobroOnlineListView,
    CobroOnlineResueltoView,
    ConfirmarPagoView,
    CuotaAnularView,
    CuotaMontoView,
    CuotaVolverAImpagaView,
    MedioCobroCreateView,
    MedioCobroListView,
    MedioCobroUpdateView,
    CuotaListView,
    PagarCuotaMPView,
    RetornoCuotaMPView,
)

app_name = "pagos"

urlpatterns = [
    path("", CuotaListView.as_view(), name="listado"),
    path("medios/", MedioCobroListView.as_view(), name="medios_listado"),
    path("medios/nuevo/", MedioCobroCreateView.as_view(), name="medios_crear"),
    path("medios/<int:pk>/editar/", MedioCobroUpdateView.as_view(), name="medios_editar"),
    path("<int:pk>/confirmar/", ConfirmarPagoView.as_view(), name="confirmar"),
    path("<int:pk>/anular/", CuotaAnularView.as_view(), name="anular"),
    path("<int:pk>/monto/", CuotaMontoView.as_view(), name="monto"),
    path(
        "<int:pk>/volver-a-impaga/",
        CuotaVolverAImpagaView.as_view(),
        name="volver_a_impaga",
    ),
    path("mercadopago/", CobroOnlineListView.as_view(), name="online"),
    path(
        "mercadopago/<int:pk>/resuelto/",
        CobroOnlineResueltoView.as_view(),
        name="online_resuelto",
    ),
    path("<int:pk>/mercadopago/", PagarCuotaMPView.as_view(), name="pagar_mp"),
    path(
        "mercadopago/retorno/<uuid:referencia>/",
        RetornoCuotaMPView.as_view(),
        name="retorno_mp",
    ),
    path(
        "<int:pk>/comprobante/",
        AlumnoComprobanteUpdateView.as_view(),
        name="comprobante_subir",
    ),
]
