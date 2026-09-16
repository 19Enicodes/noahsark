"""
Sunday morning check-in reminder, sent to every active worker before service.

Refuses to run on a non-Sunday unless --force, so a stray scheduled run or a
mistimed click can't blast everyone mid-week.
"""

from django.utils import timezone

from management import notifications
from management.models import Worker

from ._messaging_base import MessagingCommand


class Command(MessagingCommand):
    help = (
        "Sends the 'service starts at 8am' check-in reminder to all active workers. "
        "Only runs on Sundays unless --force is given."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--force',
            action='store_true',
            help='Send even if today is not a Sunday.',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']

        if today.weekday() != 6 and not options['force']:
            self.stdout.write(
                self.style.WARNING(
                    f'Today is {today:%A}, not Sunday - skipping to avoid a mid-week send. '
                    f'Use --force to override.'
                )
            )
            return

        workers = list(Worker.objects.filter(status='ACTIVE').order_by('full_name'))
        if options['limit']:
            workers = workers[:options['limit']]

        self.stdout.write(
            f"Noah's Ark Sunday check-in reminder for {today:%A, %d %B %Y} "
            f'({len(workers)} active worker(s))'
        )

        if dry_run:
            summary = self.report_preview(workers, notifications.CHECKIN_REMINDER)
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. Nothing sent. Would send {summary['sms_recipients']} SMS "
                    f"(~{summary['sms_pages']} billable pages) and {summary['email_recipients']} email(s)."
                )
            )
            return

        sent, failed, skipped = self.send_to_all(workers, notifications.CHECKIN_REMINDER)

        summary = (
            f'{len(workers)} active worker(s): {sent} sent, {failed} failed, {skipped} already sent.'
        )
        style = self.style.ERROR if failed else self.style.SUCCESS
        self.stdout.write(style(summary))
