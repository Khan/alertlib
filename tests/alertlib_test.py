#!/usr/bin/env python

"""Tests for alertlib/__init__.py."""
import contextlib
import json
import logging
import six.moves.http_client
import importlib
import socket
import sys
import syslog
import time
import types
import unittest
import six

import apiclient.errors
import oauth2client
import mock


# Before we can import alertlib, we need to define a module 'secrets'
# so the alertlib import can succeed
fake_secrets = types.ModuleType('secrets')
fake_secrets.__name__ = 'secrets'
fake_secrets.hipchat_alertlib_token = '<hipchat token>'
fake_secrets.hostedgraphite_api_key = '<hostedgraphite API key>'
fake_secrets.slack_alertlib_webhook_url = '<slack webhook url>'
fake_secrets.asana_api_token = '<asana api token>'
fake_secrets.google_alertlib_service_account = "{}"
sys.modules['secrets'] = fake_secrets

# And we want the google tests to work even without appengine installed.
fake_google_mail = types.ModuleType('google_mail')
fake_google_mail.__name__ = 'google_mail'
sys.modules['google_mail'] = fake_google_mail

# This makes it so we can find alertlib when running from repo-root.
sys.path.insert(0, '.')
import alertlib

ALERTLIB_MODULES = (
    'asana',
    'email',
    'graphite',
    'hipchat',
    'logs',
    'pagerduty',
    'slack',
    'stackdriver',
)
for module in ALERTLIB_MODULES:
    importlib.import_module('alertlib.%s' % module)


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


class MockResponse:
    """Mock of six.moves.urllib.request.Request with only necessary methods."""
    def __init__(self, mock_read_val, mock_status_code):
        self.mock_read_val = json.dumps({'data': mock_read_val})
        self.mock_status_code = mock_status_code

    def read(self):
        return self.mock_read_val

    def getcode(self):
        return self.mock_status_code


