"""Cola de moderación de la tienda: lo ve y lo resuelve el superadmin.

La lógica (transiciones, cola, contador) vive en `tienda.moderacion`; acá sólo
se arma la pantalla y se traduce el POST. Los errores de usuario van con
`messages` + redirect (bajo `hx-boost` un 4xx es un click mudo); la única
excepción es una acción que no existe, que es un 404 como en `EstadoCuentaView`.
"""

import mimetypes

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from plataforma.mixins import SuperadminRequiredMixin
from tienda import moderacion
from tienda.models import DenunciaProducto, FotoProducto, Producto

LARGO_MOTIVO = 300
YA_NO_ESTABA_FOTO = "Esa foto ya no estaba en la cola."
YA_NO_ESTABA = "Este producto ya no estaba en la cola."


def _enteros(valores):
    """Los pks que llegan del POST, sin los que no son enteros (nunca un 500)."""
    pks = []
    for valor in valores:
        try:
            pks.append(int(valor))
        except (TypeError, ValueError):
            continue
    return pks


def _con_marcas(productos):
    """Agrega `fotos_nuevas` (cuántas esperan aprobación) a cada producto.
    Las fotos ya vienen prefetcheadas: no suma queries."""
    for producto in productos:
        producto.fotos_nuevas = sum(1 for f in producto.fotos.all() if not f.aprobada)
    return productos


@method_decorator(never_cache, name="dispatch")
class ModeracionView(SuperadminRequiredMixin, View):
    def get(self, request):
        datos = moderacion.cola()
        return render(
            request,
            "plataforma/moderacion.html",
            {
                "pendientes": _con_marcas(datos["pendientes"]),
                "a_la_venta": _con_marcas(datos["a_la_venta"]),
                "demo": _con_marcas(datos["demo"]),
            },
        )


class FotoModeracionView(SuperadminRequiredMixin, View):
    """La foto de cualquier gimnasio, aprobada o no, para que el superadmin la
    vea en grande. Lee los bytes del storage: nunca `imagen.url` (en R2 es una
    URL firmada que se re-firma en cada render) y nada que se cachee."""

    def get(self, request, pk):
        foto = get_object_or_404(FotoProducto, pk=pk)
        with foto.imagen.open("rb") as archivo:
            contenido = archivo.read()
        tipo = mimetypes.guess_type(foto.imagen.name)[0] or "image/jpeg"
        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Cache-Control"] = "private, no-store"
        return respuesta


