"""
Tests for the messaging layer.

Nothing here touches the network: send_sms and send_email are patched, so the
suite verifies routing, dedupe and text handling without spending money.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from . import notifications
from .attendance import calculate_consecutive_misses, get_current_sunday_reference
from .models import AlertLog, CheckIn, Department, Worker


class GSM7SanitisingTests(TestCase):
    """SMS text has to survive as GSM-7 or Termii bills us double."""

    def test_strips_emoji(self):
        self.assertEqual(notifications.to_gsm7('Happy Birthday! 🎉🎂❤️'), 'Happy Birthday!')

    def test_converts_typographic_dashes(self):
        self.assertEqual(notifications.to_gsm7('a — b – c'), 'a - b - c')

    def test_converts_curly_quotes(self):
        self.assertEqual(notifications.to_gsm7('we’re “here”'), 'we\'re "here"')

    def test_tidies_space_left_by_removed_character(self):
        # Dropping the emoji leaves a double space and a space before the comma.
        self.assertEqual(notifications.to_gsm7('Hi 🎉 there'), 'Hi there')
        self.assertEqual(notifications.to_gsm7('Hi 🎉, there'), 'Hi, there')

    def test_plain_ascii_is_unchanged(self):
        text = "Hi Ada, service begins today at 8am. See you soon! - Noah's Ark"
        self.assertEqual(notifications.to_gsm7(text), text)

    def test_every_message_is_gsm7_clean_after_sanitising(self):
        for kind in notifications.MESSAGES:
            body = notifications.MESSAGES[kind]['body'].format(
                name='Ada', date='Sunday, 6 September 2026'
            )
            cleaned = notifications.to_gsm7(body)
            leftover = {c for c in cleaned if c not in notifications.GSM7_ALL}
            self.assertEqual(leftover, set(), f'{kind} still has non-GSM-7 characters')


class SmsPageCountTests(TestCase):
    def test_empty_text_costs_nothing(self):
        self.assertEqual(notifications.sms_page_count(''), 0)

    def test_short_gsm7_is_one_page(self):
        self.assertEqual(notifications.sms_page_count('a' * 160), 1)

    def test_gsm7_over_one_page_uses_multipart_size(self):
        self.assertEqual(notifications.sms_page_count('a' * 161), 2)
        self.assertEqual(notifications.sms_page_count('a' * 306), 2)
        self.assertEqual(notifications.sms_page_count('a' * 307), 3)

    def test_emoji_forces_ucs2_and_costs_more(self):
        # 70 UCS-2 units is one page; an emoji counts as two units.
        self.assertEqual(notifications.sms_page_count('a' * 68 + '🎉'), 1)
        self.assertEqual(notifications.sms_page_count('a' * 69 + '🎉'), 2)

    def test_sanitising_reduces_billed_pages(self):
        body = notifications.MESSAGES[notifications.BIRTHDAY]['body'].format(name='Ada')
        raw_pages = notifications.sms_page_count(body)
        clean_pages = notifications.sms_page_count(notifications.to_gsm7(body))
        self.assertLess(clean_pages, raw_pages)


class PhoneNumberTests(TestCase):
    def test_local_number_gets_country_code(self):
        self.assertEqual(notifications.normalise_nigerian_number('08031234567'), '2348031234567')

    def test_plus_prefix_is_stripped(self):
        self.assertEqual(notifications.normalise_nigerian_number('+2348031234567'), '2348031234567')

    def test_spaces_and_punctuation_are_removed(self):
        self.assertEqual(notifications.normalise_nigerian_number(' 0803 123-4567 '), '2348031234567')

    def test_already_international_is_left_alone(self):
        self.assertEqual(notifications.normalise_nigerian_number('2348031234567'), '2348031234567')


class WorkerFactoryMixin:
    def make_worker(self, name='Ada Obi', email=None, phone=None, status='ACTIVE'):
        return Worker.objects.create(
            full_name=name,
            phone_number=phone or f'080{Worker.objects.count():08d}',
            email=email,
            status=status,
            department=self.department,
        )

    def setUp(self):
        self.department = Department.objects.create(name='Choir Department')


class ChannelPolicyTests(WorkerFactoryMixin, TestCase):
    def test_birthday_goes_to_both_channels_when_email_known(self):
        worker = self.make_worker(email='ada@example.com')
        self.assertEqual(
            notifications.resolve_channels(notifications.BIRTHDAY, worker),
            [notifications.SMS, notifications.EMAIL],
        )

    def test_birthday_still_goes_by_sms_without_an_email(self):
        worker = self.make_worker(email=None)
        self.assertEqual(
            notifications.resolve_channels(notifications.BIRTHDAY, worker),
            [notifications.SMS],
        )

    def test_checkin_confirmation_prefers_email_and_skips_sms(self):
        """The highest-volume message: email is free, so SMS is fallback only."""
        worker = self.make_worker(email='ada@example.com')
        self.assertEqual(
            notifications.resolve_channels(notifications.CHECKIN_CONFIRM, worker),
            [notifications.EMAIL],
        )

    def test_checkin_confirmation_falls_back_to_sms_without_an_email(self):
        worker = self.make_worker(email=None)
        self.assertEqual(
            notifications.resolve_channels(notifications.CHECKIN_CONFIRM, worker),
            [notifications.SMS],
        )


@patch.object(notifications, 'send_email', return_value=(True, 'ok'))
@patch.object(notifications, 'send_sms', return_value=(True, 'msg-123'))
class NotifyTests(WorkerFactoryMixin, TestCase):
    def test_logs_one_row_per_channel(self, mock_sms, mock_email):
        worker = self.make_worker(email='ada@example.com')
        results = notifications.notify(worker, notifications.BIRTHDAY)

        self.assertEqual(len(results), 2)
        self.assertEqual(AlertLog.objects.filter(status='SENT').count(), 2)
        self.assertEqual(
            set(AlertLog.objects.values_list('channel', flat=True)),
            {notifications.SMS, notifications.EMAIL},
        )

    def test_sms_receives_sanitised_text_and_email_keeps_emoji(self, mock_sms, mock_email):
        worker = self.make_worker(email='ada@example.com')
        notifications.notify(worker, notifications.BIRTHDAY)

        sms_text = mock_sms.call_args[0][1]
        email_text = mock_email.call_args[0][2]
        self.assertNotIn('🎂', sms_text)
        self.assertIn('🎂', email_text)

    def test_provider_detail_is_stored(self, mock_sms, mock_email):
        worker = self.make_worker()
        notifications.notify(worker, notifications.BIRTHDAY)
        self.assertEqual(AlertLog.objects.get(channel=notifications.SMS).detail, 'msg-123')

    def test_second_run_skips_instead_of_resending(self, mock_sms, mock_email):
        worker = self.make_worker(email='ada@example.com')
        notifications.notify(worker, notifications.BIRTHDAY)
        results = notifications.notify(worker, notifications.BIRTHDAY)

        self.assertTrue(all(status == 'SKIPPED' for _, status, _ in results))
        self.assertEqual(mock_sms.call_count, 1)
        self.assertEqual(AlertLog.objects.count(), 2)  # no extra rows

    def test_force_overrides_the_dedupe_window(self, mock_sms, mock_email):
        worker = self.make_worker()
        notifications.notify(worker, notifications.BIRTHDAY)
        notifications.notify(worker, notifications.BIRTHDAY, force=True)
        self.assertEqual(mock_sms.call_count, 2)

    def test_placeholders_are_filled(self, mock_sms, mock_email):
        worker = self.make_worker(name='Ada Obi')
        notifications.notify(
            worker, notifications.CHECKIN_CONFIRM, {'date': 'Sunday, 6 September 2026'}
        )
        text = mock_sms.call_args[0][1]
        self.assertIn('Ada Obi', text)
        self.assertIn('Sunday, 6 September 2026', text)
        self.assertNotIn('{', text)


class NotifyFailureTests(WorkerFactoryMixin, TestCase):
    @patch.object(notifications, 'send_sms', return_value=(False, 'Termii rejected the number'))
    def test_failure_is_recorded_with_its_reason(self, mock_sms):
        worker = self.make_worker()
        results = notifications.notify(worker, notifications.BIRTHDAY)

        self.assertEqual(results[0][1], 'FAILED')
        log = AlertLog.objects.get()
        self.assertEqual(log.status, 'FAILED')
        self.assertEqual(log.detail, 'Termii rejected the number')

    @patch.object(notifications, 'send_sms', return_value=(False, 'network down'))
    def test_a_failed_send_is_retried_next_run(self, mock_sms):
        """A FAILED row must not suppress the retry the way a SENT row does."""
        worker = self.make_worker()
        notifications.notify(worker, notifications.BIRTHDAY)
        notifications.notify(worker, notifications.BIRTHDAY)
        self.assertEqual(mock_sms.call_count, 2)

    @patch.object(notifications, 'send_sms', side_effect=RuntimeError('boom'))
    def test_an_unexpected_error_is_recorded_and_does_not_propagate(self, mock_sms):
        """One bad recipient must not abort a bulk run."""
        worker = self.make_worker()
        results = notifications.notify(worker, notifications.BIRTHDAY)

        self.assertEqual(results[0][1], 'FAILED')
        log = AlertLog.objects.get()
        self.assertEqual(log.status, 'FAILED')
        self.assertIn('RuntimeError', log.detail)


class MessagingDisabledTests(WorkerFactoryMixin, TestCase):
    @override_settings(MESSAGING_ENABLED=False)
    def test_kill_switch_blocks_sms(self):
        ok, detail = notifications.send_sms('08031234567', 'hello')
        self.assertFalse(ok)
        self.assertIn('disabled', detail)

    @override_settings(MESSAGING_ENABLED=False)
    def test_kill_switch_blocks_email(self):
        ok, detail = notifications.send_email('ada@example.com', 'Hi', 'hello')
        self.assertFalse(ok)
        self.assertIn('disabled', detail)

    @override_settings(MESSAGING_ENABLED=True, TERMII_API_KEY='')
    def test_missing_termii_key_fails_loudly_rather_than_sending(self):
        ok, detail = notifications.send_sms('08031234567', 'hello')
        self.assertFalse(ok)
        self.assertIn('TERMII_API_KEY', detail)


class AttendanceTests(WorkerFactoryMixin, TestCase):
    def test_sunday_reference_is_a_sunday(self):
        self.assertEqual(get_current_sunday_reference().weekday(), 6)

    def test_no_misses_when_checked_in_last_sunday(self):
        worker = self.make_worker()
        Worker.objects.filter(pk=worker.pk).update(
            date_joined=timezone.now() - timedelta(days=60)
        )
        worker.refresh_from_db()

        today = date.today()
        last_sunday = today if today.weekday() == 6 else today - timedelta(days=today.weekday() + 1)
        CheckIn.objects.create(
            worker=worker, sunday_reference=last_sunday, usher_id='test'
        )
        self.assertEqual(calculate_consecutive_misses(worker), 0)

    def test_counts_consecutive_misses_since_joining(self):
        worker = self.make_worker()
        Worker.objects.filter(pk=worker.pk).update(
            date_joined=timezone.now() - timedelta(days=21)
        )
        worker.refresh_from_db()
        # No check-ins at all, so every Sunday since joining is a miss.
        self.assertGreaterEqual(calculate_consecutive_misses(worker), 3)

    def test_inactive_workers_are_never_flagged(self):
        worker = self.make_worker(status='PENDING')
        Worker.objects.filter(pk=worker.pk).update(
            date_joined=timezone.now() - timedelta(days=60)
        )
        worker.refresh_from_db()
        self.assertEqual(calculate_consecutive_misses(worker), 0)


class EmailRenderingTests(WorkerFactoryMixin, TestCase):
    @override_settings(
        MESSAGING_ENABLED=True,
        EMAIL_HOST_PASSWORD='test-app-password',
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_email_has_a_plain_text_body_and_an_html_alternative(self):
        from django.core import mail

        ok, _ = notifications.send_email('ada@example.com', 'Happy Birthday', 'Hello Ada 🎂')
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)

        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Happy Birthday')
        self.assertIn('Hello Ada 🎂', message.body)

        html, content_type = message.alternatives[0]
        self.assertEqual(content_type, 'text/html')
        self.assertIn('Hello Ada 🎂', html)
        self.assertTrue("Noah's Ark" in html or "Noah&#x27;s Ark" in html)


@patch.object(notifications, 'send_email', return_value=(True, 'ok'))
@patch.object(notifications, 'send_sms', return_value=(True, 'msg-123'))
class PreviewTests(WorkerFactoryMixin, TestCase):
    def test_preview_counts_recipients_without_sending(self, mock_sms, mock_email):
        self.make_worker(name='With Email', email='a@example.com')
        self.make_worker(name='No Email')

        summary = notifications.preview(Worker.objects.all(), notifications.BIRTHDAY)

        self.assertEqual(summary['total_workers'], 2)
        self.assertEqual(summary['sms_recipients'], 2)  # BIRTHDAY is SMS 'always'
        self.assertEqual(summary['email_recipients'], 1)
        self.assertGreater(summary['sms_pages'], 0)
        mock_sms.assert_not_called()
        mock_email.assert_not_called()
        self.assertEqual(AlertLog.objects.count(), 0)

    def test_preview_reports_already_sent_as_skipped(self, mock_sms, mock_email):
        worker = self.make_worker()
        notifications.notify(worker, notifications.BIRTHDAY)

        summary = notifications.preview(Worker.objects.all(), notifications.BIRTHDAY)
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(summary['sms_recipients'], 0)
