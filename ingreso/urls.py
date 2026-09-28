from django.urls import path

from ingreso import views

app_name = "ingreso"

urlpatterns = [
    # La versión va en la ruta desde el día 1: el instalador la deja cableada
    # en un equipo que no podemos actualizar.
    path("v1/eventos/", views.EventosApiView.as_view(), name="api_eventos"),
    path("v1/padron/", views.PadronApiView.as_view(), name="api_padron"),
    # Pantallas de staff. Sin `dispositivo_eliminar` a propósito: un lector se
    # desactiva, y el `PROTECT` de los eventos lo respalda.
    path("", views.EventoIngresoListView.as_view(), name="listado"),
    path("lectores/", views.DispositivoListView.as_view(), name="dispositivos"),
    path("lectores/nuevo/", views.DispositivoCreateView.as_view(), name="dispositivo_crear"),
    path(
        "lectores/<int:pk>/",
        views.DispositivoUpdateView.as_view(),
        name="dispositivo_editar",
    ),
    path(
        "lectores/<int:pk>/token/",
        views.DispositivoRotarTokenView.as_view(),
        name="dispositivo_rotar",
    ),
    path(
        "configuracion/",
        views.ConfiguracionIngresoView.as_view(),
        name="configuracion",
    ),
    path(
        "credenciales/generar/",
        views.CredencialesEnBloqueView.as_view(),
        name="credenciales_en_bloque",
    ),
    path(
        "credenciales/<int:pk>/baja/",
        views.CredencialBajaView.as_view(),
        name="credencial_baja",
    ),
    path(
        "alumnos/<int:alumno_pk>/qr/",
        views.CredencialQrView.as_view(),
        name="credencial_qr",
    ),
    path(
        "alumnos/<int:alumno_pk>/tarjeta/",
        views.TarjetaAlumnoPdfView.as_view(),
        name="tarjeta_alumno",
    ),
    path("tarjetas/", views.TarjetasView.as_view(), name="tarjetas"),
    path("tarjetas/pdf/", views.TarjetasPdfView.as_view(), name="tarjetas_pdf"),
    # Portal del alumno: sin pk, el alumno es el de la sesión.
    path("mi-codigo/", views.MiCodigoView.as_view(), name="mi_codigo"),
    path(
        "alumnos/<int:alumno_pk>/facial/",
        views.CredencialFacialView.as_view(),
        name="credencial_facial",
    ),
]
