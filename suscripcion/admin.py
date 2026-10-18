"""Los intentos de cobro en `/admin/`, de sólo lectura."""

from django.contrib import admin

from suscripcion.models import IntentoDeCobro


@admin.register(IntentoDeCobro)
class IntentoDeCobroAdmin(admin.ModelAdmin):
    list_display = (
        "gimnasio",
        "plan",
        "periodo_desde",
        "periodo_hasta",
        "monto_tienda_usd",
        "monto_ars",
        "estado",
        "estado_mp",
        "creado",
    )
    list_filter = ("estado", "plan")
    list_select_related = ("gimnasio",)

    # Un cobro no se edita a mano: se resuelve en el panel de Mercado Pago y la
    # app lo refleja sola.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