class ProductoModeracionView(SuperadminRequiredMixin, View):
    """«Aprobar» y «Bajar» un producto: GET de confirmación (con todo lo que el
    superadmin va a aprobar a la vista) y POST que aplica."""

    ACCIONES = ("aprobar", "bajar")

    def _accion(self):
        accion = self.kwargs["accion"]
        if accion not in self.ACCIONES:
            raise Http404("Esa acción no existe.")
        return accion

    def _producto(self):
        # Después del chequeo de acceso (el mixin corre primero).
        return get_object_or_404(
            Producto.objects.select_related("gimnasio", "categoria").prefetch_related(
                "fotos", "variantes"
            ),
            pk=self.kwargs["pk"],
        )

    def _pantalla(self, request, producto, accion, error=""):
        abiertas = list(
            DenunciaProducto.objects.filter(
                producto=producto, estado=DenunciaProducto.Estado.ABIERTA
            )
            .select_related("alumno")
            .order_by("creado", "pk")
        )
        return render(
            request,
            "plataforma/moderacion_confirmar.html",
            {
                "accion": accion,
                "producto": producto,
                "fotos": list(producto.fotos.all()),
                "variantes": list(producto.variantes.all()),
                "denuncias": abiertas,
                "motivo": request.POST.get("motivo", ""),
                "error_motivo": error,
                "largo_motivo": LARGO_MOTIVO,
            },
        )

    def get(self, request, pk, accion):
        accion = self._accion()
        return self._pantalla(request, self._producto(), accion)

    def post(self, request, pk, accion):
        accion = self._accion()
        producto = self._producto()
        if accion == "aprobar":
            return self._aprobar(request, producto)
        return self._bajar(request, producto)

    def _aprobar(self, request, producto):
        try:
            version = parse_datetime(request.POST.get("version", ""))
        except ValueError:
            version = None
        if version is None:
            # Sin versión válida no se puede saber qué vio: como si fuera vieja.
            messages.warning(
                request, "El producto cambió mientras lo mirabas: revisalo de nuevo."
            )
            return redirect("plataforma:moderacion")
        aprobado = moderacion.aprobar_producto(
            producto.pk,
            version=version,
            fotos_vistas=_enteros(request.POST.getlist("fotos_vistas")),
            denuncias_vistas=_enteros(request.POST.getlist("denuncias_vistas")),
        )
        if aprobado:
            messages.success(request, f"Aprobado: {producto.nombre} ya está a la venta.")
        else:
            self._avisar_no_aplico(request, producto)
        return redirect("plataforma:moderacion")

    @staticmethod
    def _avisar_no_aplico(request, producto):
        """Distingue «cambió mientras lo mirabas» de «ya no estaba en la cola»
        mirando el estado de la base."""
        vigente = Producto.objects.filter(
            pk=producto.pk,
            moderacion__in=[Producto.Moderacion.PENDIENTE, Producto.Moderacion.APROBADO],
        ).exists()
        if vigente:
            messages.warning(
                request, "El producto cambió mientras lo mirabas: revisalo de nuevo."
            )
        else:
            messages.info(request, YA_NO_ESTABA)

    def _bajar(self, request, producto):
        motivo = request.POST.get("motivo", "").strip()
        if not motivo:
            return self._pantalla(
                request, producto, "bajar", error="Escribí el motivo de la baja."
            )
        if len(motivo) > LARGO_MOTIVO:
            return self._pantalla(
                request, producto, "bajar",
                error=f"El motivo no puede pasar de {LARGO_MOTIVO} caracteres.",
            )
        cancelados = moderacion.bajar_producto(producto.pk, motivo=motivo)
        if cancelados is None:
            messages.info(request, YA_NO_ESTABA)
        else:
            messages.success(
                request,
                f"{producto.nombre} quedó dado de baja. "
                f"Se cancelaron {len(cancelados)} pedido(s) sin pagar.",
            )
        return redirect("plataforma:moderacion")


class FotoModeracionAccionView(SuperadminRequiredMixin, View):
    """«Aprobar» una foto es un POST directo desde la cola. «Rechazar» tiene
    pantalla de confirmación: borra la fila y le avisa al gimnasio."""

    ACCIONES = ("aprobar", "rechazar")

    def _accion(self):
        accion = self.kwargs["accion"]
        if accion not in self.ACCIONES:
            raise Http404("Esa acción no existe.")
        return accion

    def get(self, request, pk, accion):
        accion = self._accion()
        if accion == "aprobar":
            # Aprobar no tiene pantalla: un GET no puede aplicar el cambio.
            return redirect("plataforma:moderacion")
        foto = FotoProducto.objects.select_related("producto__gimnasio").filter(pk=pk).first()
        if foto is None:
            # Cola abierta en otra pestaña, o el staff la borró: cartel, no 404.
            messages.info(request, YA_NO_ESTABA_FOTO)
            return redirect("plataforma:moderacion")
        return render(
            request,
            "plataforma/moderacion_confirmar.html",
            {"accion": "rechazar_foto", "foto": foto, "producto": foto.producto},
        )

    def post(self, request, pk, accion):
        accion = self._accion()
        if accion == "aprobar":
            if moderacion.aprobar_foto(pk):
                messages.success(request, "Foto aprobada.")
            else:
                messages.info(request, YA_NO_ESTABA_FOTO)
        else:
            if moderacion.rechazar_foto(pk):
                messages.success(request, "Foto rechazada; le avisamos al gimnasio.")
            else:
                messages.info(request, YA_NO_ESTABA_FOTO)
        return redirect("plataforma:moderacion")
