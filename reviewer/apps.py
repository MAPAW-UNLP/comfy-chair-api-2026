from django.apps import AppConfig


class ReviewerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reviewer'

    def ready(self):
        import reviewer.signals  # noqa: F401 (registra los signals)
