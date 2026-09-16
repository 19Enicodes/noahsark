# Scheduling the automated messages

Two scheduled jobs. Both are safe to run more than once — a message that has
already gone out is skipped, not resent.

| Script | When | What it sends |
|---|---|---|
| `run_sunday_reminder.bat` | Sundays, 06:30 | The "service starts at 8am" reminder to every active worker |
| `run_daily_reminders.bat` | Every day, ~07:00 | Birthday wishes, plus follow-ups to anyone who has missed 3+ Sundays |

The check-in confirmation isn't scheduled — it fires the moment an usher
checks someone in.

## Set up the Sunday reminder

The included task is preconfigured for `C:\Users\taiwo\noahs_ark`. Import it:

```
schtasks /create /tn "NoahsArk_SundayReminder" /xml "C:\Users\taiwo\noahs_ark\scripts\NoahsArk_SundayReminder.xml"
```

Or through the GUI: **Task Scheduler → Action → Import Task…** and pick
`scripts\NoahsArk_SundayReminder.xml`.

If the project ever moves, edit `<Command>` and `<WorkingDirectory>` in the XML
before importing.

## Set up the daily birthday/welfare check

Same idea, pointing at the other script:

```
schtasks /create /tn "NoahsArk_DailyReminders" /sc daily /st 07:00 /tr "C:\Users\taiwo\noahs_ark\scripts\run_daily_reminders.bat"
```

## Verify it works

Run a task on demand without waiting for Sunday:

```
schtasks /run /tn "NoahsArk_SundayReminder"
```

Then check `logs\sunday_reminder.log`. Every run appends its full output, so
that file is the first place to look if a Sunday goes quiet.

## Things to know

- **The machine has to be on and awake.** `WakeToRun` is enabled, but a
  powered-off computer sends nothing. `StartWhenAvailable` means a missed run
  fires late rather than not at all — and the dashboard button is always there
  as a manual fallback.
- **The reminder self-guards.** `send_checkin_reminders` refuses to send on a
  non-Sunday unless `--force`, so a mistimed trigger can't blast everyone
  mid-week. The dashboard button passes `--force` deliberately.
- **No web server needed.** These run the management commands directly against
  the database; `runserver` does not have to be up.
- **To pause all sending**, set `MESSAGING_ENABLED=false` in `.env`. The jobs
  still run and still log, but nothing goes out.
