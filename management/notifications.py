"""
Outbound messaging for Noah's Ark: SMS via Termii, email via Gmail SMTP.

Everything that sends a message goes through notify(). It resolves which
channels apply, renders the text, skips anything already sent, dispatches,
and records the attempt in AlertLog.

Two things worth knowing before editing:

1. SMS text is sanitised to the GSM-7 alphabet (see to_gsm7). Emoji and
   em/en dashes force the whole message into UCS-2, which cuts each billed
   SMS page from 153 characters to 67 - roughly doubling the cost. Email
   keeps the original text, emoji and all.

2. AlertLog rows are created as PENDING *before* sending and only marked
   SENT on success, so a crash mid-send can't leave a false SENT row that
   permanently blocks a retry.
"""

import logging
import re
import threading
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

import requests

from .models import AlertLog

logger = logging.getLogger(__name__)

# Alert types. These are stored in AlertLog.type.
BIRTHDAY = 'BIRTHDAY'
CHECKIN_REMINDER = 'CHECKIN_REMINDER'
WELFARE_RED = 'WELFARE_RED'
CHECKIN_CONFIRM = 'CHECKIN_CONFIRM'

SMS = 'SMS'
EMAIL = 'EMAIL'


# --------------------------------------------------------------------------
# Message text
# --------------------------------------------------------------------------
# Placeholders: {name} always available, {date} on CHECKIN_CONFIRM.
# Edit the wording here; it is used verbatim for email and sanitised for SMS.

MESSAGES = {
    BIRTHDAY: {
        'subject': 'Happy Birthday, {name}! 🎂',
        'body': (
            "Happy Birthday, {name}! 🎉 Today, the entire Noah's Ark family wants to "
            "pause and celebrate YOU. Thank you for being such a wonderful part of our "
            "community — your presence brings so much joy and life to this place. You are "
            "loved and appreciated more than words can say. May this new year of your life "
            "be filled with God's abundant grace, good health, peace, and every blessing He "
            "has in store for you. Enjoy your day to the fullest — you deserve it! "
            "With love, Noah's Ark family. 🎂❤️"
        ),
    },
    CHECKIN_REMINDER: {
        'subject': 'Service starts at 8am today — see you soon!',
        'body': (
            "Hi {name}, just a warm reminder that service begins today at 8am. We can't "
            "wait to see your face! When you arrive, please remember to check in using your "
            "unique code so we can keep things running smoothly. We hope today brings you "
            "exactly what your heart needs, see you soon! – Noah's Ark"
        ),
    },
    WELFARE_RED: {
        'subject': "We've missed you at Noah's Ark",
        'body': (
            "Hi {name}, we missed seeing you at church lately, and just wanted to reach out "
            "to make sure everything is okay with you. You matter to us, not just as a "
            "member, but as family, so please don't hesitate to reach out if you need "
            "anything at all. If it was simply a busy week, no worries at all, we just "
            "wanted you to know you were on our minds. Take care and God bless. – Noah's Ark"
        ),
    },
    CHECKIN_CONFIRM: {
        'subject': "You're checked in — {date}",
        'body': (
            "Hi {name}, thank you for joining us today, {date}! You've been successfully "
            "checked in for service. We're so glad you're here, your presence adds so much "
            "to our community, and we're grateful you chose to spend this time with us. "
            "May today be a blessing to you. Enjoy the service! – Noah's Ark"
        ),
    },
}


# --------------------------------------------------------------------------
# Channel policy
# --------------------------------------------------------------------------
# 'always'   -> send on this channel every time
# 'fallback' -> SMS only when the worker has no email address on file
# 'if_known' -> email only when the worker has an email address on file
#
# Flip a value here to change routing; no other code needs touching.

CHANNEL_POLICY = {
    BIRTHDAY:         {SMS: 'always',   EMAIL: 'if_known'},
    CHECKIN_REMINDER: {SMS: 'always',   EMAIL: 'if_known'},
    WELFARE_RED:      {SMS: 'always',   EMAIL: 'if_known'},
    # Highest-volume message: everyone who checks in, every Sunday. Email is
    # free, so SMS is only used for people we can't reach by email.
    CHECKIN_CONFIRM:  {SMS: 'fallback', EMAIL: 'if_known'},
}

# How long a successful send suppresses a repeat.
# 'day' = same calendar day, or a number of days.
DEDUPE_WINDOW = {
    BIRTHDAY: 'day',
    CHECKIN_REMINDER: 'day',
    CHECKIN_CONFIRM: 'day',
    WELFARE_RED: 7,
}


# --------------------------------------------------------------------------
# SMS text sanitising
# --------------------------------------------------------------------------

# Characters outside GSM-7 that we can map to a close equivalent rather than drop.
GSM7_SUBSTITUTIONS = {
    '—': '-',   # em dash
    '–': '-',   # en dash
    '‘': "'",   # left single quote
    '’': "'",   # right single quote / curly apostrophe
    '“': '"',   # left double quote
    '”': '"',   # right double quote
    '…': '...',  # ellipsis
    ' ': ' ',   # non-breaking space
    '•': '-',   # bullet
    '′': "'",   # prime
}

