from django.contrib import admin

from reconocimientos.models import Premio, PremioOtorgado


@admin.register(Premio)
class PremioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "valor", "cuotas", "por_defecto", "activo", "gimnasio")
    list_filter = ("tipo", "activo", "gimnasio")
    search_fields = ("nombre",)


@admin.register(PremioOtorgado)
class PremioOtorgadoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "alumno", "periodo_inicio", "estado", "gimnasio")
    list_filter = ("estado", "gimnasio")
    raw_id_fields = ("alumno", "premio", "otorgado_por")
