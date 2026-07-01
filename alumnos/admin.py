from django.contrib import admin

from alumnos.models import Alumno


@admin.register(Alumno)
class AlumnoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "apellido", "estado", "gimnasio", "creado")
    list_filter = ("estado", "gimnasio")
    search_fields = ("nombre", "apellido", "email")
