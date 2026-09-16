"""
Credential smoke test. Sends one message to an address/number you choose
without touching worker records or writing to AlertLog.

Run this first, before any bulk send:

    python manage.py send_test_message --type BIRTHDAY \
        --to-email you@example.com --to-phone 08031234567
"""

from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from management import notifications


class Command(BaseCommand):
    help = 'Sends one test message to verify Termii and Gmail credentials work.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            default=notifications.BIRTHDAY,
            choices=sorted(notifications.MESSAGES.keys()),
            help='Which message to send.',
        )
        parser.add_argument('--to-email', help='Email address to test.')
        parser.add_argument('--to-phone', help='Phone number to test, e.g. 08031234567.')
        parser.add_argument('--name', default='Test Recipient', help='Name to render into the message.')

    def handle(self, *args, **options):
        kind = options['type']
        to_email = options['to_email']
        to_phone = options['to_phone']
        name = options['name']

        if not to_email and not to_phone:
            raise CommandError('Give at least one of --to-email or --to-phone.')

        template = notifications.MESSAGES[kind]
        context = {'name': name, 'date': date.today().strftime('%A, %d %B %Y')}
        subject = template['subject'].format(**context)
        body = template['body'].format(**context)
        sms_body = notifications.to_gsm7(body)

        self.stdout.write(self.style.MIGRATE_HEADING(f'\nTesting {kind}'))
        self.stdout.write(f'  Messaging enabled : {settings.MESSAGING_ENABLED}')
        self.stdout.write(f'  Termii key set    : {bool(settings.TERMII_API_KEY)}')
        self.stdout.write(f'  Gmail password set: {bool(settings.EMAIL_HOST_PASSWORD)}')

        if to_phone:
            pages = notifications.sms_page_count(sms_body)
            number = notifications.normalise_nigerian_number(to_phone)
            self.stdout.write(
                f'\n  SMS -> {number} ({len(sms_body)} chars, {pages} billable page(s))'
            )
            self.stdout.write(f'  {sms_body}')
            ok, detail = notifications.send_sms(to_phone, sms_body)
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(f"  {'SENT' if ok else 'FAILED'}: {detail}"))

        if to_email:
            safe_subject = notifications.to_gsm7(subject)
            self.stdout.write(f'\n  EMAIL -> {to_email}')
            self.stdout.write(f'  Subject: {safe_subject}')
            ok, detail = notifications.send_email(to_email, subject, body)
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(f"  {'SENT' if ok else 'FAILED'}: {detail}"))

        self.stdout.write('\nNo AlertLog rows were written - this was a test.')
