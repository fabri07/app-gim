from django.contrib import admin

from importaciones.models import Importacion


@admin.register(Importacion)
class ImportacionAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'gimnasio', 'estado', 'creado', 'creado_por')
    list_filter = ('gimnasio', 'tipo', 'estado')
    readonly_fields = ('resultado',)
