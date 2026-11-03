from django.apps import AppConfig

class UserConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user'

    # Grupo 3 (2026)
    def ready(self):
        import user.signals
