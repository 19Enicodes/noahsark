@echo off
REM Daily birthday wishes and welfare follow-ups for Noah's Ark.
REM Run this every morning - it only messages people whose birthday is today
REM or who have missed 3+ consecutive Sundays, and never sends twice.

cd /d "%~dp0.."
call venv\Scripts\activate.bat
python manage.py run_reminders >> logs\daily_reminders.log 2>&1
