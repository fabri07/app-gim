"""Qué impide borrar un registro, y qué se lleva puesto si se borra.

El proyecto usa `on_delete=PROTECT` en todo lo que es historial (pagos,
rutinas asignadas, ejercicios usados en una plantilla) y `CASCADE` en lo que
solo tiene sentido acompañando al padre (reservas, novedades dirigidas,
credenciales de Calendar). Un `DeleteView` pelado convierte eso en un
`ProtectedError` -- una pantalla de error 500 -- en vez de una explicación.

Estas dos funciones leen el MODELO (no una lista escrita a mano) para que la
pantalla de confirmación pueda decir de antemano "no se puede, tiene 8 pagos"
o "se van a borrar también sus 12 reservas". Si mañana alguien agrega una FK
nueva, aparece sola.

El chequeo previo NO reemplaza al `try/except ProtectedError` del POST: entre
que se pinta la pantalla y se aprieta el botón puede aparecer un pago nuevo
(el cron los genera solo). Es defensa en profundidad, no adorno.
"""

from django.db.models import ProtectedError

__all__ = ["bloqueos_de_borrado", "arrastres_de_borrado", "ProtectedError"]


def _relaciones(objeto, on_delete_buscado):
    """(etiqueta en plural, cantidad) por cada relación entrante cuyo
    `on_delete` sea el buscado y que tenga al menos una fila."""
    encontrados = []
    for relacion in objeto._meta.related_objects:
        if relacion.on_delete is not on_delete_buscado:
            continue
        cantidad = relacion.related_model._default_manager.filter(
            **{relacion.field.name: objeto}
        ).count()
        if cantidad:
            modelo = relacion.related_model._meta
            etiqueta = modelo.verbose_name if cantidad == 1 else modelo.verbose_name_plural
            encontrados.append((str(etiqueta), cantidad))
    return sorted(encontrados)


def bloqueos_de_borrado(objeto):
    """Lo que IMPIDE borrar `objeto` (relaciones `PROTECT` con filas).

    Lista vacía = se puede borrar.
    """
    from django.db.models import PROTECT

    return _relaciones(objeto, PROTECT)


def arrastres_de_borrado(objeto):
    """Lo que se borraría JUNTO con `objeto` (relaciones `CASCADE` con filas).

    Sirve para que la confirmación no esconda el costo real: borrar un alumno
    sin historial de cobros igual se lleva sus reservas y las novedades
    dirigidas a él.
    """
    from django.db.models import CASCADE

    return _relaciones(objeto, CASCADE)


def frase(items):
    """`[("pagos", 8), ("rutina", 1)]` -> `"8 pagos y 1 rutina"`.

    En castellano y no con comas al final ("8 pagos, 1 rutina,") porque este
    texto lo lee el dueño del gimnasio, no un desarrollador.
    """
    partes = [f"{cantidad} {etiqueta}" for etiqueta, cantidad in items]
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " y " + partes[-1]
