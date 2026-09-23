"""Los pagos de la plataforma en `/admin/`.

El camino normal es el panel (`plataforma:pago_nuevo`), que precarga el
período y el precio. Esto es la salida de emergencia: corregir una fila mal
cargada o borrar un pago duplicado, que el panel no ofrece a propósito.
"""

from django.contrib import admin

from plataforma.models import PagoPlataforma


@admin.register(PagoPlataforma)
class PagoPlataformaAdmin(admin.ModelAdmin):
    list_display = (
        "gimnasio",
        "periodo_desde",
        "periodo_hasta",
        "fecha_pago",
        "monto_usd",
        "monto_ars",
        "alumnos_activos",
    )
    list_filter = ("gimnasio",)
    date_hierarchy = "fecha_pago"
    search_fields = ("gimnasio__nombre", "notas")
    # Sin el `select_related`, el listado hace una query por fila para
    # resolver el nombre del gimnasio y el usuario que lo registró.
    list_select_related = ("gimnasio", "registrado_por")
