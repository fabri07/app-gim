from django.contrib import admin

from pagos.models import PagoMensual


@admin.register(PagoMensual)
class PagoMensualAdmin(admin.ModelAdmin):
    list_display = ("alumno", "mes", "anio", "monto", "estado", "gimnasio")
    list_filter = ("estado", "gimnasio", "anio")
    search_fields = ("alumno__nombre", "alumno__apellido")
