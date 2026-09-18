import sys
import os
from django.apps import AppConfig


class ManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'management'

    def ready(self):
        # Do not start scheduler during non-server CLI commands like test, migrate, etc.
        skip_commands = {'test', 'makemigrations', 'migrate', 'collectstatic', 'shell', 'seed_departments'}
        if set(sys.argv) & skip_commands:
            return

        # Under runserver, wait for the child reload process so it does not start twice
        if 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return

        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception:
            pass
