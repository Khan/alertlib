#!/usr/bin/env python

"""Tests for alertlib/__init__.py."""

import contextlib
import json
import logging
import sys
import syslog
import time
import types
import unittest

# Before we can import alertlib, we need to define a module 'secrets'
# so the alertlib import can succeed
fake_secrets = types.ModuleType('secrets')
fake_secrets.__name__ = 'secrets'
fake_secrets.hipchat_alertlib_token = '<hipchat token>'
fake_secrets.hostedgraphite_api_key = '<hostedgraphite API key>'
fake_secrets.slack_alertlib_webhook_url = '<slack webhook url>'
sys.modules['secrets'] = fake_secrets

# And we want the google tests to work even without appengine installed.
fake_google_mail = types.ModuleType('google_mail')
fake_google_mail.__name__ = 'google_mail'
sys.modules['google_mail'] = fake_google_mail

# This makes it so we can find alertlib when running from repo-root.
sys.path.insert(1, '.')
import alertlib


@contextlib.contextmanager
def disable_google_mail():
    def google_mail_fail(*args, **kwargs):
        raise AssertionError('Google mail does not work!')

    orig_send_to_gae_email = alertlib.Alert._send_to_gae_email
    alertlib.Alert._send_to_gae_email = google_mail_fail
    try:
        yield
    finally:
        alertlib.Alert._send_to_gae_email = orig_send_to_gae_email


class TestBase(unittest.TestCase):
    def setUp(self):
        super(TestBase, self).setUp()

        self.maxDiff = None      # (We can have big diffs if tests fail)

        self.sent_to_hipchat = []
        self.sent_to_slack = []
        self.sent_to_google_mail = []
        self.sent_to_sendmail = []
        self.sent_to_info_log = []
        self.sent_to_warning_log = []
        self.sent_to_error_log = []
        self.sent_to_syslog = []
        self.sent_to_graphite = []

        class FakeSMTP(object):
            """We need to fake out the sendmail() and quit() methods."""
            def __init__(*args, **kwargs):
                pass

            def sendmail(_, frm, to, msg):
                self.sent_to_sendmail.append((frm, to, msg))

            def quit(_):
                pass

        class FakeGraphiteSocket(object):
            @staticmethod
            def send(arg):
                self.sent_to_graphite.append(arg)

        # We need to mock out a bunch of stuff so we don't actually
        # talk to the real world.
        self.mock(alertlib.Alert, '_make_hipchat_api_call',
                  lambda s, post_dict: self.sent_to_hipchat.append(post_dict))

        self.mock(alertlib.Alert, '_make_slack_webhook_post',
                  lambda s, payload: self.sent_to_slack.append(payload))

        self.mock(alertlib.google_mail, 'send_mail',
                  lambda **kwargs: self.sent_to_google_mail.append(kwargs))

        self.mock(alertlib.smtplib, 'SMTP', FakeSMTP)

        self.mock(alertlib.logging, 'info',
                  lambda *args: self.sent_to_info_log.append(args))

        self.mock(alertlib.logging, 'warning',
                  lambda *args: self.sent_to_warning_log.append(args))

        self.mock(alertlib.logging, 'error',
                  lambda *args: self.sent_to_error_log.append(args))

        self.mock(alertlib.syslog, 'syslog',
                  lambda prio, msg: self.sent_to_syslog.append((prio, msg)))

        self.mock(alertlib, '_graphite_socket',
                  lambda hostname: FakeGraphiteSocket)

    def tearDown(self):
        # None of the tests should have caused any errors.
        self.assertEqual([], self.sent_to_error_log)

    def mock(self, container, var_str, new_value):
        if hasattr(container, var_str):
            old_value = getattr(container, var_str)
            self.addCleanup(lambda: setattr(container, var_str, old_value))
        else:
            self.addCleanup(lambda: delattr(container, var_str))
        setattr(container, var_str, new_value)


