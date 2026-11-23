"""Regla de «el alumno debe aceptar los términos y condiciones».

No importa nada de `plataforma` (su middleware consume esto; `plataforma.models`
importa `tenants.models`).
"""

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers

# Subirla obliga a todos los alumnos a volver a aceptar.
VERSION_TERMINOS = 1


def debe_aceptar_terminos(perfil):
    """True si hay que frenar al alumno con la pantalla de términos.

    Sin queries mientras no haya nada pendiente: lee `perfil.gimnasio`, que el
    request ya resolvió. Sólo si la versión está pendiente mira la ficha del
    alumno (una query): un menor de 18 no participa en ningún ranking, así que
    no hay nada que consentir y nunca se le muestra el cartel. Sin ficha se
    mantiene el comportamiento de siempre (debe aceptar).
    """
    if perfil is None or perfil.rol != perfil.Rol.ALUMNO:
        return False
    if not perfil.gimnasio.reconocimientos_habilitado:
        return False
    if perfil.terminos_version >= VERSION_TERMINOS:
        return False
    try:
        alumno = perfil.alumno
    except ObjectDoesNotExist:
        return True
    return alumno.es_mayor_de_edad


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
