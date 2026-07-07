from django.apps import AppConfig


class CalendarioConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'calendario'

    def ready(self):
        from calendario import signals  # noqa: F401