class TestBase(unittest.TestCase):
    def setUp(self):
        super(TestBase, self).setUp()

        self.maxDiff = None      # (We can have big diffs if tests fail)

        self.sent_to_hipchat = []
        self.sent_to_slack = []
        self.sent_to_asana = []
        self.sent_to_google_mail = []
        self.sent_to_sendmail = []
        self.sent_to_info_log = []
        self.sent_to_warning_log = []
        self.sent_to_error_log = []
        self.sent_to_syslog = []
        self.sent_to_graphite = []
        self.sent_to_stackdriver = []

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
        self.mock_origs = {}   # used to unmock if needed

        self.mock(alertlib.hipchat, '_make_hipchat_api_call',
                  lambda post_dict: self.sent_to_hipchat.append(post_dict))

        self.mock(alertlib.slack, '_make_slack_webhook_post',
                  lambda payload: self.sent_to_slack.append(payload))

        self.mock(alertlib.email.google_mail, 'send_mail',
                  lambda **kwargs: self.sent_to_google_mail.append(kwargs))

        self.mock(alertlib.email.smtplib, 'SMTP', FakeSMTP)

        self.mock(alertlib.logs.syslog, 'syslog',
                  lambda prio, msg: self.sent_to_syslog.append((prio, msg)))

        self.mock(alertlib.graphite, '_graphite_socket',
                  lambda hostname: FakeGraphiteSocket)

        self.mock(alertlib.stackdriver, 'send_datapoints_to_stackdriver',
                  lambda data, *a, **kw: (
                      self.sent_to_stackdriver.extend(data)))

        for module in ALERTLIB_MODULES:
            alertlib_module = getattr(alertlib, module)
            logging_module = getattr(alertlib_module, 'logging')
            self.mock(logging_module, 'info',
                      lambda *args: self.sent_to_info_log.append(args))
            self.mock(logging_module, 'warning',
                      lambda *args: self.sent_to_warning_log.append(args))
            self.mock(logging_module, 'error',
                      lambda *args: self.sent_to_error_log.append(args))

    def tearDown(self):
        # None of the tests should have caused any errors unless specifcally
        # tested for in the test itself, which should reset sent_to_error_log
        # to [] before returning
        self.assertEqual([], self.sent_to_error_log)

        alertlib.asana._CACHED_ASANA_TAG_MAP = {}
        alertlib.asana._CACHED_ASANA_PROJECT_MAP = {}

    def mock(self, container, var_str, new_value):
        if hasattr(container, var_str):
            oldval = getattr(container, var_str)
            self.mock_origs[(container, var_str)] = oldval
            self.addCleanup(lambda: setattr(container, var_str, oldval))
        else:
            self.mock_origs[(container, var_str)] = None
            self.addCleanup(lambda: delattr(container, var_str))
        setattr(container, var_str, new_value)

    def unmock(self, container, var_str):
        """Used to unmock a function before the tests are ended."""
        self.mock(container, var_str, self.mock_origs[(container, var_str)])

    def mock_urlopen(self, on_check_exists_vals=None, on_get_tags_vals=None,
                     on_get_projects_vals=None, on_get_user_vals=None,
                     on_post_vals=None):
        """Remocks the urllib2 urlopen with the given response parameters.

        Each parameter is a tuple (read_val, status_code) if a response is
        expected. If an Exception is expected, the parameter is a tuple:
        (Exception(message), 'Exception') where the second tuple element is
        unused. If any of the parameters are None, the below default values
        will be used (in general, at most one of these parameters is supplied
        per test case).
        """
        default_on_check_exists_vals = ([], 200)
        default_on_get_tags_vals = ([{'id': 44, 'name': 'P4'},
                                    {'id': 0, 'name': 'P3'},
                                    {'id': 10, 'name': 'P2'},
                                    {'id': 100, 'name': 'P1'},
                                    {'id': 1, 'name': 'Evil tag'},
                                    {'id': 2, 'name': 'Evil tag'},
                                    {'id': 3, 'name': 'Evil tag'},
                                    {'id': 666, 'name': 'Auto generated'}],
                                    200)
        default_on_get_projects_vals = ([{'id': 0, 'name':
                                         'Engineering support'},
                                        {'id': 1, 'name': 'Evil project'},
                                        {'id': 2, 'name': 'Evil project'},
                                        {'id': 3, 'name': 'Evil project'}],
                                        200)
        default_on_get_user_vals = ([{'id': 0, 'email': 'alex@ka.org'}], 200)
        default_on_post_vals = ([], 200)

        on_check_exists_vals = (on_check_exists_vals or
                                default_on_check_exists_vals)
        on_get_tags_vals = on_get_tags_vals or default_on_get_tags_vals
        on_get_projects_vals = (on_get_projects_vals or
                                default_on_get_projects_vals)
        on_get_user_vals = default_on_get_user_vals or on_get_user_vals
        on_post_vals = on_post_vals or default_on_post_vals

        def new_mock_urlopen(request, data=None):
            request_url = request.get_full_url()
            if 'completed' in request_url:
                (mock_read_val, mock_status_code) = on_check_exists_vals
            elif '/api/1.0/tags?workspace=' in request_url:
                (mock_read_val, mock_status_code) = on_get_tags_vals
            elif '/api/1.0/projects?workspace=' in request_url:
                (mock_read_val, mock_status_code) = on_get_projects_vals
            elif '/api/1.0/users?workspace=' in request_url:
                (mock_read_val, mock_status_code) = on_get_user_vals
            elif data is not None:
                (mock_read_val, mock_status_code) = on_post_vals
                is_exception = isinstance(mock_read_val, Exception)
                if not is_exception and mock_status_code < 300:
                    self.sent_to_asana.append(json.loads(data))
            else:
                raise Exception('Invalid Asana API url')

            if six.PY3 and isinstance(data, six.text_type):
                raise TypeError(
                        'POST data should be bytes, an iterable of bytes, or '
                        'a file object. It cannot be of type str.')

            if isinstance(mock_read_val, Exception):
                raise mock_read_val
            return MockResponse(mock_read_val, mock_status_code)

        self.mock(six.moves.urllib.request, 'urlopen', new_mock_urlopen)


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
        message = u'\xf7'
        room_id = u'1s and \xf7s'
        alertlib.Alert(message).send_to_hipchat(room_id)
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': alertlib.base.handle_encoding(message),
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': alertlib.base.handle_encoding(room_id)}],
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
        zwsp = alertlib.base.handle_encoding(u'\u200b')
        alertlib.Alert('(commit 345d8)', summary='(345d8)').send_to_hipchat(
            'rm')
        self.assertEqual([{'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '(345d8{})'.format(zwsp),
                           'message_format': 'text',
                           'notify': 0,
                           'room_id': 'rm'},
                          {'auth_token': '<hipchat token>',
                           'color': 'purple',
                           'from': 'AlertiGator',
                           'message': '(commit 345d8{})'.format(zwsp),
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


class AsanaTest(TestBase):

    def test_tags_no_severity(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    def test_name_endswith_colon(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3']
        alert = alertlib.Alert('test message', summary='hi:')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    def test_severity_no_tags(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.WARNING)
        alert.send_to_asana(project=project_name)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

        self.sent_to_asana = []
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.ERROR)
        alert.send_to_asana(project=project_name)
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P2']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

        self.sent_to_asana = []
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.CRITICAL)
        alert.send_to_asana(project=project_name)
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P1']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    def test_severity_and_tags(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3', 'P1']
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.ERROR)
        alert.send_to_asana(project=project_name, tags=tag_names)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(alertlib.asana._CACHED_ASANA_TAG_MAP['P1'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    def test_duplicate_task(self):
        on_check_exists_vals = ([{'name': 'hi', 'completed': False}], 200)
        self.mock_urlopen(on_check_exists_vals=on_check_exists_vals)

        project_name = 'Engineering support'
        tag_names = ['P3']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)

    def test_overloaded_tags(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['Evil tag']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['P4'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        self.assertTrue(len(expected_tag_ids) > 1)

    def test_overloaded_project(self):
        self.mock_urlopen()

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['Evil tag']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['P4'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        self.assertTrue(len(expected_project_ids) > 1)

    def test_valid_follower(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3', 'P1']
        followers = ['alex@ka.org']
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.ERROR)
        alert.send_to_asana(project=project_name, tags=tag_names,
                            followers=followers)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(alertlib.asana._CACHED_ASANA_TAG_MAP['P1'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [0],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    def test_invalid_follower(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3', 'P1']
        followers = ['not_alex@ka.org']
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.ERROR)
        alert.send_to_asana(project=project_name, tags=tag_names,
                            followers=followers)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(alertlib.asana._CACHED_ASANA_TAG_MAP['P1'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        self.assertEqual([('Invalid asana user email: not_alex@ka.org; Fields '
                           'involving this user such as follower will not '
                           'added to task.',)], self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_invalid_project(self):
        self.mock_urlopen()

        project_name = 'Invalid project name'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        self.assertEqual([('Invalid asana project name; task not created.',)],
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_all_invalid_tags(self):
        self.mock_urlopen()

        project_name = 'Evil project'
        tag_names = ['llama', 'also llama']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = alertlib.asana._CACHED_ASANA_PROJECT_MAP[
            'Evil project']
        expected_tag_ids = []
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['P4'])
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Invalid asana tag name: llama; tag not added '
                               'to task.',),
                              ('Invalid asana tag name: also '
                               'llama; tag not added to task.',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_some_invalid_tags(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3', 'llama']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Invalid asana tag name: llama; tag not added'
                               ' to task.',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_no_summary(self):
        self.mock_urlopen()

        project_name = 'Engineering support'
        tag_names = ['P3']
        alert = alertlib.Alert('test message')
        alert.send_to_asana(project=project_name, tags=tag_names)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])

        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'test message',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)

    # won't fail, but will make duplicate task
    def test_urlopen_exception_duplicate_on_check_exists(self):
        on_check_exists_vals = (Exception('Test failure'), 'Exception')
        self.mock_urlopen(on_check_exists_vals=on_check_exists_vals)

        project_name = 'Engineering support'
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.WARNING)
        alert.send_to_asana(project=project_name)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana because of Test'
                               ' failure',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    # won't fail
    def test_urlopen_exception_no_duplicate_on_check_exists(self):
        on_check_exists_vals = (Exception('Test failure'), 'Exception')
        self.mock_urlopen(on_check_exists_vals=on_check_exists_vals)

        project_name = 'Engineering support'
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.WARNING)
        alert.send_to_asana(project=project_name)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana because of Test'
                               ' failure',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_exception_on_get_tags(self):
        on_get_tags_vals = (Exception('Failed gettings tags'), 'Exception')
        self.mock_urlopen(on_get_tags_vals=on_get_tags_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana because of Failed'
                               ' gettings tags',),
                              ('Failed to build Asana tags cache. Task will'
                               ' not be created',),
                              ('Failed to retrieve asana tag name to tag id'
                               ' mapping. Task will not be created.',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_exception_on_get_projects(self):
        on_get_projects_vals = (Exception('Failed getting projects'),
                                'Exception')
        self.mock_urlopen(on_get_projects_vals=on_get_projects_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana because of Failed'
                               ' getting projects',),
                              ('Invalid asana project name; task not '
                               'created.',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_exception_on_post(self):
        on_post_vals = (Exception('Test failure'), 'Exception')
        self.mock_urlopen(on_post_vals=on_post_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending {"data": {"followers": [], '
                               '"name": "hi", "notes": "test message", '
                               '"projects": [1, 2, 3], "tags": [1, 2, 3, 44,'
                               ' 666], "workspace": 1120786379245}} to asana '
                               'because of Test failure',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    # won't fail, but will make duplicate task
    def test_urlopen_bad_status_code_duplicate_on_check_exists(self):
        on_check_exists_vals = ([{'name': 'hi', 'completed': False}], 400)
        self.mock_urlopen(on_check_exists_vals=on_check_exists_vals)

        project_name = 'Engineering support'
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.WARNING)
        alert.send_to_asana(project=project_name)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana with code 400',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    # won't fail
    def test_urlopen_bad_status_code_no_duplicate_on_check_exists(self):
        on_check_exists_vals = ([], 400)
        self.mock_urlopen(on_check_exists_vals=on_check_exists_vals)

        project_name = 'Engineering support'
        alert = alertlib.Alert('test message', summary='hi',
                               severity=logging.WARNING)
        alert.send_to_asana(project=project_name)

        expected_project_ids = (
            alertlib.asana._CACHED_ASANA_PROJECT_MAP[project_name])
        expected_tag_ids = alertlib.asana._CACHED_ASANA_TAG_MAP['P3']
        expected_tag_ids.extend(
            alertlib.asana._CACHED_ASANA_TAG_MAP['Auto generated'])
        self.assertEqual([{'data':
                          {'followers': [],
                           'name': 'hi',
                           'notes': 'test message',
                           'projects': expected_project_ids,
                           'tags': expected_tag_ids,
                           'workspace': 1120786379245}
                           }
                          ],
                         self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana with code 400',)]
        self.assertEqual(expected_error_log, self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_bad_status_code_on_get_tags(self):
        on_get_tags_vals = ([{'id': 44, 'name': 'P4'},
                            {'id': 0, 'name': 'P3'},
                            {'id': 10, 'name': 'P2'},
                            {'id': 100, 'name': 'P1'},
                            {'id': 1, 'name': 'Evil tag'},
                            {'id': 2, 'name': 'Evil tag'},
                            {'id': 3, 'name': 'Evil tag'},
                            {'id': 666, 'name': 'Auto generated'}],
                            400)
        self.mock_urlopen(on_get_tags_vals=on_get_tags_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana with code 400',),
                              ('Failed to build Asana tags cache. Task will'
                               ' not be created',),
                              ('Failed to retrieve asana tag name to tag id'
                               ' mapping. Task will not be created.',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_bad_status_code_on_get_projects(self):
        on_get_projects_vals = ([{'id': 0, 'name':
                                  'Engineering support'},
                                 {'id': 1, 'name': 'Evil project'},
                                 {'id': 2, 'name': 'Evil project'},
                                 {'id': 3, 'name': 'Evil project'}],
                                400)
        self.mock_urlopen(on_get_projects_vals=on_get_projects_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending None to asana with code 400',),
                              ('Invalid asana project name; task not '
                               'created.',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def test_urlopen_bad_status_code_on_post(self):
        on_post_vals = ([], 400)
        self.mock_urlopen(on_post_vals=on_post_vals)

        project_name = 'Evil project'
        tag_names = ['Evil tag']
        alert = alertlib.Alert('test message', summary='hi')
        alert.send_to_asana(project=project_name, tags=tag_names)
        self.assertEqual([], self.sent_to_asana)
        expected_error_log = [('Failed sending {"data": {"followers": [], '
                               '"name": "hi", "notes": "test message", '
                               '"projects": [1, 2, 3], "tags": [1, 2, 3, 44, '
                               '666], "workspace": 1120786379245}} to asana '
                               'with code 400',)]

        self.assertEqual(expected_error_log,
                         self.sent_to_error_log)
        self.sent_to_error_log = []


class SlackTest(TestBase):
    def test_default_options(self):
        alertlib.Alert('test message').send_to_slack('#bot-testing')
        actual = json.loads(self.sent_to_slack[0])
        self.assertEqual(actual['channel'], '#bot-testing')
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
        self.assertEqual(actual['attachments'][0]['fallback'], 'ABC\nxyz')

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
        self.assertEqual(actual.get('text'), '\n')
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

    def test_utf8_python2(self):
        if sys.version_info >= (3, 0):
            return

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

    def test_utf8_python3(self):
        '''In Python 3 we should actually send the email in UTF-8'''
        if sys.version_info < (3, 0):
            return

        with disable_google_mail():
            alertlib.Alert(u'yo \xf7', summary=u'yep \xf7') \
                .send_to_pagerduty('oncall') \
                .send_to_email('ka-admin')

        self.assertEqual([('no-reply@khanacademy.org',
                           ['oncall@khan-academy.pagerduty.com'],
                           'Content-Type: text/plain; charset="utf-8"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: base64\n'
                           'Subject: =?utf-8?b?eWVwIMO3?=\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: oncall@khan-academy.pagerduty.com\n\n'
                           'eW8gw7cK\n'
                           ),
                          ('no-reply@khanacademy.org',
                           ['ka-admin@khanacademy.org'],
                           'Content-Type: text/plain; charset="utf-8"\n'
                           'MIME-Version: 1.0\n'
                           'Content-Transfer-Encoding: base64\n'
                           'Subject: =?utf-8?b?eWVwIMO3?=\n'
                           'From: alertlib <no-reply@khanacademy.org>\n'
                           'To: ka-admin@khanacademy.org\n\n'
                           'eW8gw7cK\n'
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


class StackdriverTest(TestBase):
    def setUp(self):
        super(StackdriverTest, self).setUp()
        self.alert = alertlib.Alert('test message')

    def test_value(self):
        self.alert.send_to_stackdriver('stats.test_message', 4)

        sent_data_point = self._get_sent_datapoint()
        self.assertEqual({'doubleValue': 4}, sent_data_point['value'])

    def test_default_value(self):
        self.alert.send_to_stackdriver('stats.test_message')

        sent_data_point = self._get_sent_datapoint()
        self.assertEqual({'doubleValue': 1}, sent_data_point['value'])

    def test_metric_name(self):
        self.alert.send_to_stackdriver('stats.Bad-M#tric%name')
        sent_data = self._get_sent_timeseries_data()
        sent_metric_name = sent_data['metric']['type']
        self.assertEqual('custom.googleapis.com/stats.Bad_M_tric_name',
                         sent_metric_name)

    def test_default_labels(self):
        self.alert.send_to_stackdriver('stats.test_message')
        sent_data = self._get_sent_timeseries_data()
        sent_metric = sent_data['metric']
        self.assertEqual({'type': 'custom.googleapis.com/stats.test_message'},
                         sent_metric)
        self.assertNotIn('resource', sent_data)

    def test_explicit_labels(self):
        self.alert.send_to_stackdriver('stats.test_message',
                                       metric_labels={'foo': 'bar', 'baz': 4})
        sent_data = self._get_sent_timeseries_data()
        sent_metric = sent_data['metric']
        expected = {
            'type': 'custom.googleapis.com/stats.test_message',
            'labels': {'foo': 'bar', 'baz': 4}
        }
        self.assertEqual(sent_metric, expected)
        self.assertNotIn('resource', sent_data)

    def test_monitored_resource_type(self):
        self.alert.send_to_stackdriver(
            'stats.test_message',
            monitored_resource_type='datastore_request',
            monitored_resource_labels={'module_id': 'i18n', 'version_id': 4})
        sent_data = self._get_sent_timeseries_data()
        sent_metric = sent_data['resource']
        expected = {
            'type': 'datastore_request',
            'labels': {'module_id': 'i18n', 'version_id': 4}
        }
        self.assertEqual(sent_metric, expected)

    def test_when(self):
        self.alert.send_to_stackdriver(
            'stats.test_message',
            when=1467411024)
        sent_data_point = self._get_sent_datapoint()
        self.assertEqual({'startTime': '2016-07-01T22:10:24Z',
                          'endTime': '2016-07-01T22:10:24Z'},
                         sent_data_point['interval'])

    def test_ignore_errors(self):
        self.unmock(alertlib.stackdriver, 'send_datapoints_to_stackdriver')
        self.mock(alertlib.stackdriver, '_get_google_apiclient', mock.Mock())
        self.mock(alertlib.stackdriver, '_call_stackdriver_with_retries',
                  lambda *a, **kw: 1 / 0)
        self.alert.send_to_stackdriver('stats.test_message', 4)

        self.assertEqual([], self.sent_to_stackdriver)

    def test_do_not_ignore_errors(self):
        self.unmock(alertlib.stackdriver, 'send_datapoints_to_stackdriver')
        self.mock(alertlib.stackdriver, '_get_google_apiclient', mock.Mock())
        self.mock(alertlib.stackdriver, '_call_stackdriver_with_retries',
                  lambda *a, **kw: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            self.alert.send_to_stackdriver('stats.test_message', 4,
                                           ignore_errors=False)
        self.assertEqual([('cloud-monitoring error, not sending some data',)],
                         self.sent_to_error_log)
        self.sent_to_error_log = []

    def _get_sent_timeseries_data(self):
        self.assertEqual(1, len(self.sent_to_stackdriver))
        sent_data = self.sent_to_stackdriver[0]

        # Ensure the timeseries has the keys we expect
        expected_keys = {'metric', 'points'}
        self.assertTrue(expected_keys.issubset(set(sent_data)))

        return sent_data

    def _get_sent_datapoint(self):
        sent_points = self._get_sent_timeseries_data()['points']
        self.assertEqual(1, len(sent_points))

        return sent_points[0]


class CallWithRetriesTest(TestBase):

    class MockHttpResponse:
        def __init__(self, status_code, reason=""):
            self.status_code = status_code
            self.reason = reason

        def __getitem__(self, value):
            if value == 'status':
                return self.status_code

        def status(self):
            return self.status_code

        def _get_reason(self):
            return self.reason

    def test_expected_errors(self):
        error_types = [socket.error, six.moves.http_client.HTTPException,
                       oauth2client.client.Error]

        for error_type in error_types:
            test_func = mock.Mock(side_effect=error_type('error'))

            # On the N+1 try, the function re-raises
            with self.assertRaises(error_type):
                alertlib.stackdriver._call_stackdriver_with_retries(
                    test_func, wait_time=0)

            self.assertEqual(test_func.call_count, 10)

    def test_unexpected_error(self):
        test_func = mock.Mock(side_effect=RuntimeError('error'))

        with self.assertRaises(RuntimeError):
            alertlib.stackdriver._call_stackdriver_with_retries(
                test_func, wait_time=0)

        # We should not retry when unexpected errors are raised
        self.assertEqual(test_func.call_count, 1)

    def test_expected_apiclient_errors(self):
        expected_error_status_codes = [403, 503]

        for code in expected_error_status_codes:
            test_func = self._http_error_fn(code)

            with self.assertRaises(apiclient.errors.HttpError):
                alertlib.stackdriver._call_stackdriver_with_retries(
                    test_func, wait_time=0)

            self.assertEqual(test_func.call_count, 10)

    def test_unexpected_apiclient_errors(self):
        test_func = self._http_error_fn(401)

        with self.assertRaises(apiclient.errors.HttpError):
            alertlib.stackdriver._call_stackdriver_with_retries(
                test_func, wait_time=0)

        # We should not retry when unexpected errors are raised
        self.assertEqual(test_func.call_count, 1)

    def test_expected_400_timeseries_response(self):
        error_msg = 'Timeseries data must be more recent'
        test_func = self._http_error_fn(400, error_msg)

        # We do not expect an error to be raised here
        alertlib.stackdriver._call_stackdriver_with_retries(
            test_func, wait_time=0)
        self.assertEqual(test_func.call_count, 1)

    def test_expected_500_timeseries_response(self):
        error_msg = ('One or more of the points specified was older than '
                     'the most recent stored point.')
        test_func = self._http_error_fn(500, error_msg)

        # We do not expect an error to be raised here
        alertlib.stackdriver._call_stackdriver_with_retries(
            test_func, wait_time=0)
        self.assertEqual(test_func.call_count, 1)

    def test_unexpected_400_response(self):
        # If we have any other reason, we raise and do not retry
        test_func = self._http_error_fn(400, 'Some other reason')

        with self.assertRaises(apiclient.errors.HttpError):
            alertlib.stackdriver._call_stackdriver_with_retries(
                test_func, wait_time=0)

        self.assertEqual(test_func.call_count, 1)

    def test_non_default_num_retries(self):
        test_func = mock.Mock(side_effect=socket.error('error'))

        with self.assertRaises(socket.error):
            alertlib.stackdriver._call_stackdriver_with_retries(
                test_func, num_retries=20, wait_time=0)

        self.assertEqual(test_func.call_count, 21)

    def test_eventual_success(self):
        side_effects = [socket.error('error'), socket.error('error'), 0]
        test_func = mock.Mock(side_effect=side_effects)

        alertlib.stackdriver._call_stackdriver_with_retries(
            test_func, wait_time=0)
        self.assertEqual(test_func.call_count, 3)

    def _http_error_fn(self, status_code, reason=""):
        response = CallWithRetriesTest.MockHttpResponse(status_code, reason)
        error = apiclient.errors.HttpError(response, b"expected error")

        return mock.Mock(side_effect=error)


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
        for _ in range(100):
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(100, len(self.sent_to_graphite))

    def test_burst(self):
        alert = alertlib.Alert('test message', rate_limit=60)
        for _ in range(100):
            alert.send_to_graphite('stats.test_message', 4)
        self.assertEqual(1, len(self.sent_to_graphite))

    def test_different_alert_objects(self):
        # Objects don't share state, so we won't rate limit here.
        for _ in range(100):
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
        for _ in range(100):
            alert.send_to_graphite('stats.test_message', 4) \
                 .send_to_hipchat('1s and 0s') \
                 .send_to_stackdriver('stats.test_message')
            alert.send_to_logs()
        self.assertEqual(1, len(self.sent_to_graphite))
        self.assertEqual(1, len(self.sent_to_hipchat))
        self.assertEqual(1, len(self.sent_to_syslog))
        self.assertEqual(1, len(self.sent_to_stackdriver))


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

        self.assertEqual([], self.sent_to_stackdriver)

    def test_test_mode(self):
        alertlib.enter_test_mode()
        try:
            alertlib.Alert('test message') \
                .send_to_hipchat('1s and 0s') \
                .send_to_email('ka-admin') \
                .send_to_pagerduty('oncall') \
                .send_to_logs() \
                .send_to_graphite('stats.alerted') \
                .send_to_stackdriver('stats.test_mode')
        finally:
            alertlib.exit_test_mode()

        # Should only log, not send to anything
        self.assertEqual([], self.sent_to_hipchat)
        self.assertEqual([], self.sent_to_google_mail)
        self.assertEqual([], self.sent_to_sendmail)
        self.assertEqual([], self.sent_to_syslog)
        self.assertEqual([], self.sent_to_graphite)
        self.assertEqual([], self.sent_to_stackdriver)

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
             ('alertlib: would send to graphite: stats.alerted 1',),
             ("alertlib: would send to stackdriver: "
              "metric_name: stats.test_mode, value: 1",)
             ],
            self.sent_to_info_log)

    def test_rate_limiting(self):
        """Make sure we put rate-limiting properly on each service."""
        alert = alertlib.Alert('test message', rate_limit=60)
        for _ in range(10):
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
        old_smtplib = alertlib.email.smtplib
        old_syslog = alertlib.logs.syslog
        try:
            del alertlib.email.smtplib
            del alertlib.logs.syslog

            # Just make sure nothing crashes
            alertlib.Alert('test message') \
                .send_to_hipchat('1s and 0s') \
                .send_to_email('ka-admin') \
                .send_to_pagerduty('oncall') \
                .send_to_logs() \
                .send_to_graphite('stats.alerted')
        finally:
            alertlib.email.smtplib = old_smtplib
            alertlib.logs.syslog = old_syslog


if __name__ == '__main__':
    unittest.main()
