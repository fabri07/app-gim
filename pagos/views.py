"""Vistas de gestión (Fase 2 §6) de pagos mensuales de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). No hay vista de creación: los `Cuota` PENDIENTE
ya existen, autogenerados por el cron de Fase 1 (`generar_pagos_pendientes`).
La única acción de escritura del staff es confirmar un pago existente
(`ConfirmarPagoView`), operando siempre sobre un pk ya acotado al tenant por
`TenantScopedMixin.get_queryset` -- por eso un pk de otro gimnasio da 404, no
403 (Django resuelve `SingleObjectMixin.get_object` sobre ese queryset).
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView


from core.mixins import TenantScopedMixin
from tenants import suplantacion
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from pagos.forms import (
    AlumnoComprobanteForm,
    ConfirmarPagoForm,
    CuotaMontoForm,
    MedioCobroForm,
)
from pagos import acceso, cobro
from pagos.models import CobroOnlineCuota, Cuota, MedioCobro

logger = logging.getLogger(__name__)


def _fecha_o_nada(texto):
    """`parse_date` devuelve `None` con un texto mal formado, pero LANZA
    `ValueError` con uno bien formado e imposible (`2026-02-31`). Sin esto,
    `?desde=2026-02-31` era un 500."""
    if not texto:
        return None
    try:
        return parse_date(texto)
    except ValueError:
        return None


class CuotaListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    model = Cuota
    template_name = "pagos/pago_list.html"
    context_object_name = "pagos"

    def get_queryset(self):
        """Filtros por RANGO DE FECHAS, no por mes/año.

        Con cuotas ancladas a cada alumno, "el mes" dejó de ser una unidad de
        cobro: un ciclo de 28 días que arranca el 28 de marzo cae casi entero
        en abril. Un rango `desde`/`hasta` sobre `periodo_inicio` responde la
        misma pregunta sin mentir. Las dos fechas son opcionales e
        independientes; una fecha mal tipeada se ignora en vez de dar 500.
        """
        queryset = super().get_queryset().select_related("alumno")
        self.desde = self.request.GET.get("desde", "")
        self.hasta = self.request.GET.get("hasta", "")
        self.estado = self.request.GET.get("estado", "")

        if (desde := _fecha_o_nada(self.desde)) is not None:
            queryset = queryset.filter(periodo_inicio__gte=desde)
        if (hasta := _fecha_o_nada(self.hasta)) is not None:
            queryset = queryset.filter(periodo_inicio__lte=hasta)

        if self.estado == "deudores":
            queryset = queryset.filter(estado__in=Cuota.ESTADOS_IMPAGOS)
        elif self.estado:
            queryset = queryset.filter(estado=self.estado)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["desde_actual"] = self.desde
        context["hasta_actual"] = self.hasta
        context["estado_actual"] = self.estado
        context["estados"] = Cuota.Estado.choices
        return context


class ConfirmarPagoView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """Confirma el pago de una cuota impaga, o edita una ya pagada.

    Con cobro online, la cuota puede pasar a PAGADA por Mercado Pago mientras
    el staff tiene este form abierto. El form viaja con el estado que la cuota
    tenía al abrirlo (`estado_al_abrir`) y la escritura pasa por
    `pagos.cobro`, con lock y releyendo la base:

    - abierta impaga → `marcar_pagada`, que NO guarda si MP ya la cobró;
    - abierta pagada → `editar_pagada`, que NO guarda si en el medio dejó de
      estar pagada (un pago devuelto que el staff volvió a impaga).

    En los dos casos el rechazo es un mensaje y un redirect, nunca un 4xx
    (bajo `hx-boost` sería un click mudo).
    """

    model = Cuota
    form_class = ConfirmarPagoForm
    template_name = "pagos/pago_confirmar.html"
    success_url = reverse_lazy("pagos:listado")

    def form_valid(self, form):
        # `estado` no es un campo del form: la instancia conserva el que leyó
        # `get_object`, que es el respaldo si el POST no trae el oculto.
        abierta_como = form.cleaned_data.get("estado_al_abrir") or self.object.estado
        cuota = form.save(commit=False)
        if abierta_como == Cuota.Estado.PAGADO:
            if not cobro.editar_pagada(cuota):
                messages.warning(
                    self.request,
                    "Esta cuota cambió mientras la editabas (ya no figura como "
                    "pagada); no se guardó tu cambio. Revisala en el listado.",
                )
                return redirect(self.success_url)
        elif not cobro.marcar_pagada(cuota):
            messages.warning(
                self.request,
                "Esta cuota se acaba de cobrar por Mercado Pago; no se guardó tu cambio.",
            )
            return redirect(self.success_url)
        self.object = cuota
        _avisar_si_sigue_bloqueado(self.request, self.object, "Pago confirmado")
        return redirect(self.get_success_url())



def _avisar_si_sigue_bloqueado(request, cuota, mensaje_ok):
    """El mensaje de éxito de cualquier acción que destraba una cuota.

    Si al alumno le queda OTRA cuota bloqueándolo, decirlo. Un «listo» a
    secas sobre alguien que sigue sin poder abrir su rutina es una mentira
    que el staff descubre por el reclamo del alumno.
    """
    bloqueo = acceso.bloqueo_de(cuota.alumno)
    if bloqueo is None:
        messages.success(request, f"{mensaje_ok}.")
    else:
        messages.warning(
            request,
            f"{mensaje_ok}, pero {cuota.alumno} sigue con el acceso "

            f"pausado por la cuota del "
            f"{bloqueo.cuota.periodo_inicio:%d/%m/%Y}, que también está "
            f"impaga.",
        )


class CuotaAnularView(StaffRequiredMixin, TenantScopedMixin, DetailView):
    """Condona una cuota (`Estado.ANULADO`), o deshace la condonación.

    Es la ÚNICA salida del staff para sacar una cuota del conjunto que bloquea
    sin falsear la facturación: marcar PAGADO a un becado o a alguien de
    licencia suma a `ingresos_por_mes` un dinero que no entró. El estado
    existía desde la migración a ciclos pero ninguna vista lo escribía -- solo
    se podía desde `/admin/`, y el filtro «Anulada» del listado no podía
    matchear nunca nada.

    Con pantalla de confirmación a propósito, mismo criterio que
    `BorrarConExplicacionView`: anular no borra, pero saca la cuota de la
    deuda del alumno y del panel del staff, y desde el listado es un click.

    Una cuota PAGADA no se anula (da 404, igual que la de otro gimnasio): si
    el cobro fue un error, se edita desde «Confirmar pago». Una cuota ya
    anulada vuelve a PENDIENTE; si su plazo ya pasó, la vence el próximo cron.

    Anular y reactivar son UPDATE condicionales (`pagos.cobro`): si entre que
    se leyó la cuota y se escribe la cobró Mercado Pago, no se anula y se
    dice. Anular cancela los links de pago vivos.
    """

    model = Cuota
    template_name = "pagos/pago_anular.html"
    context_object_name = "cuota"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .exclude(estado=Cuota.Estado.PAGADO)
            .select_related("alumno")
        )

    def post(self, request, *args, **kwargs):
        cuota = self.get_object()
        if cuota.estado == Cuota.Estado.ANULADO:
            if cobro.reactivar(cuota.pk):
                messages.success(
                    request,
                    f"La cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                    f"vuelve a estar pendiente.",
                )
            else:
                messages.warning(
                    request,
                    f"No se reactivó: la cuota del {cuota.periodo_inicio:%d/%m/%Y} "
                    f"de {cuota.alumno} cambió mientras tanto.",
                )
        elif cobro.anular(cuota.pk):
            _avisar_si_sigue_bloqueado(
                request,
                cuota,
                f"Cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                f"anulada",
            )
        else:
            messages.warning(
                request,
                f"No se anuló: la cuota del {cuota.periodo_inicio:%d/%m/%Y} de "
                f"{cuota.alumno} ya no está impaga (se acaba de pagar).",
            )
        return redirect("pagos:listado")


class CuotaMontoView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """Corrige el monto de una cuota IMPAGA (`pagos:monto`).

    Existe aparte de «Confirmar pago» porque cambiar lo que se le cobra al
    alumno no es registrar un cobro. Cancela los links de pago vivos: cobran
    el monto viejo. Una pagada o anulada da 404; si se paga entre la lectura y
    la escritura, el UPDATE condicional no la toca y se dice.
    """

    model = Cuota
    form_class = CuotaMontoForm
    template_name = "pagos/pago_monto.html"
    context_object_name = "cuota"
    success_url = reverse_lazy("pagos:listado")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(estado__in=Cuota.ESTADOS_IMPAGOS)
            .select_related("alumno")
        )

    def form_valid(self, form):
        cuota = self.object
        if cobro.corregir_monto(cuota.pk, self.gimnasio, form.cleaned_data["monto"]):
            messages.success(
                self.request,
                f"Monto de la cuota del {cuota.periodo_inicio:%d/%m/%Y} de "
                f"{cuota.alumno} corregido.",
            )
        else:
            messages.warning(
                self.request,
                f"No se corrigió: la cuota del {cuota.periodo_inicio:%d/%m/%Y} de "
                f"{cuota.alumno} ya no está impaga.",
            )
        return redirect(self.success_url)


class CuotaVolverAImpagaView(StaffRequiredMixin, TenantScopedMixin, View):
    """«Volver a impaga» (`pagos:volver_a_impaga`, sólo POST): para una cuota
    PAGADA cuyo cobro por Mercado Pago se devolvió.

    La devolución NO la vuelve impaga sola (puede estar compensada en
    efectivo); lo decide el staff. Sin esta acción la plata devuelta seguiría
    sumando en los ingresos y el alumno seguiría desbloqueado. Cualquier otra
    cuota (pagada a mano, pagada por MP sin devolución) no se toca: mensaje y
    redirect, nunca un 4xx.
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        cuota = get_object_or_404(
            Cuota.objects.for_gimnasio(self.gimnasio).select_related("alumno", "gimnasio"),
            pk=pk,
        )
        if cobro.volver_a_impaga(cuota):
            messages.success(
                request,
                f"La cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                f"vuelve a estar impaga.",
            )
        else:
            messages.warning(
                request,
                f"La cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                f"no tiene un cobro de Mercado Pago devuelto: no se cambió.",
            )
        return redirect("pagos:listado")