# The GSM-7 basic alphabet plus its extension table. Anything not in here
# forces the message into UCS-2 encoding.
GSM7_BASIC = (
    '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ'
    ' !"#¤%&\'()*+,-./0123456789:;<=>?'
    '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§'
    '¿abcdefghijklmnopqrstuvwxyzäöñüà'
)
# Extension characters cost two GSM-7 septets each.
GSM7_EXTENDED = '^{}\\[~]|€'
GSM7_ALL = frozenset(GSM7_BASIC + GSM7_EXTENDED)

# Single-segment and multipart segment sizes for each encoding.
GSM7_SINGLE, GSM7_MULTIPART = 160, 153
UCS2_SINGLE, UCS2_MULTIPART = 70, 67


def to_gsm7(text):
    """
    Make text safe for single-byte SMS encoding, preserving the wording.

    Substitutes typographic punctuation for ASCII equivalents and drops
    anything still outside GSM-7 (emoji, in practice). Collapses the
    whitespace that dropping a character can leave behind.
    """
    for char, replacement in GSM7_SUBSTITUTIONS.items():
        text = text.replace(char, replacement)

    text = ''.join(char for char in text if char in GSM7_ALL)

    # Dropping an emoji usually leaves a double space or a space before
    # punctuation; tidy that up so the message still reads naturally.
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r' ([,.!?])', r'\1', text)
    return text.strip()


def sms_page_count(text):
    """
    How many SMS pages Termii will bill for this text.

    Used by --dry-run so cost is visible before a bulk send.
    """
    if not text:
        return 0

    is_gsm7 = all(char in GSM7_ALL for char in text)
    if is_gsm7:
        # Extension characters occupy two septets.
        length = sum(2 if char in GSM7_EXTENDED else 1 for char in text)
        single, multipart = GSM7_SINGLE, GSM7_MULTIPART
    else:
        # Characters outside the BMP (most emoji) need two UTF-16 code units.
        length = sum(2 if ord(char) > 0xFFFF else 1 for char in text)
        single, multipart = UCS2_SINGLE, UCS2_MULTIPART

    if length <= single:
        return 1
    return -(-length // multipart)  # ceiling division


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_message(kind, worker, extra_context=None):
    """
    Build the text for one alert type.

    Returns (subject, email_body, sms_body). The email body keeps the original
    wording; the SMS body is GSM-7 sanitised.
    """
    template = MESSAGES[kind]
    # `date` defaults to today so a caller forgetting it can't raise KeyError
    # mid-send. Callers may override it.
    context = {
        'name': worker.full_name,
        'date': date.today().strftime('%A, %d %B %Y'),
    }
    if extra_context:
        context.update(extra_context)

    subject = template['subject'].format(**context)
    body = template['body'].format(**context)
    return subject, body, to_gsm7(body)


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------

def normalise_nigerian_number(raw):
    """
    Put a phone number into the international form Termii expects: no '+',
    country code first. '08031234567' -> '2348031234567'.
    """
    number = re.sub(r'[\s\-()]', '', (raw or '').strip())
    if number.startswith('+'):
        number = number[1:]
    if number.startswith('0') and len(number) == 11:
        number = '234' + number[1:]
    return number


def send_sms(to_number, text):
    """
    Send one SMS through Termii. Returns (ok, detail).

    `detail` is the Termii message id on success, or the reason on failure -
    it gets stored on AlertLog so failures are diagnosable later.
    """
    if not settings.MESSAGING_ENABLED:
        return False, 'Messaging is disabled (MESSAGING_ENABLED=false).'
    if not settings.TERMII_API_KEY:
        return False, 'Termii API key is not configured. Set TERMII_API_KEY in .env.'
    if not settings.TERMII_SENDER_ID:
        return False, 'Termii sender ID is not configured. Set TERMII_SENDER_ID in .env.'

    number = normalise_nigerian_number(to_number)
    if not number:
        return False, 'No phone number on record.'

    payload = {
        'to': number,
        'from': settings.TERMII_SENDER_ID,
        'sms': text,
        'type': 'plain',
        'channel': 'generic',
        'api_key': settings.TERMII_API_KEY,
    }

    try:
        response = requests.post(settings.TERMII_SMS_URL, json=payload, timeout=15)
    except requests.RequestException as exc:
        return False, f'Network error contacting Termii: {exc}'

    try:
        data = response.json()
    except ValueError:
        return False, f'Termii returned HTTP {response.status_code} with a non-JSON body.'

    if response.status_code == 200 and ('message_id' in data or data.get('status') == 'success'):
        return True, str(data.get('message_id', 'sent'))

    return False, data.get('message') or f'Termii error (HTTP {response.status_code}): {data}'


def send_email(to_address, subject, body):
    """Send one email through Gmail SMTP. Returns (ok, detail)."""
    if not settings.MESSAGING_ENABLED:
        return False, 'Messaging is disabled (MESSAGING_ENABLED=false).'
    if not settings.EMAIL_HOST_PASSWORD:
        return False, 'Gmail App Password is not configured. Set EMAIL_HOST_PASSWORD in .env.'
    if not to_address:
        return False, 'No email address on record.'

    # Split into paragraphs so the HTML version isn't one dense block.
    # The template auto-escapes each one.
    paragraphs = [part.strip() for part in re.split(r'\n{2,}', body.strip()) if part.strip()]
    html_body = render_to_string(
        'management/email/base_email.html',
        {'subject': subject, 'paragraphs': paragraphs, 'church_name': settings.CHURCH_NAME},
    )

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_address],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'

    return True, f'Email accepted by SMTP for {to_address}'


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

