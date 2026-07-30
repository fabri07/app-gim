"""Middleware de `tenants`.

El proyecto mantiene `MIDDLEWARE` deliberadamente corto, así que agregar uno
necesita justificación. Esta la tiene: la expiración de una suplantación es un
control que **tiene que evaluarse en cada request**, y no existe otro lugar
donde hacerlo. Ponerlo en un mixin dejaría afuera cualquier vista que no lo
use (`HomeView`, por ejemplo, solo lleva `LoginRequiredMixin`), y ponerlo en
una vista solo lo evaluaría al entrar a esa vista.
"""

from tenants import suplantacion


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
        if suplantacion.vencida(request):
            try:
                suplantacion.volver(request)
            except Exception:
                # `volver()` es fail-closed y ya flushea la sesión en los
                # casos que puede prever. Si igual falla (el staff original
                # perdió el rol, cambió de gimnasio, etc.) no se puede dejar
                # la sesión a medio camino en la cuenta del alumno.
                request.session.flush()
        return self.get_response(request)
