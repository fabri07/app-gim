"""
Helpers para armar cuotas en los tests.

No lo importa ningún código de producción: existe porque el proyecto no usa
factories a propósito (ver la cabecera de `pagos/tests.py`) y, desde que la
cuota dejó de ser `(mes, año)`, cada `Cuota.objects.create(...)` necesita las
dos fechas del período. Repetir ese cálculo en ~60 lugares es justo la clase de
duplicación que hace que un cambio futuro quede a medias.
"""

from datetime import date, timedelta

from pagos.models import DIAS_CICLO, Cuota


def crear_cuota(*, gimnasio, alumno, inicio, dias=DIAS_CICLO, **extra):
    """Una cuota que cubre `dias` días desde `inicio` (`periodo_fin` inclusivo)."""
    extra.setdefault("monto", 0)
    return Cuota.objects.create(
        gimnasio=gimnasio,
        alumno=alumno,
        periodo_inicio=inicio,
        periodo_fin=inicio + timedelta(days=dias - 1),
        **extra,
    )


def crear_cuota_mensual(*, gimnasio, alumno, mes, anio, **extra):
    """Una cuota que cubre un mes calendario entero.

    Es la forma que tienen las cuotas históricas después del backfill, así que
    sirve para los tests que solo necesitan "una cuota que exista" y para los
    que verifican la convivencia entre el histórico mensual y los ciclos
    nuevos.
    """
    import calendar

    inicio = date(anio, mes, 1)
    return crear_cuota(
        gimnasio=gimnasio,
        alumno=alumno,
        inicio=inicio,
        dias=calendar.monthrange(anio, mes)[1],
        **extra,
    )
