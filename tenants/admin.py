from django.contrib import admin

from tenants.models import Gimnasio, Perfil


@admin.register(Gimnasio)
class GimnasioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "slug", "activo", "creado")
    search_fields = ("nombre", "slug")
    prepopulated_fields = {"slug": ("nombre",)}


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ("usuario", "gimnasio", "rol", "creado")
    list_filter = ("rol", "gimnasio")
    search_fields = ("usuario__username", "gimnasio__nombre")
