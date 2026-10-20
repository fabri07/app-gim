from django.apps import AppConfig


class TiendaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tienda"
    verbose_name = "Tienda"

    def ready(self):
        from django.db.models.signals import pre_delete

        from tienda.models import FotoProducto
        from tienda.retencion import retener

        pre_delete.connect(retener, sender=FotoProducto, dispatch_uid="tienda_retener_foto")
