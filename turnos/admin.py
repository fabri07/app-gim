from django.contrib import admin

from turnos.models import ConfiguracionTurnos, CupoExcepcion, HorarioAtencion, Reserva


@admin.register(ConfiguracionTurnos)
class ConfiguracionTurnosAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "duracion_minutos", "vacantes_default")


@admin.register(HorarioAtencion)
class HorarioAtencionAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "dia_semana", "hora_desde", "hora_hasta")
    list_filter = ("gimnasio", "dia_semana")


@admin.register(CupoExcepcion)
class CupoExcepcionAdmin(admin.ModelAdmin):
    list_display = ("gimnasio", "dia_semana", "hora_inicio", "vacantes")
    list_filter = ("gimnasio", "dia_semana")


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("alumno", "gimnasio", "fecha", "hora_inicio")
    list_filter = ("gimnasio", "fecha")
    search_fields = ("alumno__nombre", "alumno__apellido")
