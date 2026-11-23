"""Mayoría de edad del alumno (los menores no participan en ningún ranking).

Módulo sin imports de modelos, para que lo usen `alumnos.models`, el ranking
y los términos sin ciclos. Sin fecha de nacimiento el alumno NO es mayor: no
se puede verificar, así que queda afuera.
"""

from datetime import date

from django.utils import timezone

MAYORIA_DE_EDAD = 18


def fecha_corte_mayoria(hoy=None):
    """La última fecha de nacimiento que ya es mayor de edad.

    Filtro de queryset: `fecha_nacimiento__lte=fecha_corte_mayoria()`; los
    NULL quedan afuera solos. Quien nació un 29-feb cumple 18 el 1-mar del año
    en que el aniversario no existe: ese día el corte es el 28-feb.
    """
    hoy = hoy or timezone.localdate()
    try:
        return hoy.replace(year=hoy.year - MAYORIA_DE_EDAD)
    except ValueError:  # hoy es 29-feb y el año de corte no es bisiesto
        return date(hoy.year - MAYORIA_DE_EDAD, 2, 28)


def es_mayor_de_edad(fecha_nacimiento, hoy=None):
    if fecha_nacimiento is None:
        return False
    return fecha_nacimiento <= fecha_corte_mayoria(hoy)
