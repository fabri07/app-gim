from django.urls import path

from suscripcion import views

app_name = "suscripcion"

urlpatterns = [
    path("", views.MiSuscripcionView.as_view(), name="mi_suscripcion"),
    path("pagar/", views.IniciarPagoView.as_view(), name="iniciar_pago"),
    path(
        "retorno/<uuid:referencia>/", views.RetornoView.as_view(), name="retorno"
    ),
    # La versión va en la ruta del webhook porque esa URL queda cargada en el
    # panel de Mercado Pago (y viaja en cada preferencia ya generada), así que
    # cambiarla no es atómico: mismo criterio que `/ingreso/v1/`.
    path(
        "v1/webhook/mercadopago/", views.WebhookView.as_view(), name="webhook"
    ),
]
