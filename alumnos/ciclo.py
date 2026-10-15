"""Rango válido de la fecha de inicio del ciclo de cobro de un alumno.

Django-free a propósito (ni imports de django ni de modelos): lo comparten el
lector del archivo de importación, la acción en lote y `AlumnoForm`, y los tres
tienen que aceptar exactamente las mismas fechas. Es la única fuente de
`[hoy - 27, hoy + 60]`; `hoy` lo pasa siempre quien llama.
"""

from datetime import timedelta

# 27 y no 28: el ciclo vigente cae siempre en [hoy - 27, hoy]; una ancla más
# vieja ya habría emitido (y vencido) cuotas que nadie cobró.
DIAS_MAX_INICIO_PASADO = 27
DIAS_MAX_INICIO_FUTURO = 60


def rango_inicio_ciclo(hoy):
    """`(desde, hasta)` inclusivos de las fechas de inicio aceptadas."""
    return (
        hoy - timedelta(days=DIAS_MAX_INICIO_PASADO),
        hoy + timedelta(days=DIAS_MAX_INICIO_FUTURO),
    )
