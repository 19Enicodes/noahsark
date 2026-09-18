"""
Background automated scheduler for Noah's Ark church messaging.

Runs as a lightweight daemon thread inside the Django process.
Automatically triggers:
- 07:00 AM WAT: Daily Birthday Wishes
- 08:00 AM WAT: Daily Welfare Follow-ups (3+ missed Sundays)
- 06:30 AM WAT: Sunday Check-in Reminder (Sundays only)

Safe and idempotent:
1. In-memory calendar-day latch prevents re-running within the same day.
2. The messaging layer (AlertLog dedupe window) prevents double-sending.
"""

import logging
import threading
import time
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger(__name__)

# State tracking: record last run date for each task
_last_runs = {
    'birthdays': None,
    'welfare': None,
    'sunday': None,
}
_scheduler_thread = None
_lock = threading.Lock()


def run_birthday_job(force=False):
    """Executes the birthday wishes management command."""
    today = timezone.localdate()
    with _lock:
        if not force and _last_runs['birthdays'] == today:
            return "Birthdays already processed today."
        _last_runs['birthdays'] = today

    output = StringIO()
    try:
        call_command('send_birthday_reminders', stdout=output, stderr=output, no_color=True)
        summary = output.getvalue().strip()
        logger.info("[Scheduler] Birthday job finished: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("[Scheduler] Birthday job failed: %s", exc)
        return f"Error: {exc}"


def run_welfare_job(force=False):
    """Executes the welfare check management command."""
    today = timezone.localdate()
    with _lock:
        if not force and _last_runs['welfare'] == today:
            return "Welfare check already processed today."
        _last_runs['welfare'] = today

    output = StringIO()
    try:
        call_command('send_welfare_reminders', stdout=output, stderr=output, no_color=True)
        summary = output.getvalue().strip()
        logger.info("[Scheduler] Welfare job finished: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("[Scheduler] Welfare job failed: %s", exc)
        return f"Error: {exc}"


def run_sunday_job(force=False):
    """Executes the Sunday check-in reminder management command."""
    today = timezone.localdate()
    with _lock:
        if not force and _last_runs['sunday'] == today:
            return "Sunday reminder already processed today."
        _last_runs['sunday'] = today

    output = StringIO()
    try:
        call_command('send_checkin_reminders', force=force, stdout=output, stderr=output, no_color=True)
        summary = output.getvalue().strip()
        logger.info("[Scheduler] Sunday reminder job finished: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("[Scheduler] Sunday reminder job failed: %s", exc)
        return f"Error: {exc}"


def _scheduler_loop():
    """Main loop checking schedule every 30 seconds."""
    logger.info("[Scheduler] Automated messaging background daemon started.")
    while True:
        try:
            now = timezone.localtime()
            today = now.date()

            # 1. Birthday Check: Run once today if now >= 07:00 AM
            if now.hour >= 7 and _last_runs['birthdays'] != today:
                logger.info("[Scheduler] Triggering scheduled 07:00 AM Birthday check...")
                run_birthday_job()

            # 2. Welfare Check: Run once today if now >= 08:00 AM
            if now.hour >= 8 and _last_runs['welfare'] != today:
                logger.info("[Scheduler] Triggering scheduled 08:00 AM Welfare check...")
                run_welfare_job()

            # 3. Sunday Reminder: Run once on Sunday if now >= 06:30 AM
            if now.weekday() == 6:  # 6 = Sunday
                is_after_630 = (now.hour > 6) or (now.hour == 6 and now.minute >= 30)
                if is_after_630 and _last_runs['sunday'] != today:
                    logger.info("[Scheduler] Triggering scheduled 06:30 AM Sunday reminder...")
                    run_sunday_job()

        except Exception as exc:
            logger.exception("[Scheduler] Unexpected loop error: %s", exc)

        time.sleep(30)


def start_scheduler():
    """Starts the scheduler thread if enabled and not already running."""
    global _scheduler_thread
    if not getattr(settings, 'AUTOMATED_SCHEDULER_ENABLED', True):
        logger.info("[Scheduler] Automated scheduler disabled in settings.")
        return

    with _lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            return
        _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="NoahsArkScheduler")
        _scheduler_thread.start()
        logger.info("[Scheduler] Daemon thread successfully launched.")
