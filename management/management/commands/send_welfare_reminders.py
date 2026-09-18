"""
Welfare follow-ups for workers who missed consecutive Sundays.

Sends welfare check messages to workers who have missed 3 or more Sundays.
Safe to run repeatedly: notify() suppresses anything already sent in the 7-day window.
"""

from django.utils import timezone

from management import notifications
from management.attendance import calculate_consecutive_misses
from management.models import Worker

from ._messaging_base import MessagingCommand

RED_FLAG_THRESHOLD = 3


class Command(MessagingCommand):
    help = (
        "Sends welfare follow-ups to anyone who has missed 3 or more Sundays in a row."
    )

    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']
        limit = options['limit']

        self.stdout.write(f"Noah's Ark welfare check for {today:%A, %d %B %Y}")

        # Only the red-flagged: 3+ consecutive Sundays missed.
        welfare_workers = [
            worker
            for worker in Worker.objects.filter(status='ACTIVE').order_by('full_name')
            if calculate_consecutive_misses(worker) >= RED_FLAG_THRESHOLD
        ]

        if limit:
            welfare_workers = welfare_workers[:limit]

        if dry_run:
            summary = self.report_preview(welfare_workers, notifications.WELFARE_RED)
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. Nothing sent. "
                    f"{len(welfare_workers)} worker(s) flagged for welfare, ~{summary['sms_pages']} billable SMS pages."
                )
            )
            return

        self.stdout.write(
            f"\nSending welfare follow-ups ({RED_FLAG_THRESHOLD}+ Sundays missed) to {len(welfare_workers)} worker(s):"
        )
        sent, failed, skipped = self.send_to_all(welfare_workers, notifications.WELFARE_RED)

        summary = (
            f"{len(welfare_workers)} welfare follow-up(s): {sent} sent, {failed} failed, {skipped} already sent."
        )
        style = self.style.ERROR if failed else self.style.SUCCESS
        self.stdout.write(style(summary))
