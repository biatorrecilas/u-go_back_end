from django.apps import AppConfig


class WebsiteConfig(AppConfig):
    name = 'website'

    def ready(self):
        # Importing registers deployment checks without doing runtime work.
        from . import checks  # noqa: F401
