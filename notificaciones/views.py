import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.urls import reverse
from django.views import View

from notificaciones.icons import (
    TAMANOS_PERMITIDOS,
    color_lienzo,
    generar_icono,
    icono_pwa_url,
    version_icono,
)
from notificaciones.models import SuscripcionPush
from tenants import suplantacion
from tenants.views import gimnasio_activo_o_404


class IconoGimnasioView(View):
    """PNG del ícono del gimnasio para el manifest, cacheado. Pública (sin
    login): un ícono de app no es dato sensible, y el manifest se pide antes
    de loguearse.

    La URL lleva `?v=<versión>` (ver `icons.icono_pwa_url`); el valor no se
    valida a propósito -- siempre se sirve el ícono actual -- pero permite
    que la respuesta sea `immutable`: cambiar el logo cambia la URL del
    manifest, y una URL nueva nunca choca con un cache viejo."""

    maskable = False

    def get(self, request, slug, size):
        if size not in TAMANOS_PERMITIDOS:
            raise Http404
        gimnasio = gimnasio_activo_o_404(slug)
        variante = "maskable" if self.maskable else "any"
        clave = f"pwa-icono-{gimnasio.pk}-{size}-{variante}-{version_icono(gimnasio)}"
        png = cache.get(clave)
        if png is None:
            png = generar_icono(gimnasio, size, maskable=self.maskable)
            cache.set(clave, png, timeout=60 * 60 * 24)
        response = HttpResponse(png, content_type="image/png")
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _color_lienzo_cacheado(gimnasio) -> str:
    """`color_lienzo` abre el logo (en producción, desde R2); el manifest se
    pide poco pero no hace falta bajar el archivo cada vez."""
    clave = f"pwa-lienzo-{gimnasio.pk}-{version_icono(gimnasio)}"
    color = cache.get(clave)
    if color is None:
        color = color_lienzo(gimnasio)
        cache.set(clave, color, timeout=60 * 60 * 24)
    return color


class ManifestView(View):
    """Manifest dinámico por gimnasio -- blanco-etiquetado (nombre, colores,
    ícono propios de cada tenant).

    `background_color` es el lienzo del ícono (no el fondo de la paleta):
    Android pinta el splash con ese color y pone el ícono encima, así que
    los dos tienen que ser el mismo o se ve un cuadrado que desentona.
    `Cache-Control: no-cache` para que la verificación periódica de
    actualización del navegador vea el manifest nuevo (y con él la URL
    versionada del ícono) en vez de una copia vieja."""

    def get(self, request, slug):
        gimnasio = gimnasio_activo_o_404(slug)
        data = {
            "name": gimnasio.nombre,
            "short_name": gimnasio.nombre[:30],
            "start_url": "/?utm_source=pwa",
            "id": f"/g/{gimnasio.slug}/",
            "scope": "/",
            "display": "standalone",
            "background_color": _color_lienzo_cacheado(gimnasio),
            "theme_color": gimnasio.color_primario_css,
            "icons": [
                {
                    "src": icono_pwa_url(gimnasio, s, maskable=maskable),
                    "sizes": f"{s}x{s}",
                    "type": "image/png",
                    "purpose": "maskable" if maskable else "any",
                }
                for maskable in (False, True)
                for s in TAMANOS_PERMITIDOS
            ],
        }
        response = JsonResponse(data, content_type="application/manifest+json")
        response["Cache-Control"] = "no-cache"
        return response


class ServiceWorkerView(View):
    """Sirve `static/js/sw.js` en la RAÍZ del dominio (no vía `{% static %}`)
    para que el scope del service worker sea `/` sin necesitar el header
    `Service-Worker-Allowed`, y con una URL estable que no cambia con el
    fingerprinting de WhiteNoise en producción."""

    def get(self, request):
        with open(settings.BASE_DIR / "static" / "js" / "sw.js", encoding="utf-8") as archivo:
            contenido = archivo.read()
        response = HttpResponse(contenido, content_type="application/javascript")
        response["Cache-Control"] = "no-cache"
        return response


class SuscripcionPushCreateView(View):
    """Alta/actualización de una suscripción push del usuario logueado.
    Bloqueada durante suplantación: un staff suplantando a un alumno nunca
    debe suscribir su propio dispositivo a nombre del alumno."""

    http_method_names = ["post"]

    def post(self, request):
        if not request.user.is_authenticated:
            return HttpResponseForbidden()
        if suplantacion.esta_activa(request):
            return HttpResponseForbidden(
                "No se puede suscribir a notificaciones durante una suplantación."
            )
        try:
            gimnasio = request.user.perfil.gimnasio
        except ObjectDoesNotExist:
            return HttpResponseForbidden()
        try:
            data = json.loads(request.body)
            endpoint = data["endpoint"]
            keys = data["keys"]
            p256dh = keys["p256dh"]
            auth = keys["auth"]
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return HttpResponseBadRequest()
        SuscripcionPush.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "gimnasio": gimnasio,
                "usuario": request.user,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
                "activa": True,
            },
        )
        return JsonResponse({"ok": True})


class SuscripcionPushDeleteView(View):
    http_method_names = ["post"]

    def post(self, request):
        if not request.user.is_authenticated:
            return HttpResponseForbidden()
        try:
            data = json.loads(request.body)
            endpoint = data["endpoint"]
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return HttpResponseBadRequest()
        SuscripcionPush.objects.filter(endpoint=endpoint, usuario=request.user).delete()
        return JsonResponse({"ok": True})
