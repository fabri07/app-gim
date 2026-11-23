"""Foto del alumno (reconocimientos): guardar, quitar y borrar del storage.

La columna se escribe con `QuerySet.update()` y NO con `alumno.save()`: el
`save()` dispararía las señales de `Alumno` (`registrar_fecha_de_baja` hace un
SELECT extra, `sincronizar_acceso_con_estado` lee `perfil.usuario`) para un
cambio que no toca `estado`. Como `update()` no dispara `auto_now`, se estampa
`modificado` a mano.

El archivo viejo se borra con `transaction.on_commit`: si la transacción se
revierte (p. ej. `vaciar_gimnasio` corre dentro de `transaction.atomic()`), la
fila sigue apuntando al archivo y borrarlo antes dejaría una foto rota.
"""

import logging

from django.db import transaction
from django.utils import timezone

from alumnos.models import Alumno

logger = logging.getLogger(__name__)


def guardar_foto(alumno, archivo):
    """Valida y re-codifica `archivo` (JPEG, nombre uuid) y lo deja como la
    foto del alumno. Deja pasar `forms.ValidationError` sin guardar nada."""
    # Import tardío: `tienda` importa `alumnos`; a nivel de módulo sería un ciclo.
    from tienda.fotos import preparar_foto

    contenido = preparar_foto(archivo)
    anterior = alumno.foto.name
    storage = alumno.foto.storage
    nombre = storage.save(
        alumno.foto.field.generate_filename(alumno, contenido.name), contenido
    )
    _escribir_columna(alumno, nombre)
    if anterior:
        borrar_al_confirmar(storage, anterior)


def quitar_foto(alumno):
    anterior = alumno.foto.name
    if not anterior:
        return
    storage = alumno.foto.storage
    _escribir_columna(alumno, "")
    borrar_al_confirmar(storage, anterior)


def borrar_al_confirmar(storage, nombre):
    """Borra `nombre` cuando la transacción en curso confirma (en el acto si no
    hay ninguna). Un archivo que ya no está no es un error: sólo se registra."""

    def borrar():
        try:
            storage.delete(nombre)
        except Exception:
            logger.warning("No se pudo borrar la foto %s.", nombre, exc_info=True)

    transaction.on_commit(borrar)


def _escribir_columna(alumno, nombre):
    ahora = timezone.now()
    Alumno.objects.filter(pk=alumno.pk).update(foto=nombre, modificado=ahora)
    alumno.foto = nombre
    alumno.modificado = ahora
