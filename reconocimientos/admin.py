from django.contrib import admin

from reconocimientos.models import Premio


@admin.register(Premio)
class PremioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "valor", "cuotas", "por_defecto", "activo", "gimnasio")
    list_filter = ("tipo", "activo", "gimnasio")
    search_fields = ("nombre",)
