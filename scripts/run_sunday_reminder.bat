@echo off
REM Sunday check-in reminder for Noah's Ark.
REM Wired up by scripts\NoahsArk_SundayReminder.xml (Task Scheduler), and
REM safe to double-click if a scheduled run was missed.
REM
REM The command itself refuses to send on a non-Sunday, so running this by
REM accident mid-week does nothing.

cd /d "%~dp0.."
call venv\Scripts\activate.bat
python manage.py send_checkin_reminders >> logs\sunday_reminder.log 2>&1
