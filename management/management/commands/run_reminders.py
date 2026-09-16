"""
Birthday wishes and missed-check-in follow-ups.

Safe to run repeatedly: notify() suppresses anything already sent inside its
dedupe window (same day for birthdays, 7 days for welfare follow-ups).
"""

from django.utils import timezone

from management import notifications
from management.attendance import calculate_consecutive_misses
from management.models import Worker

from ._messaging_base import MessagingCommand

# Consecutive Sundays missed before we reach out. Matches the dashboard's
# red-flag threshold.
RED_FLAG_THRESHOLD = 3


class Command(MessagingCommand):
    help = (
        "Sends birthday wishes to workers whose birthday is today, and welfare "
        "follow-ups to anyone who has missed 3 or more Sundays in a row."
    )

    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']
        limit = options['limit']

        self.stdout.write(f"Noah's Ark birthday & welfare check for {today}")

        birthday_workers = list(
            Worker.objects.filter(
                status='ACTIVE',
                birthday__month=today.month,
                birthday__day=today.day,
            ).order_by('full_name')
        )

        # Only the red-flagged: 3+ consecutive Sundays missed.
        welfare_workers = [
            worker
            for worker in Worker.objects.filter(status='ACTIVE').order_by('full_name')
            if calculate_consecutive_misses(worker) >= RED_FLAG_THRESHOLD
        ]

        if limit:
            birthday_workers = birthday_workers[:limit]
            welfare_workers = welfare_workers[:limit]

        if dry_run:
            birthday_summary = self.report_preview(birthday_workers, notifications.BIRTHDAY)
            welfare_summary = self.report_preview(welfare_workers, notifications.WELFARE_RED)
            total_pages = birthday_summary['sms_pages'] + welfare_summary['sms_pages']
            self.stdout.write(
                self.style.WARNING(
                    f'Dry run complete. Nothing sent. '
                    f'{len(birthday_workers)} birthday(s), {len(welfare_workers)} welfare follow-up(s), '
                    f'~{total_pages} billable SMS pages.'
                )
            )
            return

        self.stdout.write(f'\nBirthdays today ({len(birthday_workers)}):')
        b_sent, b_failed, b_skipped = self.send_to_all(birthday_workers, notifications.BIRTHDAY)

        self.stdout.write(
            f'\nWelfare follow-ups, {RED_FLAG_THRESHOLD}+ Sundays missed ({len(welfare_workers)}):'
        )
        w_sent, w_failed, w_skipped = self.send_to_all(welfare_workers, notifications.WELFARE_RED)

        sent, failed, skipped = b_sent + w_sent, b_failed + w_failed, b_skipped + w_skipped
        summary = (
            f'{len(birthday_workers)} birthday(s), {len(welfare_workers)} welfare follow-up(s): '
            f'{sent} sent, {failed} failed, {skipped} already sent.'
        )
        style = self.style.ERROR if failed else self.style.SUCCESS
        self.stdout.write(style(summary))