class HipchatTest(TestBase):
    def test_options(self):
        alertlib.Alert('test message') \
            .send_to_hipchat('1s and 0s', color='gray', notify=True)
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'gray',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 1,
                           'room_id': '1s and 0s'}],
                         self.sent_to_hipchat)

    def test_custom_sender(self):
        alertlib.Alert('test message') \
            .send_to_hipchat('1s and 0s', sender='Notification Newt')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'Notification Newt',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and 0s'}],
                         self.sent_to_hipchat)

    def test_debug_severity(self):
        alertlib.Alert('test message', severity=logging.DEBUG) \
            .send_to_hipchat('1s and 0s')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'gray',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and 0s'}],
                         self.sent_to_hipchat)

    def test_error_severity(self):
        alertlib.Alert('test message', severity=logging.ERROR) \
            .send_to_hipchat('1s and 0s')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'red',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and 0s'}],
                         self.sent_to_hipchat)

    def test_critical_severity(self):
        alertlib.Alert('test message', severity=logging.CRITICAL) \
            .send_to_hipchat('1s and 0s')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'red',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 1,
                           'room_id': '1s and 0s'}],
                         self.sent_to_hipchat)

    def test_message_truncation(self):
        alertlib.Alert('a' * 30000).send_to_hipchat('1s and 0s')
        self.assertLess(len(self.sent_to_hipchat[0]['message']), 10000)

    def test_utf8(self):
        alertlib.Alert(u'\xf7').send_to_hipchat(u'1s and \xf7s')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '\xc3\xb7',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and \xc3\xb7s'}],
                         self.sent_to_hipchat)

    def test_summary(self):
        alertlib.Alert('test message', summary='test').send_to_hipchat('room')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'room'},
                          {'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'room'}],
                         self.sent_to_hipchat)

    def test_html(self):
        alertlib.Alert('<b>test message</b>', html=True).send_to_hipchat('rm')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '<b>test message</b>',
                           'message_format': 'html',
                           'notify': 0,
                           'room_id': 'rm'}],
                         self.sent_to_hipchat)

    def test_nix_emoticons(self):
        alertlib.Alert('(commit 345d8)', summary='(345d8)').send_to_hipchat(
            'rm')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '(345d8\xe2\x80\x8b)',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'rm'},
                          {'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '(commit 345d8\xe2\x80\x8b)',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'rm'}],
                         self.sent_to_hipchat)

    def test_no_message_munging_in_html(self):
        """html mode doesn't display emoticons, so no need to munge them."""
        alertlib.Alert('(commit 345d8)', html=True).send_to_hipchat('rm')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '(commit 345d8)',
                           'message_format': 'html',
                           'notify': 0,
                           'room_id': 'rm'}],
                         self.sent_to_hipchat)


class SlackTest(TestBase):
    def test_default_options(self):
        alertlib.Alert('test message').send_to_slack('#bot-testing')
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(actual['channel'], '#bot-testing')
        self.assertEqual(actual['username'], 'AlertiGator')
        self.assertEqual(actual['icon_emoji'], ':crocodile:')
        self.assertEqual(len(actual['attachments']), 1)
        self.assertEqual(actual['attachments'][0]['text'], 'test message')
        self.assertEqual(actual['attachments'][0]['fallback'], 'test message')

    def test_specified_options(self):
        alertlib.Alert('test message').send_to_slack('#bot-testing',
                                                     sender='Bob Bot',
                                                     icon_emoji=':poop:')
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(actual['channel'], '#bot-testing')
        self.assertEqual(actual['username'], 'Bob Bot')
        self.assertEqual(actual['icon_emoji'], ':poop:')
        self.assertEqual(len(actual['attachments']), 1)

    def test_default_alert_with_summary(self):
        alertlib.Alert('xyz', summary='ABC').send_to_slack('#bot-testing')
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(len(actual['attachments']), 1)
        self.assertEqual(actual['attachments'][0]['pretext'], 'ABC')
        self.assertEqual(actual['attachments'][0]['text'], 'xyz')
        self.assertEqual(actual['attachments'][0]['fallback'], 'ABC - xyz')

    def test_default_alert_with_severity(self):
        alertlib.Alert('test message', severity=logging.CRITICAL) \
            .send_to_slack('#bot-testing')
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(len(actual['attachments']), 1)
        self.assertEqual(actual['attachments'][0]['color'], 'danger')

    def test_simple_message(self):
        alertlib.Alert('test message') \
            .send_to_slack('#bot-testing', simple_message=True)
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(actual['text'], 'test message')
        self.assertIsNone(actual.get('attachments'))

    def test_custom_attachments(self):
        alertlib.Alert('test message').send_to_slack(
            '#bot-testing',
            attachments=[
                {"text": "hi mom"},
                {"text": "hi dad", "color": "#abcdef"}
            ]
        )
        actual = json.loads(self.sent_to_slack[0])
        self.assertIsNone(actual.get('text'))
        self.assertEqual(len(actual['attachments']), 2)
        self.assertEqual(actual['attachments'][0]['text'], 'hi mom')
        self.assertEqual(actual['attachments'][1]['text'], 'hi dad')
        self.assertEqual(actual['attachments'][1]['color'], '#abcdef')

    def test_warn_on_html(self):
        alertlib.Alert('test <b>message</b>', html=True) \
            .send_to_slack('#bot-testing')
        self.assertIn("Unsupported HTML msg being sent to Slack!: %s",
                      self.sent_to_warning_log[0])


