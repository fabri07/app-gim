from django.contrib import admin

from ejercicios.models import CategoriaEjercicio, Ejercicio


@admin.register(CategoriaEjercicio)
class CategoriaEjercicioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "orden", "activo", "gimnasio")
    list_filter = ("activo", "gimnasio")
    search_fields = ("nombre",)


@admin.register(Ejercicio)
class EjercicioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "activo", "gimnasio")
    list_filter = ("categoria", "activo", "gimnasio")
    search_fields = ("nombre",)