def resolve_channels(kind, worker):
    """Which channels apply to this worker for this alert type."""
    policy = CHANNEL_POLICY[kind]
    has_email = bool(worker.email)
    channels = []

    sms_rule = policy.get(SMS)
    if sms_rule == 'always' or (sms_rule == 'fallback' and not has_email):
        channels.append(SMS)

    email_rule = policy.get(EMAIL)
    if email_rule == 'always' or (email_rule == 'if_known' and has_email):
        channels.append(EMAIL)

    return channels


def already_sent(worker, kind, channel):
    """
    True if this message already went out successfully inside its dedupe window.

    Only SENT rows suppress a resend, so a previous failure will be retried.
    """
    window = DEDUPE_WINDOW.get(kind, 'day')
    query = AlertLog.objects.filter(
        worker=worker, type=kind, channel=channel, status='SENT'
    )
    if window == 'day':
        return query.filter(sent_at__date=timezone.localdate()).exists()
    cutoff = timezone.now() - timedelta(days=window)
    return query.filter(sent_at__gte=cutoff).exists()


def notify(worker, kind, extra_context=None, force=False):
    """
    Send one alert type to one worker across every channel that applies.

    Returns a list of (channel, status, detail) where status is one of
    'SENT', 'FAILED' or 'SKIPPED'. Never raises: one bad recipient must not
    abort the rest of a bulk run.
    """
    results = []

    try:
        subject, email_body, sms_body = render_message(kind, worker, extra_context)
        channels = resolve_channels(kind, worker)
    except Exception as exc:
        logger.exception('Could not build %s message for %s', kind, worker)
        return [('-', 'FAILED', f'Could not build message: {type(exc).__name__}: {exc}')]

    for channel in channels:
        if not force and already_sent(worker, kind, channel):
            results.append((channel, 'SKIPPED', 'Already sent within the dedupe window.'))
            continue

        # Recorded before sending so a crash leaves evidence, not a false SENT.
        log = AlertLog.objects.create(
            worker=worker, type=kind, channel=channel, status='PENDING'
        )

        try:
            if channel == SMS:
                ok, detail = send_sms(worker.phone_number, sms_body)
            else:
                ok, detail = send_email(worker.email, subject, email_body)
        except Exception as exc:
            # send_sms/send_email handle their own expected errors; this catches
            # anything unforeseen so the run continues to the next recipient.
            ok, detail = False, f'Unexpected error: {type(exc).__name__}: {exc}'
            logger.exception('Unexpected error sending %s %s to %s', kind, channel, worker)

        log.status = 'SENT' if ok else 'FAILED'
        log.detail = (detail or '')[:2000]
        log.save(update_fields=['status', 'detail'])

        if not ok:
            logger.warning('%s %s to %s failed: %s', kind, channel, worker.full_name, detail)
        results.append((channel, log.status, detail))

    return results


def notify_in_background(worker, kind, extra_context=None):
    """
    Fire notify() on a daemon thread.

    Used from the usher check-in view: a Termii HTTP call plus an SMTP
    handshake is seconds of latency, and the usher has a queue of people
    waiting. Failures are logged, never surfaced as an error to the usher.
    """
    def run():
        from django.db import connection
        try:
            notify(worker, kind, extra_context)
        except Exception:
            logger.exception('Background %s notification failed for %s', kind, worker)
        finally:
            # Django opens a fresh connection per thread; close it so
            # long-running servers don't accumulate idle connections.
            connection.close()

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------
# Dry-run preview
# --------------------------------------------------------------------------

def preview(workers, kind, extra_context=None):
    """
    What a bulk send would do, without sending anything.

    Returns a dict with per-channel recipient counts, total billable SMS
    pages, and the rendered SMS text - so cost is visible before spending it.
    """
    summary = {
        'kind': kind,
        'total_workers': 0,
        'sms_recipients': 0,
        'email_recipients': 0,
        'skipped': 0,
        'sms_pages': 0,
        'sms_text': '',
        'unreachable': [],
    }

    for worker in workers:
        summary['total_workers'] += 1
        _, _, sms_body = render_message(kind, worker, extra_context)
        if not summary['sms_text']:
            summary['sms_text'] = sms_body

        channels = resolve_channels(kind, worker)
        if not channels:
            summary['unreachable'].append(worker.full_name)
            continue

        for channel in channels:
            if already_sent(worker, kind, channel):
                summary['skipped'] += 1
            elif channel == SMS:
                summary['sms_recipients'] += 1
                summary['sms_pages'] += sms_page_count(sms_body)
            else:
                summary['email_recipients'] += 1

    return summary
