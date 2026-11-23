from django.contrib import admin

from pagos.models import CobroOnlineCuota, Cuota, MedioCobro


@admin.register(Cuota)
class CuotaAdmin(admin.ModelAdmin):
    list_display = (
        "alumno", "periodo_inicio", "periodo_fin", "monto", "descuento", "estado", "gimnasio",
    )
    list_filter = ("estado", "gimnasio")
    date_hierarchy = "periodo_inicio"
    search_fields = ("alumno__nombre", "alumno__apellido")

    def get_readonly_fields(self, request, obj=None):
        """El descuento sólo se toca en una cuota impaga: en una pagada o
        anulada es parte del registro de lo que se cobró."""
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.estado not in Cuota.ESTADOS_IMPAGOS:
            base += ["descuento", "descuento_motivo"]
        return base


@admin.register(MedioCobro)
class MedioCobroAdmin(admin.ModelAdmin):
    list_display = ("alias", "titular", "entidad", "activo")


@admin.register(CobroOnlineCuota)
class CobroOnlineCuotaAdmin(admin.ModelAdmin):
    """Sólo lectura: un registro de plata que escriben el webhook y la
    reconciliación, nunca una persona desde `/admin/`."""

    list_display = ("cuota", "monto", "estado", "estado_mp", "payment_id", "gimnasio", "creado")
    list_filter = ("estado", "revision_pendiente", "gimnasio")
    search_fields = ("payment_id", "preference_id", "cuota__alumno__apellido")
    list_select_related = ("cuota__alumno", "gimnasio")

    def get_readonly_fields(self, request, obj=None):
        return [campo.name for campo in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Sin permiso de cambio, el admin muestra el detalle en sólo lectura
        # (alcanza con el de ver, que el superadmin tiene).
        return False

    def has_delete_permission(self, request, obj=None):
        return False
