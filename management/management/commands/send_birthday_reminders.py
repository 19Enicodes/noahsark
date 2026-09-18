"""
Birthday wishes command.

Sends birthday messages to active workers whose birthday is today.
Safe to run repeatedly: notify() suppresses anything already sent today.
"""

from django.utils import timezone

from management import notifications
from management.models import Worker

from ._messaging_base import MessagingCommand


class Command(MessagingCommand):
    help = "Sends birthday wishes to all active workers whose birthday is today."

    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']
        limit = options['limit']

        self.stdout.write(f"Noah's Ark birthday check for {today:%A, %d %B %Y}")

        birthday_workers = list(
            Worker.objects.filter(
                status='ACTIVE',
                birthday__month=today.month,
                birthday__day=today.day,
            ).order_by('full_name')
        )

        if limit:
            birthday_workers = birthday_workers[:limit]

        if dry_run:
            summary = self.report_preview(birthday_workers, notifications.BIRTHDAY)
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. Nothing sent. "
                    f"{len(birthday_workers)} birthday celebrant(s), ~{summary['sms_pages']} billable SMS pages."
                )
            )
            return

        self.stdout.write(f"\nSending to {len(birthday_workers)} birthday celebrant(s):")
        sent, failed, skipped = self.send_to_all(birthday_workers, notifications.BIRTHDAY)

        summary = (
            f"{len(birthday_workers)} birthday celebrant(s): {sent} sent, {failed} failed, {skipped} already sent."
        )
        style = self.style.ERROR if failed else self.style.SUCCESS
        self.stdout.write(style(summary))
