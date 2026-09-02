"""Vistas genéricas compartidas por las apps de dominio."""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.views.generic import DetailView

from core.borrado import (
    ProtectedError,
    arrastres_de_borrado,
    bloqueos_de_borrado,
    frase,
)


class BorrarConExplicacionView(DetailView):
    """Borra un registro, o explica en castellano por qué no se puede.

    Un `DeleteView` pelado sobre este proyecto es una fábrica de errores 500:
    casi todo el historial cuelga con `on_delete=PROTECT` (pagos y rutinas de
    un alumno, plantillas que usan un ejercicio), así que el borrado revienta
    con `ProtectedError` justo en los casos más comunes.

    Decisión de producto (2026-09-02): borrar de verdad lo que NO tiene
    historial -- lo cargado por error o de prueba, que es el caso real -- y
    cuando no se puede, decir qué lo impide y ofrecer la alternativa que ya
    existe (dar de baja al alumno, desactivar el ejercicio). Nunca borrar
    historial de cobros en cascada.

    GET muestra la confirmación; POST borra. La confirmación NO es opcional:
    el precedente de POST-sin-confirmación del proyecto es
    `rutinas:item_eliminar`, un ejercicio suelto dentro de una plantilla --
    acá se borra un alumno o un plan entero, y un click accidental no se
    deshace.

    Las subclases definen `template_name`, `url_exito`, `titulo`,
    `alternativa` (qué hacer si está bloqueado) y `mensaje_exito`.
    """

    template_name = "core/confirmar_borrado.html"
    context_object_name = "objeto"
    #: Texto que ofrece la salida cuando el borrado está bloqueado.
    alternativa = ""

    def get_url_exito(self):
        raise NotImplementedError

    def get_titulo(self):
        return f"Eliminar {self.object}"

    def get_mensaje_exito(self):
        return "Eliminado."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bloqueos"] = frase(bloqueos_de_borrado(self.object))
        context["arrastres"] = frase(arrastres_de_borrado(self.object))
        context["titulo"] = self.get_titulo()
        context["alternativa"] = self.alternativa
        context["url_cancelar"] = self.get_url_exito()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            with transaction.atomic():
                self.object.delete()
        except ProtectedError as error:
            # El chequeo del GET puede haber quedado viejo: el cron de pagos
            # genera filas solas. Este `except` es el guard real, no el
            # preview.
            # Se re-cuenta con el mismo helper del preview en vez de contar
            # `error.protected_objects`: ese set trae INSTANCIAS y armar el
            # texto desde ahí daría "1 pagos" para 8 filas del mismo modelo.
            bloqueos = frase(bloqueos_de_borrado(self.object))
            messages.error(
                request,
                f"No se puede eliminar: tiene {bloqueos} asociados. "
                + self.alternativa,
            )
            return redirect(request.path)
        messages.success(request, self.get_mensaje_exito())
        return redirect(self.get_url_exito())
