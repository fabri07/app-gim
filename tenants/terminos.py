"""Regla de «el alumno debe aceptar los términos y condiciones».

No importa nada de `plataforma` (su middleware consume esto; `plataforma.models`
importa `tenants.models`).
"""

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
