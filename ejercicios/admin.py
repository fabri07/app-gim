from django.contrib import admin

from ejercicios.models import Ejercicio


@admin.register(Ejercicio)
class EjercicioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "grupo_muscular", "activo", "gimnasio")
    list_filter = ("grupo_muscular", "activo", "gimnasio")
    search_fields = ("nombre",)
