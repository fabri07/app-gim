from django.contrib import admin

from novedades.models import Novedad


@admin.register(Novedad)
class NovedadAdmin(admin.ModelAdmin):
    list_display = ("titulo", "gimnasio", "fecha_publicacion", "visible_hasta", "activa")
    list_filter = ("activa", "gimnasio")
    search_fields = ("titulo",)
