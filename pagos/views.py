"""Vistas de gestión (Fase 2 §6) de pagos mensuales de un gimnasio.

Solo staff (`StaffRequiredMixin`) y siempre acotado al gimnasio del usuario
(`TenantScopedMixin`). No hay vista de creación: los `Cuota` PENDIENTE
ya existen, autogenerados por el cron de Fase 1 (`generar_pagos_pendientes`).
La única acción de escritura del staff es confirmar un pago existente
(`ConfirmarPagoView`), operando siempre sobre un pk ya acotado al tenant por
`TenantScopedMixin.get_queryset` -- por eso un pk de otro gimnasio da 404, no
403 (Django resuelve `SingleObjectMixin.get_object` sobre ese queryset).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect

from django.urls import reverse_lazy
from django.utils.dateparse import parse_date
from django.views.generic import CreateView, DetailView, ListView, UpdateView


from core.mixins import TenantScopedMixin
from tenants.mixins import AlumnoRequiredMixin, StaffRequiredMixin
from pagos.forms import AlumnoComprobanteForm, ConfirmarPagoForm, MedioCobroForm
from pagos import acceso
from pagos.models import MedioCobro, Cuota


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
    model = Cuota
    form_class = ConfirmarPagoForm
    template_name = "pagos/pago_confirmar.html"
    success_url = reverse_lazy("pagos:listado")

    def form_valid(self, form):
        form.instance.estado = Cuota.Estado.PAGADO
        response = super().form_valid(form)
        _avisar_si_sigue_bloqueado(self.request, self.object, "Pago confirmado")

        return response



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
            cuota.estado = Cuota.Estado.PENDIENTE
            cuota.save(update_fields=["estado", "modificado"])
            messages.success(
                request,
                f"La cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                f"vuelve a estar pendiente.",
            )
        else:
            cuota.estado = Cuota.Estado.ANULADO
            cuota.save(update_fields=["estado", "modificado"])
            _avisar_si_sigue_bloqueado(
                request,
                cuota,
                f"Cuota del {cuota.periodo_inicio:%d/%m/%Y} de {cuota.alumno} "
                f"anulada",

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

    def get(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.alumno is None:
            raise PermissionDenied("Todavía no tenés una ficha de alumno vinculada.")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Comprobante subido. El staff lo va a revisar.")
        pago = self.object
        from notificaciones import services as notificaciones_services

        transaction.on_commit(lambda: notificaciones_services.notificar_comprobante_subido(pago))
        return response


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
