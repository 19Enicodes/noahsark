"""
Shared plumbing for the messaging commands.

Named with a leading underscore so Django's command loader ignores it.
"""

from django.core.management.base import BaseCommand

from management import notifications


class MessagingCommand(BaseCommand):
    """Adds --dry-run / --limit and a common way to report results."""

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show who would be messaged and the estimated SMS cost, without sending.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Only process the first N recipients. Useful for a single live test send.',
        )

    def report_preview(self, workers, kind, extra_context=None):
        """Print a dry-run summary. Returns the summary dict."""
        summary = notifications.preview(workers, kind, extra_context)

        self.stdout.write(self.style.MIGRATE_HEADING(f'\nDRY RUN - {kind} (nothing sent)'))
        self.stdout.write(f"  Recipients considered : {summary['total_workers']}")
        self.stdout.write(f"  SMS to send           : {summary['sms_recipients']}")
        self.stdout.write(f"  Emails to send        : {summary['email_recipients']}")
        self.stdout.write(f"  Already sent (skipped): {summary['skipped']}")
        self.stdout.write(
            self.style.WARNING(f"  Billable SMS pages    : {summary['sms_pages']}")
        )

        if summary['unreachable']:
            names = ', '.join(summary['unreachable'][:10])
            more = '' if len(summary['unreachable']) <= 10 else f" (+{len(summary['unreachable']) - 10} more)"
            self.stdout.write(self.style.ERROR(f'  No contact route      : {names}{more}'))

        if summary['sms_text']:
            pages = notifications.sms_page_count(summary['sms_text'])
            self.stdout.write(
                f"\n  SMS text as it will send ({len(summary['sms_text'])} chars, {pages} pages):"
            )
            self.stdout.write(f"  {summary['sms_text']}\n")

        return summary

    def send_to_all(self, workers, kind, extra_context_for=None):
        """
        Send `kind` to every worker in `workers`.

        `extra_context_for` is an optional callable taking a worker and
        returning its template context. Returns (sent, failed, skipped).
        """
        sent = failed = skipped = 0

        for worker in workers:
            extra = extra_context_for(worker) if extra_context_for else None
            results = notifications.notify(worker, kind, extra)

            if not results:
                self.stdout.write(
                    self.style.ERROR(f'  ! {worker.full_name}: no usable contact details')
                )
                continue

            for channel, status, detail in results:
                if status == 'SENT':
                    sent += 1
                    self.stdout.write(self.style.SUCCESS(f'  + {worker.full_name} [{channel}]'))
                elif status == 'SKIPPED':
                    skipped += 1
                    self.stdout.write(f'  = {worker.full_name} [{channel}] already sent')
                else:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(f'  x {worker.full_name} [{channel}]: {detail}')
                    )

        return sent, failed, skipped
