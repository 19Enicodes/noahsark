# Noah's Ark — Messaging

How the four automated messages work, and what you need to configure before
they can send.

## The four messages

| Message | When it fires | Channels |
|---|---|---|
| **Check-in confirmation** | Immediately when an usher checks someone in | Email, or SMS if we have no email for them |
| **Check-in reminder** | Sunday morning before the 8am service | SMS + email |
| **Birthday wish** | Every morning, to anyone whose birthday is today | SMS + email |
| **Missed check-in follow-up** | After 3 consecutive Sundays missed | SMS + email |

SMS goes through **Termii**; email goes through **Gmail SMTP** from
`rccgnaycmessages@gmail.com`.

The message wording lives in `management/notifications.py` under `MESSAGES` —
edit it there. Which channels each message uses is the `CHANNEL_POLICY` table
directly below it; change a value and nothing else needs touching.

## One-time setup

### 1. Fill in `.env`

Copy `.env.example` to `.env` if it isn't there already, then set:

```
TERMII_API_KEY=<from your Termii dashboard>
TERMII_SENDER_ID=<your approved Termii sender ID>
EMAIL_HOST_USER=rccgnaycmessages@gmail.com
EMAIL_HOST_PASSWORD=<16-character Google App Password>
MESSAGING_ENABLED=true
```

`.env` is gitignored and must stay that way — it holds live credentials.

### 2. Get the Gmail App Password

A Google account password will **not** work for SMTP. You need an App Password:

1. Turn on 2-Step Verification for `rccgnaycmessages@gmail.com`.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Create one named "Noah's Ark".
4. Paste the 16 characters into `EMAIL_HOST_PASSWORD` (spaces are fine).

Gmail allows roughly 500 recipients per day on a free account — comfortably
above what this system sends.

### 3. Apply the database migration

```
python manage.py migrate
```

### 4. Verify credentials before sending to anyone real

```
python manage.py send_test_message --type BIRTHDAY ^
    --to-email you@example.com --to-phone 08031234567
```

This sends one message to you, prints exactly what the SMS will look like and
how many pages it costs, and writes nothing to the database. Get this working
before running anything else.

### 5. Set up the scheduled jobs

See `scripts/README.md`.

## Day-to-day

Both bulk sends have dashboard buttons at `/admin/dashboard/`, and both prompt
for confirmation because they spend real money.

From the command line:

```
python manage.py send_checkin_reminders --dry-run   # preview + cost, sends nothing
python manage.py send_checkin_reminders             # only sends on a Sunday
python manage.py send_checkin_reminders --force     # send today whatever day it is
python manage.py run_reminders --dry-run            # birthdays + welfare preview
python manage.py run_reminders
```

`--dry-run` prints recipient counts and **billable SMS pages** before you spend
anything. Use it before any large send. `--limit N` restricts a run to the first
N recipients, which is the safe way to do one live test.

**Every command is safe to run twice.** A message that already went out
successfully is skipped, not resent. Failures *are* retried on the next run,
which is the behaviour you want.

## Why SMS text differs slightly from email

SMS is billed per 160-character page — but only if every character is in the
GSM-7 alphabet. Emoji and typographic dashes (`—` `–`) force the whole message
into UCS-2 encoding, which cuts each page to 67 characters and roughly doubles
the cost.

So `to_gsm7()` strips emoji and converts `—` `–` `'` `"` to plain equivalents
**for SMS only**. The wording is identical; email keeps the emoji. The birthday
message drops from ~8 pages to ~4 this way.

## Troubleshooting

**Check `/admin-internal/` → Alert logs first.** Every attempt is recorded with
its channel, status, and a `detail` field holding either the Termii message ID
or the exact failure reason.

| Symptom | Cause |
|---|---|
| `Gmail App Password is not configured` | `EMAIL_HOST_PASSWORD` is empty in `.env` |
| `SMTPAuthenticationError` | Using the account password, not an App Password — or 2-Step Verification is off |
| `Termii API key is not configured` | `TERMII_API_KEY` is empty in `.env` |
| `Messaging is disabled` | `MESSAGING_ENABLED=false` in `.env` |
| Nothing sent, no log rows | Already sent inside the dedupe window — check the timestamps |
| Sunday reminder didn't fire | Machine was off or asleep; check `logs\sunday_reminder.log` and use the dashboard button |

**To stop all outbound messaging immediately**, set `MESSAGING_ENABLED=false` in
`.env`. Jobs still run and still log; nothing leaves the building.

## Running the tests

```
python manage.py test management
```

The suite patches both senders, so it never touches the network or sends
anything. It covers the GSM-7 sanitising, page-count maths, channel routing,
dedupe behaviour, and the attendance calculations.
