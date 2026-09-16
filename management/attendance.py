"""
Attendance date maths, shared by the views and the reminder commands.

These live here rather than in views.py so management commands can use them
without importing the whole view layer.
"""

from datetime import date, timedelta

from .models import CheckIn


def get_current_sunday_reference():
    """
    The Sunday a check-in recorded right now belongs to.

    Mon-Wed look back to the Sunday just past, Thu-Sat look forward to the
    coming one, so a late entry still lands on the right service.
    """
    today = date.today()
    weekday = today.weekday()  # Mon=0, ..., Sun=6
    if weekday == 6:
        return today
    elif weekday < 3:  # Mon, Tue, Wed -> refer to last Sunday
        return today - timedelta(days=weekday + 1)
    else:  # Thu, Fri, Sat -> refer to next Sunday
        return today + timedelta(days=6 - weekday)


def get_last_sunday():
    """The nearest Sunday already past, including today if today is Sunday."""
    today = date.today()
    if today.weekday() == 6:
        return today
    return today - timedelta(days=today.weekday() + 1)


def calculate_consecutive_misses(worker):
    """
    How many Sundays in a row this worker has missed, walking backwards from
    the last Sunday until a check-in is found or we reach their join date.
    """
    if worker.status != 'ACTIVE':
        return 0

    misses = 0
    current_sunday = get_last_sunday()
    join_date = worker.date_joined.date()

    while current_sunday >= join_date:
        checked_in = CheckIn.objects.filter(
            worker=worker, sunday_reference=current_sunday
        ).exists()
        if checked_in:
            break
        misses += 1
        current_sunday -= timedelta(days=7)

    return misses
