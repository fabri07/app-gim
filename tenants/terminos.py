"""Regla de «el alumno debe aceptar los términos y condiciones».

No importa nada de `plataforma` (su middleware consume esto; `plataforma.models`
importa `tenants.models`).
"""

from django.shortcuts import render
from django.utils.cache import add_never_cache_headers

# Subirla obliga a todos los alumnos a volver a aceptar.
VERSION_TERMINOS = 1


def debe_aceptar_terminos(perfil):
    """True si hay que frenar al alumno con la pantalla de términos.

    Sin queries: lee `perfil.gimnasio`, que el request ya resolvió.
    """
    if perfil is None or perfil.rol != perfil.Rol.ALUMNO:
        return False
    if not perfil.gimnasio.reconocimientos_habilitado:
        return False
    return perfil.terminos_version < VERSION_TERMINOS


def responder_cartel(request, perfil, form=None):
    """La pantalla de términos, con 200 y `no-store`.

    Un solo lugar para el middleware (cualquier GET del alumno) y para
    `AceptarTerminosView` (el GET directo y el POST sin tildar), así los dos
    caminos muestran lo mismo.

    200 y no 4xx: bajo el `hx-boost` global un 4xx es un click mudo.
    `no-store` por lo mismo que el cartel de cuenta congelada
    (`plataforma/middleware.py`): sin eso el bfcache devuelve el cartel con
    «atrás» después de haber aceptado. `terminos_pendientes` apaga el menú
    del alumno en `base.html`: todos sus links terminan en este cartel.
    """
    from tenants.forms import AceptarTerminosForm

    respuesta = render(
        request,
        "tenants/terminos.html",
        {
            "gimnasio": perfil.gimnasio,
            "form": form if form is not None else AceptarTerminosForm(),
            "terminos_pendientes": True,
        },
        status=200,
    )
    add_never_cache_headers(respuesta)
    return respuesta
