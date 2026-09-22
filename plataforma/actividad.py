"""
Cuánto se usa cada gimnasio, leído de `ActividadDiaria`.

Separado de `plataforma/facturacion.py` por el mismo criterio con el que
`tenants/analitica.py` está separado de las vistas: acá vive el ORM de los
indicadores de uso y se testea sin pasar por ninguna pantalla.

**Regla de costo, la misma que el resto del panel: un indicador, una consulta
agregada.** Nunca un bucle por día ni una query por rol. El relleno de los días
vacíos se hace en Python sobre las filas ya traídas.

**Una fila por día aunque esté vacía.** Un gráfico que se saltea los días sin
actividad hace que dos días separados por un hueco se vean contiguos: miente
sobre la tendencia, que es justamente lo único que esta pantalla tiene que
contar.
"""

from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from plataforma.models import ActividadDiaria
from tenants.models import Perfil

#: Cuántos días muestra la ficha. Treinta entra en un gráfico de barras sin
#: que las etiquetas se pisen y cubre un ciclo de facturación completo, que es
#: la pregunta real: «¿lo usó el mes que le estoy por cobrar?».
DIAS_DE_ACTIVIDAD = 30


def activos_por_dia(gimnasio, *, dias=DIAS_DE_ACTIVIDAD, hoy=None):
    """Cuántas personas distintas usaron la app cada día, separadas por rol.

    `hoy` es parámetro (y por defecto `timezone.localdate()`, nunca
    `timezone.now().date()`) para que los tests fijen fechas y para que la
    ventana entera quede fechada igual aunque la carga cruce la medianoche.

    Cuenta filas y no usuarios distintos porque la clave única
    `(usuario, fecha)` ya garantiza que sean lo mismo: un `DISTINCT` acá sería
    trabajo de más escondiendo esa garantía.
    """
    hoy = hoy or timezone.localdate()
    ventana = [hoy - timedelta(days=n) for n in reversed(range(dias))]

    # El filtro por `ventana[0]` no es cosmético: sin él la consulta agrega la
    # tabla histórica entera y recién después se descarta lo que sobra.
    conteo = {
        (fila["fecha"], fila["rol"]): fila["total"]
        for fila in ActividadDiaria.objects.filter(
            gimnasio=gimnasio, fecha__gte=ventana[0], fecha__lte=hoy
        )
        .values("fecha", "rol")
        .annotate(total=Count("id"))
    }
    return [
        {
            "fecha": fecha,
            "etiqueta": f"{fecha.day:02d}/{fecha.month:02d}",
            "staff": conteo.get((fecha, Perfil.Rol.STAFF), 0),
            "alumnos": conteo.get((fecha, Perfil.Rol.ALUMNO), 0),
        }
        for fecha in ventana
    ]


def ultimo_uso(gimnasio):
    """`{"staff": date | None, "alumnos": date | None}`: el último día que
    entró cada lado del gimnasio.

    Los dos roles salen de UN `aggregate` con `filter=Q(...)` y no de dos
    consultas: es el mismo criterio de "un indicador, una query", y además
    garantiza que las dos fechas estén leídas del mismo estado de la tabla.

    `None` significa «nunca entró» y es un valor con significado propio: un
    gimnasio cuyo staff nunca entró es un alta que no se activó, no un
    gimnasio con cero días de uso.
    """
    return ActividadDiaria.objects.filter(gimnasio=gimnasio).aggregate(
        staff=Max("fecha", filter=Q(rol=Perfil.Rol.STAFF)),
        alumnos=Max("fecha", filter=Q(rol=Perfil.Rol.ALUMNO)),
    )
