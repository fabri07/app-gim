"""Middleware de `tenants`.

El proyecto mantiene `MIDDLEWARE` deliberadamente corto, así que agregar uno
necesita justificación. Esta la tiene: la expiración de una suplantación es un
control que **tiene que evaluarse en cada request**, y no existe otro lugar
donde hacerlo. Ponerlo en un mixin dejaría afuera cualquier vista que no lo
use (`HomeView`, por ejemplo, solo lleva `LoginRequiredMixin`), y ponerlo en
una vista solo lo evaluaría al entrar a esa vista.
"""

import logging

from django.shortcuts import redirect

from tenants import suplantacion

logger = logging.getLogger(__name__)


class ExpirarSuplantacionMiddleware:
    """Corta una suplantación que superó `suplantacion.MAX_DURACION`.

    Sin esto, `MAX_DURACION` y `vencida()` serían código muerto — y peor:
    `CLAUDE.md` e `ISSUES.md` afirmarían un límite de 2 h que no se aplica.

    Va DESPUÉS de `AuthenticationMiddleware`, porque `volver()` necesita
    `request.user` para revalidar el gimnasio.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # `vencida()` va DENTRO del try: lee `datos["inicio"]` y lo parsea con
        # `fromisoformat`, así que una sesión con la clave presente pero sin
        # `inicio` (o con una fecha corrupta) tiraría KeyError/ValueError en
        # CADA request — incluido `/accounts/logout/`, dejando al usuario sin
        # salida salvo borrar cookies a mano.
        try:
            if suplantacion.vencida(request):
                suplantacion.volver(request)
        except Exception:
            # `volver()` ya es fail-closed en los casos que prevé, pero acá no
            # se puede dejar la sesión a medio camino en la cuenta del alumno.
            # Se descarta y se manda a login: seguir con `get_response` en un
            # request donde `request.user` ya quedó resuelto como el alumno
            # renderizaría su portal con un 200, o sea fail-OPEN por un request.
            logger.exception("Fallo al cerrar una suplantación vencida")
            request.session.flush()
            return redirect("login")

        return self.get_response(request)
