from django.urls import path

from notificaciones.views import (
    IconoGimnasioView,
    ManifestView,
    ServiceWorkerView,
    SuscripcionPushCreateView,
    SuscripcionPushDeleteView,
)

app_name = "notificaciones"
urlpatterns = [
    path("sw.js", ServiceWorkerView.as_view(), name="pwa_service_worker"),
    path("g/<slug:slug>/manifest.json", ManifestView.as_view(), name="pwa_manifest"),
    path(
        "g/<slug:slug>/icono-<int:size>.png",
        IconoGimnasioView.as_view(),
        name="pwa_icono",
    ),
    path("push/suscribir/", SuscripcionPushCreateView.as_view(), name="push_suscribir"),
    path(
        "push/desuscribir/", SuscripcionPushDeleteView.as_view(), name="push_desuscribir"
    ),
]
