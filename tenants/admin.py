from django.contrib import admin

from tenants.models import Gimnasio, Perfil, RegistroSuplantacion


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


@admin.register(RegistroSuplantacion)
class RegistroSuplantacionAdmin(admin.ModelAdmin):
    """Auditoría de "entrar como este alumno", de SOLO LECTURA.

    Una auditoría que no se puede leer no es una auditoría: sin esto había que
    entrar a la base para consultarla. Y una que se puede editar tampoco sirve,
    de ahí que se apaguen alta, cambio y borrado — el `PROTECT` de las FK ya
    impide el borrado en cascada, esto cierra el borrado manual.
    """

    list_display = ("creado", "gimnasio", "staff_usuario", "alumno", "finalizada_en", "ip")
    list_filter = ("gimnasio", "creado")
    search_fields = (
        "staff_usuario__username",
        "alumno__nombre",
        "alumno__apellido",
    )
    date_hierarchy = "creado"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
