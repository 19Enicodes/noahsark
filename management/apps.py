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

        # Only start scheduler for web server processes (runserver child, gunicorn, Railway, etc.)
        is_web_server = (
            ('runserver' in sys.argv and os.environ.get('RUN_MAIN') == 'true')
            or 'gunicorn' in ' '.join(sys.argv).lower()
            or (os.environ.get('PORT') is not None and 'manage.py' not in sys.argv[0])
        )
        if not is_web_server:
            return

        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception:
            pass
