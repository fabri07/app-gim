"""
Corte de acceso por el estado de la cuenta del gimnasio.

Va en un middleware y no en un mixin por el mismo motivo que
`tenants/middleware.py`: el chequeo tiene que correr en CADA request. Un mixin
dejaría afuera cualquier vista que no lo use, y el día que alguien agregue una
sin acordarse, la cuenta congelada tendría una puerta abierta que nadie ve.

**Usa `process_view` y no `__call__`**: ahí ya existe `request.resolver_match`,
que es lo que permite decidir por nombre de ruta (`logout`, la PWA, el panel
del superadmin) en vez de por prefijo de URL escrito a mano.

**El bloqueo responde 200 con una pantalla, nunca 403 ni 404.** Bajo el
`hx-boost="true"` global de `base.html`, un 4xx es un click que no hace nada y
no deja ningún mensaje: el usuario concluye que la app se rompió. La única
excepción son los POST, que se redirigen a `home` -- y el GET de `home`
renderiza el cartel, así que el redirect termina siempre en una pantalla que
explica qué pasa, sin ningún loop posible.

Fase 4 va a registrar el día activo de cada gimnasio en este mismo middleware,
ANTES del bloqueo y sobre la misma resolución de `Perfil`: de ahí que
`_perfil_de(request)` sea un helper aparte y memoizado.
"""

from django.shortcuts import redirect, render

from tenants.models import (
    ESTADOS_SIN_ACCESO_ALUMNO,
    ESTADOS_SIN_ACCESO_STAFF,
    Perfil,
)

#: Apps enteras que el bloqueo no mira.
#:
#: `admin` y `plataforma` son las dos superficies del dueño del producto: son
#: justamente donde se descongela una cuenta, así que bloquearlas sería tirar
#: la llave adentro de la casa cerrada. `notificaciones` es la infraestructura
#: de la PWA (service worker, manifest, íconos, alta y baja de push): el
#: navegador espera un `.js` o un `.png` ahí, y devolverle un cartel HTML con
#: 200 le rompe la instalación de la app a un gimnasio que después paga.
APPS_SIN_BLOQUEO = frozenset({"admin", "plataforma", "notificaciones"})

#: Rutas de `tenants` que el bloqueo no mira. Van como nombre PELADO porque
#: `tenants/urls.py` no define `app_name` (y su comentario dice que no hay que
#: agregárselo: todo el proyecto referencia esas rutas sin namespace).
#:
#: Cada una es una forma distinta de dejar a alguien encerrado: sin `logout`
#: no puede ni salir de la sesión; sin `suplantacion_volver` el staff que
#: entró a ver la app como un alumno se queda atrapado en la cuenta del
#: alumno; `login`/`login_gimnasio` son las pantallas a las que vuelve
#: después; y la landing y la política de privacidad son páginas públicas que
#: no tienen nada que ver con el acceso de este usuario.
URLS_SIN_BLOQUEO = frozenset(
    {
        "login",
        "logout",
        "suplantacion_volver",
        "login_gimnasio",
        "logo_gimnasio",
        "fondo_gimnasio",
        "landing_gimnasio",
        "politica_privacidad",
    }
)


def _perfil_de(request):
    """El `Perfil` del usuario del request, o `None`.

    Memoizado en el request para que el middleware, el context processor y
    (en la Fase 4) el registro de actividad lean lo mismo una sola vez.

    No cuesta ninguna query propia en el camino normal: `base.html` resuelve
    `user.perfil.gimnasio` en toda página autenticada y el ORM cachea las dos
    relaciones en la instancia del usuario, así que acá se paga la misma
    consulta un poco antes. `getattr(..., None)` alcanza para el usuario sin
    Perfil: `RelatedObjectDoesNotExist` hereda de `AttributeError` justamente
    para eso.
    """
    if not hasattr(request, "_plataforma_perfil"):
        usuario = getattr(request, "user", None)
        if usuario is None or not usuario.is_authenticated:
            request._plataforma_perfil = None
        else:
            request._plataforma_perfil = getattr(usuario, "perfil", None)
    return request._plataforma_perfil


def sin_acceso(perfil):
    """True si el estado de la cuenta de ese gimnasio deja afuera a ese rol.

    Los dos conjuntos viven en `tenants.models` y los comparte
    `notificaciones/services.py`: el push silenciado y el acceso cortado
    tienen que ser exactamente la misma regla, o el alumno recibe un aviso de
    algo que después no puede abrir.
    """
    if perfil is None:
        return False
    estados = (
        ESTADOS_SIN_ACCESO_ALUMNO
        if perfil.rol == Perfil.Rol.ALUMNO
        else ESTADOS_SIN_ACCESO_STAFF
    )
    return perfil.gimnasio.estado_cuenta in estados


class PlataformaMiddleware:
    """Corta el acceso de un gimnasio con la cuenta congelada."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        usuario = getattr(request, "user", None)
        if usuario is None or not usuario.is_authenticated:
            return None
        # El dueño del producto nunca se bloquea: su Perfil puede estar colgado
        # de un gimnasio congelado (el de prueba, el suyo propio) y el panel
        # desde donde se descongela es el que dejaría de abrir.
        if usuario.is_superuser:
            return None

        match = request.resolver_match
        if match is not None and (
            match.app_name in APPS_SIN_BLOQUEO or match.url_name in URLS_SIN_BLOQUEO
        ):
            return None

        # Durante una suplantación `request.user` ES el alumno, así que el
        # staff ve exactamente lo que ve él -- que es el punto de suplantar.
        # Salir de ahí lo cubre `suplantacion_volver`, que está en la
        # allowlist de arriba.
        if not sin_acceso(_perfil_de(request)):
            return None

        if request.method in ("GET", "HEAD"):
            return render(
                request,
                "plataforma/cuenta_bloqueada.html",
                {"gimnasio": _perfil_de(request).gimnasio},
                # 200 y no 403: ver el docstring del módulo.
                status=200,
            )
        return redirect("home")