class AlumnoComprobanteUpdateView(AlumnoRequiredMixin, UpdateView):
    """El alumno sube el comprobante de SU PROPIO pago PENDIENTE/VENCIDO.
    No cambia `estado`: sigue siendo el staff quien confirma el pago en
    `ConfirmarPagoView`. `get_queryset` acota por alumno, gimnasio Y estado
    -- un pago ya PAGADO, de otro alumno, o de otro gimnasio da 404, mismo
    criterio que `RutinaAsignadaItemCalificarView`/`CancelarReservaView`."""

    model = Cuota
    form_class = AlumnoComprobanteForm
    template_name = "pagos/comprobante_alumno_form.html"
    success_url = reverse_lazy("home")

    def get_queryset(self):
        if self.alumno is None:
            return Cuota.objects.none()
        return Cuota.objects.filter(
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            estado__in=Cuota.ESTADOS_IMPAGOS,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Los mismos medios que ve en el inicio: el alumno que llega acá desde
        # «Subir comprobante» necesita el alias a la vista para transferir.
        context["medios_cobro"] = MedioCobro.objects.for_gimnasio(
            self.gimnasio
        ).filter(activo=True)
        context["gimnasio"] = self.gimnasio
        return context

    def get(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """Sube el archivo y guarda SÓLO `comprobante` (+ `modificado`) con un
        UPDATE condicional a que la cuota siga impaga.

        Antes era un `save()` completo de la instancia leída al principio del
        request: si en el medio Mercado Pago la cobraba, el `save()` la volvía
        a PENDIENTE. Subir el comprobante sigue sin desbloquear (no toca
        `estado`) y se sigue pudiendo hacer estando bloqueado.
        """
        cuota = form.save(commit=False)
        campo = Cuota._meta.get_field("comprobante")
        nuevo = bool(cuota.comprobante) and not cuota.comprobante._committed
        # `pre_save` es lo que hace `save()` con un `FileField`: sube el
        # archivo nuevo al storage y deja su nombre en la instancia.
        archivo = campo.pre_save(cuota, add=False)
        guardado = Cuota.objects.filter(
            pk=cuota.pk,
            gimnasio=self.gimnasio,
            alumno=self.alumno,
            estado__in=Cuota.ESTADOS_IMPAGOS,
        ).update(comprobante=archivo.name or "", modificado=timezone.now())
        if not guardado:
            if nuevo:
                try:
                    campo.storage.delete(archivo.name)
                except Exception:  # un huérfano en el bucket no es un error del alumno
                    pass
            messages.info(
                self.request,
                "Esta cuota ya no está pendiente de pago, así que no hizo falta "
                "el comprobante.",
            )
            return redirect(self.success_url)

        self.object = cuota
        messages.success(self.request, "Comprobante subido. El staff lo va a revisar.")
        from notificaciones import services as notificaciones_services

        transaction.on_commit(lambda: notificaciones_services.notificar_comprobante_subido(cuota))
        return redirect(self.get_success_url())


class MedioCobroListView(StaffRequiredMixin, TenantScopedMixin, ListView):
    """Lista TODOS los medios de cobro del gimnasio (activos e inactivos):
    el staff necesita ver los inactivos para poder reactivarlos, a
    diferencia de lo que ve el alumno en el portal (Task 12), que solo
    debe ver los `activo=True`."""

    model = MedioCobro
    template_name = "pagos/medio_list.html"
    context_object_name = "medios"


class MedioCobroCreateView(StaffRequiredMixin, TenantScopedMixin, CreateView):
    model = MedioCobro
    form_class = MedioCobroForm
    template_name = "pagos/medio_form.html"
    success_url = reverse_lazy("pagos:medios_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Medio de cobro creado correctamente.")
        return response


class MedioCobroUpdateView(StaffRequiredMixin, TenantScopedMixin, UpdateView):
    """No hay `MedioCobroDeleteView`: "eliminar" un medio de cobro es editarlo
    acá y destildar `activo` (mismo patrón que `Novedad.activa` en
    `NovedadUpdateView`) -- se conserva el historial en vez de borrar filas
    a las que pueden referirse pagos históricos."""

    model = MedioCobro
    form_class = MedioCobroForm
    template_name = "pagos/medio_form.html"
    success_url = reverse_lazy("pagos:medios_listado")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Medio de cobro actualizado correctamente.")
        return response


# --- Cuotas por Mercado Pago (el alumno paga) ----------------------------------------


class PagarCuotaMPView(AlumnoRequiredMixin, View):
    """«Pagar con Mercado Pago»: genera (o reusa) el link de pago de la cuota
    en la cuenta de MP del gimnasio y manda al alumno ahí.

    Sólo POST (un GET es un 405: un link no puede abrir un cobro). Nunca un
    4xx por una regla de negocio: bajo `hx-boost` sería un click mudo (el form
    va con `hx-boost="false"`, pero un POST repetido desde una pestaña vieja
    igual tiene que terminar en una pantalla que explique). Una cuota que no
    es del alumno sí es un 404.

    NO mira `acceso.bloqueo_de`: pagar es justamente la salida del bloqueo.
    Mientras el staff suplanta al alumno no se paga nada: la plata sería del
    staff, no del alumno."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from pagos import cobro_mp

        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        cuota = get_object_or_404(
            Cuota.objects.filter(gimnasio=self.gimnasio, alumno=self.alumno),
            pk=kwargs["pk"],
        )
        if suplantacion.esta_activa(request):
            messages.error(
                request, "Mientras ves la app como un alumno no podés pagar sus cuotas."
            )
            return redirect("home")
        try:
            link = cobro_mp.link_de_pago(
                cuota.pk,
                self.alumno,
                url_retorno_para=lambda referencia: request.build_absolute_uri(
                    reverse("pagos:retorno_mp", args=[referencia])
                ),
                url_notificacion_para=lambda uuid_webhook: request.build_absolute_uri(
                    reverse("tienda:mp_webhook", args=[uuid_webhook])
                ),
            )
        except cobro_mp.NoSePuedePagar as error:
            messages.error(request, error.mensaje)
            return redirect("home")
        return HttpResponseRedirect(link)


@method_decorator(never_cache, name="dispatch")
class RetornoCuotaMPView(AlumnoRequiredMixin, View):
    """Adonde vuelve el navegador desde Mercado Pago después de pagar una
    cuota. Siempre termina en el inicio, con un mensaje.

    El querystring es dato del cliente: el `status` se IGNORA, la única
    autoridad es leer el pago en la API con el token del gimnasio
    (`cobro_mp.acreditar_cuota_por_id`). Si eso falla, «Estamos confirmando tu
    pago»: el webhook y la reconciliación lo acreditan igual.

    Con la cuenta del gimnasio en `ALUMNOS_BLOQUEADOS` el middleware de la
    plataforma no deja llegar acá (comportamiento conocido y aceptado): el
    pago lo acreditan el webhook y la reconciliación."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        from pagos import cobro_mp
        from tienda.models import CuentaMercadoPago

        if self.alumno is None:
            raise Http404
        intento = get_object_or_404(
            CobroOnlineCuota.objects.filter(
                gimnasio=self.gimnasio, cuota__alumno=self.alumno
            ),
            referencia_externa=kwargs["referencia"],
        )
        payment_id = next(
            (
                valor.strip()
                for valor in (
                    request.GET.get("payment_id", ""),
                    request.GET.get("collection_id", ""),
                )
                # Mercado Pago manda el texto «null» cuando no hubo pago.
                if valor.strip() and valor.strip().lower() != "null"
            ),
            "",
        )
        if not payment_id:
            messages.info(
                request,
                "No se registró ningún pago. Podés intentarlo de nuevo o subir el comprobante.",
            )
            return redirect("home")

        confirmado = False
        if settings.TIENDA_MP_ENABLED:
            cuenta = CuentaMercadoPago.objects.filter(gimnasio=self.gimnasio).first()
            if cuenta is not None and cuenta.access_token:
                try:
                    cobro_mp.acreditar_cuota_por_id(cuenta, payment_id)
                    confirmado = True
                except Exception as exc:  # noqa: BLE001 - lo acreditan el webhook y la reconciliación
                    logger.warning(
                        "No se pudo acreditar al volver de Mercado Pago el cobro de cuota %s: %s",
                        intento.pk, type(exc).__name__,
                    )
        if not confirmado:
            messages.info(
                request, "Estamos confirmando tu pago. Te avisamos cuando se acredite."
            )
            return redirect("home")

        intento.refresh_from_db()
        cuota = Cuota.objects.get(pk=intento.cuota_id)
        _E = CobroOnlineCuota.Estado
        if intento.estado == _E.PAGADO and cuota.estado == Cuota.Estado.PAGADO:
            if acceso.bloqueo_de(self.alumno) is None:
                messages.success(request, "¡Listo, tu cuota quedó paga!")
            else:
                messages.warning(
                    request,
                    "Tu cuota quedó paga, pero tenés otra cuota impaga que sigue "
                    "pausando tu acceso. Pagala para volver a entrenar.",
                )
        elif intento.estado in (_E.REVISAR, _E.DUPLICADO):
            messages.info(
                request,
                "Recibimos tu pago. El gimnasio lo tiene que revisar antes de "
                "darlo por bueno.",
            )
        else:
            messages.info(
                request,
                "No se registró ningún pago. Podés intentarlo de nuevo o subir el comprobante.",
            )
        return redirect("home")
