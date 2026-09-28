from django.contrib import admin

from ingreso import limites
from ingreso.models import ConfiguracionIngreso, Dispositivo

admin.site.register(ConfiguracionIngreso)


@admin.register(Dispositivo)
class DispositivoAdmin(admin.ModelAdmin):
    """La salida del dueño del producto para revocar un lector cuando la
    cuenta del gimnasio está SUSPENDIDA.

    Con la cuenta congelada, `PlataformaMiddleware` le corta el panel al staff
    (incluidas las pantallas de `ingreso`), así que el dueño del gimnasio no
    puede rotar ni desactivar un token robado. `/admin/` está en
    `APPS_SIN_BLOQUEO`: el superadmin destilda `activo` y la API responde 403
    desde el request siguiente (`auth.dispositivo_autenticado` mira `activo`
    en la base, no en el set cacheado). Procedimiento anotado en `ISSUES.md`
    [2026-09-28].

    **El hash del token no se muestra**: no es la llave, pero tampoco le sirve
    a nadie mirarlo, y en una captura de pantalla del admin no tiene por qué
    aparecer. Tampoco es editable (`editable=False` en el modelo).
    """

    list_display = ["nombre", "gimnasio", "tipo", "activo", "ultimo_contacto"]
    list_filter = ["activo", "tipo"]
    search_fields = ["nombre", "gimnasio__nombre", "gimnasio__slug"]
    list_select_related = ["gimnasio"]
    readonly_fields = ["ultimo_contacto", "eventos_hoy", "eventos_hoy_fecha"]

    def has_add_permission(self, request):
        """Un lector NO se da de alta desde acá. El alta es
        `services.crear_dispositivo`, que genera la clave y guarda su hash;
        desde el admin el lector nacía con `token_hash` vacío (es
        `editable=False`), que no abre nada, y el segundo daba 500 contra la
        unicidad del hash. El alta vive en «Lectores de la puerta» del panel
        del gimnasio."""
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Desactivar/reactivar cambia quién está en el set del freno por IP:
        # el mismo `olvidar_lectores_activos` que usan las pantallas de staff.
        limites.olvidar_lectores_activos()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        limites.olvidar_lectores_activos()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        limites.olvidar_lectores_activos()
