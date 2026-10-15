"""Acciones en lote del listado de alumnos: las escrituras y sus topes.

`AlumnoLoteView` decide qué mostrar; acá vive lo que toca la base, para que la
regla de cada acción esté escrita una sola vez.

- **Fijar fecha** es UN `UPDATE` con `~Exists(Cuota…)`: a quien ya tiene
  cuotas emitidas no se le mueve el ancla (solaparía períodos y volvería a
  cobrar días ya cobrados; el `unique_together` de `Cuota` no lo impide). Es
  la misma regla que `alumnos/signals.py::anclar_ciclo_a_la_primera_rutina`.
  `modificado` va explícito porque `update()` no dispara `auto_now`.
- **Baja y reactivación** guardan alumno por alumno, NUNCA con `update()`:
  `registrar_fecha_de_baja` (estampa/limpia `fecha_baja` y re-ancla al
  reactivar) y `sincronizar_acceso_con_estado` (corta `User.is_active`) son
  señales, y `update()` no las dispara. `update_fields` lista lo que escribe
  el `pre_save`, igual que `AlumnoToggleEstadoView`.

El rango de la fecha de inicio NO se define acá: es `alumnos/ciclo.py`, la
misma fuente que usan `AlumnoForm` y el importador. Se re-exporta para quien
lea el lote desde este módulo.
"""

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.http import urlencode
from urllib.parse import parse_qsl

from alumnos.ciclo import (  # noqa: F401 (re-export: única fuente del rango)
    DIAS_MAX_INICIO_FUTURO,
    DIAS_MAX_INICIO_PASADO,
    rango_inicio_ciclo,
)
from alumnos.models import Alumno

# Fijar fecha es un solo UPDATE; baja y reactivación guardan alumno por alumno
# (tres queries cada uno: el estado previo del `pre_save`, el UPDATE y el
# `User.is_active`), y con el worker único de gunicorn 200 es ~1,2 s.
MAX_LOTE_FECHA = 1000
MAX_LOTE_ESTADO = 200

# Cuántos nombres se listan en un mensaje antes del «y N más».
MAX_NOMBRES = 10

# Únicas claves del listado que viajan al volver de una acción en lote.
FILTROS_LISTADO = ("q", "estado", "sin_fecha")


def _tiene_cuotas():
    from pagos.models import Cuota

    return Exists(Cuota.objects.filter(alumno=OuterRef("pk")))


def separar_por_cuotas(gimnasio, ids):
    """`(cambian, saltean)`: los alumnos de `ids` sin cuotas y con cuotas.

    Una sola query (`annotate` con `Exists`), para la pantalla de
    confirmación. Ordenados por apellido y nombre (el `Meta.ordering`).
    """
    alumnos = (
        Alumno.objects.for_gimnasio(gimnasio)
        .filter(pk__in=ids)
        .annotate(tiene_cuotas=_tiene_cuotas())
    )
    cambian, saltean = [], []
    for alumno in alumnos:
        (saltean if alumno.tiene_cuotas else cambian).append(alumno)
    return cambian, saltean


def fijar_fecha_inicio(gimnasio, ids, fecha):
    """Fija `fecha_inicio_ciclo = fecha` a los alumnos de `ids` sin cuotas.

    Devuelve `(cambiados, salteados)`: cuántas filas cambió el UPDATE y la
    lista de los que se saltearon por tener cuotas. El filtro de cuotas se
    evalúa en el UPDATE mismo y no se confía en lo que se mostró al confirmar:
    entre la confirmación y el POST el cron pudo haber emitido alguna.
    """
    base = Alumno.objects.for_gimnasio(gimnasio).filter(pk__in=ids)
    with transaction.atomic():
        cambiados = base.filter(~_tiene_cuotas()).update(
            fecha_inicio_ciclo=fecha, modificado=timezone.now()
        )
        salteados = list(base.filter(_tiene_cuotas()))
    return cambiados, salteados


def cambiar_estado(gimnasio, ids, estado):
    """Pasa a `estado` a los alumnos de `ids` que no lo tienen ya.

    Devuelve la lista de los que cambiaron (el mensaje los nombra). Los que ya
    estaban en `estado` se excluyen: un doble POST de la baja no re-estampa
    `fecha_baja` (la señal solo actúa en la transición, pero así ni se guarda).

    `select_related("perfil__usuario")` es lo que deja el costo en tres queries
    por alumno: la señal de acceso lee `instance.perfil.usuario`, que sin esto
    serían dos queries más cada uno. `of=("self",)` porque Postgres no deja
    lockear el lado nullable de un outer join (`perfil` es nullable). Lo
    pide la spec; en SQLite `select_for_update` es un no-op y no se prueba.
    """
    with transaction.atomic():
        alumnos = list(
            Alumno.objects.for_gimnasio(gimnasio)
            .filter(pk__in=ids)
            .exclude(estado=estado)
            .select_related("perfil__usuario")
            .select_for_update(of=("self",))
        )
        for alumno in alumnos:
            alumno.estado = estado
            alumno.save(
                update_fields=["estado", "fecha_baja", "fecha_inicio_ciclo", "modificado"]
            )
    return alumnos


def nombres(alumnos, maximo=MAX_NOMBRES):
    """«Pérez, Ana; Gómez, Beto y 3 más»: hasta `maximo` nombres."""
    alumnos = list(alumnos)
    texto = "; ".join(str(a) for a in alumnos[:maximo])
    resto = len(alumnos) - maximo
    if resto > 0:
        texto += f" y {resto} más"
    return texto


def filtros_seguros(texto):
    """Re-valida el querystring del listado: solo `FILTROS_LISTADO`, la
    primera aparición de cada clave, sin valores vacíos, re-codificado.

    Viaja en un hidden y vuelve en el redirect, así que es input del cliente:
    sin esto, un `filtros` armado a mano sería un open redirect
    (`//evil.com`) o arrastraría claves que el listado no conoce.
    """
    limpios = {}
    for clave, valor in parse_qsl(texto or ""):
        valor = valor.strip()
        if clave in FILTROS_LISTADO and valor and clave not in limpios:
            limpios[clave] = valor
    return urlencode(limpios)
