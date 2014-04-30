#!/usr/bin/env python

"""Tests for alertlib/__init__.py."""

import sys
import types
import unittest

# Before we can import alertlib, we need to define a module 'secrets'
# so the alertlib import can succeed
fake_secrets = types.ModuleType('secrets')
fake_secrets.__name__ = 'secrets'
fake_secrets.hipchat_deploy_token = '<hipchat token>'
fake_secrets.hostedgraphite_api_key = '<hostedgraphite API key>'
sys.modules['secrets'] = fake_secrets

# And we want the google tests to work even without appengine installed.
fake_google_mail = types.ModuleType('google_mail')
fake_google_mail.__name__ = 'google_mail'
sys.modules['google_mail'] = fake_google_mail

# This makes it so we can find i18nize_templates when running from repo-root.
sys.path.insert(1, '.')
import alertlib


class TestBase(unittest.TestCase):
    def setUp(self):
        super(TestBase, self).setUp()

        self.maxDiff = None      # (We can have big diffs if tests fail)

        self.sent_to_hipchat = []
        self.sent_to_google_mail = []
        self.sent_to_sendmail = []
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
        alertlib.Alert._post_to_hipchat = (
            lambda s, post_dict: self.sent_to_hipchat.append(post_dict))

        alertlib.google_mail.send_mail = (
            lambda **kwargs: self.sent_to_google_mail.append(kwargs))

        alertlib.smtplib.SMTP = FakeSMTP

        alertlib.syslog.syslog = (
            lambda prio, msg: self.sent_to_syslog.append((prio, msg)))

        alertlib._graphite_socket = (
            lambda hostname: FakeGraphiteSocket)

    def test_simple(self):
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

        self.assertEqual([{'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': '1s and 0s'},
                          {'color': 'purple',
                           'from': 'AlertiGator',
                           'message': 'test message',
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'test'}],
                         self.sent_to_hipchat)

        self.assertEqual([{'body': 'test message',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['ka-admin@khanacademy.org']},
                          {'body': 'test message',
                           'sender': 'alertlib <no-reply@khanacademy.org>',
                           'subject': 'test message',
                           'to': ['oncall@khan-academy.pagerduty.com']}],
                         self.sent_to_google_mail)

        self.assertEqual([],
                         self.sent_to_sendmail)

        self.assertEqual([(6, 'test message')],
                         self.sent_to_syslog)

        self.assertEqual(['<hostedgraphite API key>.stats.alerted 1'],
                         self.sent_to_graphite)


if __name__ == '__main__':
    unittest.main()
