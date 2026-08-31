from django.contrib import admin

from rutinas.models import (
    RutinaAsignada,
    RutinaAsignadaItem,
    RutinaPlantilla,
    RutinaPlantillaItem,
)


class RutinaPlantillaItemInline(admin.TabularInline):
    model = RutinaPlantillaItem
    extra = 1


class RutinaAsignadaItemInline(admin.TabularInline):
    model = RutinaAsignadaItem
    extra = 0
    # extra=0: los items de una rutina ASIGNADA se generan por el snapshot
    # (`crear_desde_plantilla`), no se agregan a mano desde el admin.


@admin.register(RutinaPlantilla)
class RutinaPlantillaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "gimnasio", "objetivo", "nivel", "dias_por_semana", "activa")
    list_filter = ("gimnasio", "nivel", "activa")
    search_fields = ("nombre", "objetivo")
    inlines = [RutinaPlantillaItemInline]


@admin.register(RutinaAsignada)
class RutinaAsignadaAdmin(admin.ModelAdmin):
    list_display = ("alumno", "nombre_snapshot", "gimnasio", "fecha_inicio", "fecha_fin", "activa")
    list_filter = ("gimnasio", "activa")
    search_fields = ("nombre_snapshot", "alumno__nombre", "alumno__apellido")
    inlines = [RutinaAsignadaItemInline]


@admin.register(RutinaPlantillaItem)
class RutinaPlantillaItemAdmin(admin.ModelAdmin):
    # También registrado standalone (además del inline en RutinaPlantilla)
    # para poder buscar/filtrar items across plantillas, p.ej. al reasignar
    # un ejercicio antes de borrarlo (PROTECT).
    list_display = (
        "rutina", "ejercicio", "semana", "dia", "bloque", "orden",
        "series", "repeticiones",
    )
    list_filter = ("rutina__gimnasio",)
    search_fields = ("ejercicio__nombre", "rutina__nombre")


@admin.register(RutinaAsignadaItem)
class RutinaAsignadaItemAdmin(admin.ModelAdmin):
    list_display = (
        "rutina_asignada", "ejercicio_nombre_snapshot", "semana", "dia",
        "bloque", "orden", "series", "repeticiones",
    )
    search_fields = ("ejercicio_nombre_snapshot", "rutina_asignada__nombre_snapshot")
