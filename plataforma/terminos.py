"""
El cartel de términos y condiciones: el alumno de un gimnasio con los
reconocimientos prendidos los acepta UNA vez antes de seguir usando la app.

Va en un middleware y no en un mixin por el mismo motivo que
`PlataformaMiddleware` (ver el docstring de `plataforma/middleware.py`): el
chequeo tiene que correr en CADA request, y una vista nueva que se olvide del
mixin sería una puerta abierta que nadie ve. Usa su mismo esqueleto:

- **`process_view`**, para decidir por nombre de ruta.
- **La allowlist antes de tocar `request.user`**: las mismas rutas que el
  bloqueo deja pasar (salir de la sesión, volver de una suplantación, la
  política de privacidad que el cartel enlaza, los archivos `immutable` del
  gimnasio, la PWA) más `terminos_aceptar`, que es la salida del cartel.
- **200 con una pantalla, nunca 4xx**, y los POST a cualquier otra ruta se
  redirigen a `home`, cuyo GET muestra el cartel: sin loop posible.

**Va DESPUÉS de `PlataformaMiddleware` en `MIDDLEWARE`**: la cuenta congelada
ve primero su cartel. Ese middleware devuelve su respuesta en `process_view` y
Django ya no llama al de este archivo, así que un alumno bloqueado nunca ve
dos carteles ni acepta términos de una app que no puede usar. Además reusa el
`Perfil` (con su gimnasio) que aquel ya resolvió vía `perfil_de`: decidir acá
no cuesta ninguna query.

Los webhooks (`suscripcion`, `tienda`), la API de `ingreso` (token en header)
y `solicitudes` no llevan sesión: `request.user` es anónimo y pasan por el
corte del anónimo, sin agrandar la allowlist.
"""

from django.shortcuts import redirect

from plataforma.middleware import APPS_SIN_BLOQUEO, URLS_SIN_BLOQUEO, perfil_de
from tenants import suplantacion
from tenants.terminos import debe_aceptar_terminos, responder_cartel

#: La pantalla del cartel (GET) y la que guarda la aceptación (POST).
URL_TERMINOS = "terminos_aceptar"


class TerminosMiddleware:
    """Frena al alumno que todavía no aceptó los términos vigentes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # LO PRIMERO, antes de tocar `request.user`: ver el docstring de
        # `PlataformaMiddleware.process_view` (una cookie de sesión colgada de
        # una respuesta `immutable` le rompe la caché al logo del gimnasio).
        match = request.resolver_match
        if match is not None and (
            match.app_name in APPS_SIN_BLOQUEO
            or match.url_name in URLS_SIN_BLOQUEO
            or match.url_name == URL_TERMINOS
        ):
            return None

        usuario = getattr(request, "user", None)
        if usuario is None or not usuario.is_authenticated:
            return None
        # Simétrico a `PlataformaMiddleware`: el dueño del producto puede
        # tener un Perfil colgado de cualquier gimnasio y nunca se frena.
        if usuario.is_superuser:
            return None
        # Suplantando, `request.user` ES el alumno, pero quien mira es el
        # staff: no puede aceptar en nombre del alumno (sería un
        # consentimiento que el alumno nunca dio), así que tampoco se lo frena
        # con un cartel que no puede cerrar. Ve la app como el alumno la verá
        # después de aceptar.
        if suplantacion.esta_activa(request):
            return None

        perfil = perfil_de(request)
        if not debe_aceptar_terminos(perfil):
            return None

        if request.method in ("GET", "HEAD"):
            return responder_cartel(request, perfil)
        return redirect("home")