class EmailTest(TestBase):
    def test_google_mail(self):
        alertlib.Alert('test message') \
            .send_to_email('ka-admin') \
            .send_to_pagerduty('oncall')

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org']},
                          {'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['oncall@khan-academy.pagerduty.com']}],
                         self.sent_to_google_mail)
        self.assertEqual([], self.sent_to_sendmail)

    def test_sendmail(self):
        with disable_google_mail():
            alertlib.Alert('test message') \
                .send_to_pagerduty('oncall') \
                .send_to_email('ka-admin')

        self.assertEqual([('no-reply@khanacademy.org',
                           ['oncall@khan-academy.pagerduty.com'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: oncall@khan-academy.pagerduty.com\n\n'
                           'test message\n'
                           ),
                          ('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)
        self.assertEqual([], self.sent_to_google_mail)

    def test_multiple_recipients(self):
        alertlib.Alert('test message').send_to_email(['ka-admin',
                                                      'ka-blackhole'])
        with disable_google_mail():
            alertlib.Alert('test message').send_to_email(['ka-admin',
                                                          'ka-blackhole'])

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org',
                                  'ka-blackhole@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org',
                            'ka-blackhole@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org,'
                           ' ka-blackhole@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_specified_hostname(self):
        alertlib.Alert('test message').send_to_email(
            'ka-admin@khanacademy.org')
        with disable_google_mail():
            alertlib.Alert('test message').send_to_email(
                'ka-admin@khanacademy.org')
        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_illegal_hostname(self):
        with self.assertRaises(ValueError):
            alertlib.Alert('test message').send_to_email(
                'ka-admin@appspot.org')

        with disable_google_mail():
            with self.assertRaises(ValueError):
                alertlib.Alert('test message').send_to_email(
                    'ka-admin@appspot.org')

    def test_cc_and_bcc(self):
        alertlib.Alert('test message').send_to_email(
            ['ka-admin', 'ka-blackhole'],
            cc='ka-cc',
            bcc=['ka-bcc', 'ka-hidden'])
        with disable_google_mail():
            alertlib.Alert('test message').send_to_email(
                ['ka-admin', 'ka-blackhole'],
                cc='ka-cc',
                bcc=['ka-bcc', 'ka-hidden'])

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org',
                                  'ka-blackhole@khanacademy.org'],
                           'cc': ['ka-cc@khanacademy.org'],
                           'bcc': ['ka-bcc@khanacademy.org',
                                   'ka-hidden@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org',
                            'ka-blackhole@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org,'
                           ' ka-blackhole@khanacademy.org\n'
                           'Cc: ka-cc@khanacademy.org\n'
                           'Bcc: ka-bcc@khanacademy.org,'
                           ' ka-hidden@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_sender(self):
        sender = 'foo$123*bar'
        clean_sender = 'foo-123-bar'
        alertlib.Alert('test message').send_to_email(
            ['ka-admin', 'ka-blackhole'], sender=sender)
        with disable_google_mail():
            alertlib.Alert('test message').send_to_email(
                ['ka-admin', 'ka-blackhole'], sender=sender)

        self.assertEqual([{'body': 'test message\n',
                           'sender': ('alertlib <no-reply+%s@khanacademy.org>'
                                      % clean_sender),
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org',
                                  'ka-blackhole@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org',
                            'ka-blackhole@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: test message\n'
                           'From: alertlib <no-reply+%s@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org,'
                           ' ka-blackhole@khanacademy.org\n\n'
                           'test message\n' % clean_sender
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_error_severity(self):
        alertlib.Alert('test message', severity=logging.ERROR).send_to_email(
            'ka-admin')
        with disable_google_mail():
            alertlib.Alert('test message',
                           severity=logging.ERROR).send_to_email('ka-admin')

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'ERROR: test message',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: ERROR: test message\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_error_severity_with_explicit_summary(self):
        # An explicit summary suppresses the 'ERROR: ' prefix.
        alertlib.Alert('test message', severity=logging.ERROR,
                       summary='a test...').send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert('test message', severity=logging.ERROR,
                           summary='a test...').send_to_email('ka-admin')

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'a test...',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: a test...\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_explicit_summary(self):
        alertlib.Alert('test message', summary='this is...').send_to_email(
            'ka-admin')
        with disable_google_mail():
            alertlib.Alert('test message', summary='this is...').send_to_email(
                'ka-admin')

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'this is...',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: this is...\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'test message\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_implicit_summary_first_line(self):
        message = 'This text is short\nBut has multiple lines'
        alertlib.Alert(message).send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert(message).send_to_email('ka-admin')

        self.assertEqual([{'body': message + '\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'This text is short',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: This text is short\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           '%s\n' % message
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_implicit_summary_long_first_line(self):
        message = ('This text is long, it is very very long, '
                   'I cannot even say how long it will go on for, '
                   'but probably a long time a long time.\n'
                   'Finally, a second line!')
        alertlib.Alert(message).send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert(message).send_to_email('ka-admin')

        self.assertEqual([{'body': message + '\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'This text is long, it is very very '
                           'long, I cannot even say h',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: This text is long, it is very very '
                           'long, I cannot even say h\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           '%s\n' % message
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_implicit_summary_long_first_line_with_period(self):
        message = ('This text is long.  It is very very long, '
                   'I cannot even say how long it will go on for. '
                   'Probably a long time a long time.\n'
                   'Finally, a second line!')
        alertlib.Alert(message).send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert(message).send_to_email('ka-admin')

        self.assertEqual([{'body': message + '\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'This text is long',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: This text is long\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           '%s\n' % message
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_html_email(self):
        alertlib.Alert('<b>fire!</b>', html=True).send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert('<b>fire!</b>', html=True).send_to_email('ka-admin')

        self.assertEqual([{'body': '<b>fire!</b>\n',
                           'html': '<b>fire!</b>\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': '',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/html; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: \n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           '<b>fire!</b>\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_empty_message(self):
        alertlib.Alert('').send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert('').send_to_email('ka-admin')

        self.assertEqual([{'body': '\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': '',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: \n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           '\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_extra_newlines(self):
        alertlib.Alert('yo!\n\n\n\n').send_to_email('ka-admin')
        with disable_google_mail():
            alertlib.Alert('yo!\n\n\n\n').send_to_email('ka-admin')

        self.assertEqual([{'body': 'yo!\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'yo!',
                           'to': ['ka-admin@khanacademy.org']}],
                         self.sent_to_google_mail)

        self.assertEqual([('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 7bit\n'
                           'Subject: yo!\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'yo!\n'
                           ),
                          ],
                         self.sent_to_sendmail)

    def test_utf8(self):
        with disable_google_mail():
            alertlib.Alert(u'yo \xf7', summary=u'yep \xf7') \
                .send_to_pagerduty('oncall') \
                .send_to_email('ka-admin')

        self.assertEqual([('no-reply@khanacademy.org',
                           ['oncall@khan-academy.pagerduty.com'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 8bit\n'
                           'Subject: yep \xc3\xb7\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: oncall@khan-academy.pagerduty.com\n\n'
                           'yo \xc3\xb7\n'
                           ),
                          ('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="us-ascii"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: 8bit\n'
                           'Subject: yep \xc3\xb7\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'yo \xc3\xb7\n'
                           ),
                          ],
                         self.sent_to_sendmail)
        self.assertEqual([], self.sent_to_google_mail)


class PagerDutyTest(TestBase):
    def test_multiple_recipients(self):
        alertlib.Alert('on fire!').send_to_pagerduty(['oncall', 'backup'])
        self.assertEqual([{'body': 'on fire!\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'on fire!',
                           'to': ['oncall@khan-academy.pagerduty.com',
                                  'backup@khan-academy.pagerduty.com']}],
                         self.sent_to_google_mail)

    def test_specified_hostname(self):
        # You can't specify a hostname, even if it's the right one
        with self.assertRaises(ValueError):
            alertlib.Alert('on fire!').send_to_pagerduty(
                'oncall@khan-academy.pagerduty.com')

    def test_service_name_to_email(self):
        alertlib.Alert('on fire!').send_to_pagerduty(
            ['The oncall-service, at your service!'])
        self.assertEqual([{'body': 'on fire!\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'on fire!',
                           'to': ['theoncall-serviceatyourservice'
                                  '@khan-academy.pagerduty.com']}],
                         self.sent_to_google_mail)


class LogsTest(TestBase):
    def test_error_severity(self):
        alertlib.Alert('test message', severity=logging.ERROR).send_to_logs()
        self.assertEqual([(syslog.LOG_ERR, 'test message')],
                         self.sent_to_syslog)

    def test_unknown_severity(self):
        alertlib.Alert('test message', severity=-4).send_to_logs()
        self.assertEqual([(syslog.LOG_INFO, 'test message')],
                         self.sent_to_syslog)


class GraphiteTest(TestBase):
    def test_value(self):
        alertlib.Alert('test message').send_to_graphite(
            'stats.test_message', 4)
        self.assertEqual(['<hostedgraphite API key>.stats.test_message 4\n'],
                         self.sent_to_graphite)

    def test_default_value(self):
        alertlib.Alert('test message').send_to_graphite('stats.test_message')
        self.assertEqual(['<hostedgraphite API key>.stats.test_message 1\n'],
                         self.sent_to_graphite)


class RateLimitingTest(TestBase):
    @staticmethod
    @contextlib.contextmanager
    def _mock_time(new_time):
        old_time = time.time
        time.time = lambda: new_time
        try:
            yield
        finally:
            time.time = old_time

    @staticmethod
    def _set_time(new_time):
        """Only call this within a mock-time context!"""
        time.time = lambda: new_time

    def test_no_rate_limiting(self):
        alert = alertlib.Alert('test message')
        for _ in xrange(100):
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(100, len(self.sent_to_graphite))

    def test_burst(self):
        alert = alertlib.Alert('test message', rate_limit=60)
        for _ in xrange(100):
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(1, len(self.sent_to_graphite))

    def test_different_alert_objects(self):
        # Objects don't share state, so we won't rate limit here.
        for _ in xrange(100):
            alertlib.Alert('test message').send_to_graphite(
                'stats.test_message', 4)
        self.assertEqual(100, len(self.sent_to_graphite))

    def test_limiting_with_longer_delay(self):
        alert = alertlib.Alert('test message', rate_limit=60)
        with self._mock_time(10):
            alert.send_to_graphite('stats.test_message', 4)
            self._set_time(20)
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(1, len(self.sent_to_graphite))

    def test_no_limiting_with_longer_delay(self):
        alert = alertlib.Alert('test message', rate_limit=60)
        with self._mock_time(10):
            alert.send_to_graphite('stats.test_message', 4)
            self._set_time(100)
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(2, len(self.sent_to_graphite))

    def test_limiting_on_different_services(self):
        alert = alertlib.Alert('test message', rate_limit=60)
        for _ in xrange(100):
            alert.send_to_graphite('stats.test_message', 4) \
                 .send_to_hipchat('1s and 0s')
            alert.send_to_logs()
        self.assertEqual(1, len(self.sent_to_graphite))
        self.assertEqual(1, len(self.sent_to_hipchat))
        self.assertEqual(1, len(self.sent_to_syslog))


class IntegrationTest(TestBase):
    def test_chaining(self):
        # We send to hipchat a second time to make sure that
        # send_to_graphite() support chaining properly (by returning
        # self).
        alertlib.Alert('test message') \
            .send_to_hipchat('1s and 0s') \
            .send_to_email('ka-admin') \
            .send_to_pagerduty('oncall') \
            .send_to_logs() \
            .send_to_graphite('stats.alerted') \
            .send_to_hipchat('test')

        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and 0s'},
                          {'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'test'}],
                         self.sent_to_hipchat)

        self.assertEqual([{'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org']},
                          {'body': 'test message\n',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['oncall@khan-academy.pagerduty.com']}],
                         self.sent_to_google_mail)

        self.assertEqual([],
                         self.sent_to_sendmail)

        self.assertEqual([(6, 'test message')],
                         self.sent_to_syslog)

        self.assertEqual(['<hostedgraphite API key>.stats.alerted 1\n'],
                         self.sent_to_graphite)

    def test_test_mode(self):
        alertlib.enter_test_mode()
        try:
            alertlib.Alert('test message') \
                .send_to_hipchat('1s and 0s') \
                .send_to_email('ka-admin') \
                .send_to_pagerduty('oncall') \
                .send_to_logs() \
                .send_to_graphite('stats.alerted')
        finally:
            alertlib.exit_test_mode()

        # Should only log, not send to anything
        self.assertEqual([], self.sent_to_hipchat)
        self.assertEqual([], self.sent_to_google_mail)
        self.assertEqual([], self.sent_to_sendmail)
        self.assertEqual([], self.sent_to_syslog)
        self.assertEqual([], self.sent_to_graphite)

        self.assertEqual(
            [('alertlib: would send to hipchat room 1s and 0s: '
              'test message',),
             ("alertlib: would send email to "
              "['ka-admin@khanacademy.org'] "
              "(from alertlib <no-reply@khanacademy.org> CC None BCC None): "
              "(subject test message) test message",),
             ("alertlib: would send pagerduty email to "
              "['oncall@khan-academy.pagerduty.com'] "
              "(subject test message) test message",),
             ('alertlib: would send to graphite: stats.alerted 1',)
             ],
            self.sent_to_info_log)

    def test_rate_limiting(self):
        """Make sure we put rate-limiting properly on each service."""
        alert = alertlib.Alert('test message', rate_limit=60)
        for _ in xrange(10):
            alert.send_to_hipchat('1s and 0s') \
                 .send_to_email('ka-admin') \
                 .send_to_pagerduty('oncall') \
                 .send_to_logs() \
                 .send_to_graphite('stats.alerted')

        # Should only log once
        self.assertEqual(1, len(self.sent_to_hipchat))
        # Well, google mail gets a second one due to pagerduty.
        self.assertEqual(2, len(self.sent_to_google_mail))
        # And sendmail gets none because we're using googlemail.
        self.assertEqual(0, len(self.sent_to_sendmail))
        self.assertEqual(1, len(self.sent_to_syslog))
        self.assertEqual(1, len(self.sent_to_graphite))

    def test_gae_sandbox(self):
        # Stub out imports just like appengine would.
        old_smtplib = alertlib.smtplib
        old_syslog = alertlib.syslog
        try:
            del alertlib.smtplib
            del alertlib.syslog

            # Just make sure nothing crashes
            alertlib.Alert('test message') \
                .send_to_hipchat('1s and 0s') \
                .send_to_email('ka-admin') \
                .send_to_pagerduty('oncall') \
                .send_to_logs() \
                .send_to_graphite('stats.alerted')
        finally:
            alertlib.smtplib = old_smtplib
            alertlib.syslog = old_syslog


if __name__ == '__main__':
    unittest.main()
