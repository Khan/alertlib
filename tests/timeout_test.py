#!/usr/bin/env python

"""Tests for timeout.py and (most of) alert.py.

timeout.py uses most of alert.py's logic for stuff like commandline
flags and sending alerts, so by testing timeout.py I'm testing most of
alert.py as well.
"""

import importlib
import logging
import os
import subprocess
import sys
import unittest

# This makes it so we can find timeout when running from repo-root.
sys.path.insert(1, '.')

import alertlib
import timeout

import alertlib_test

for module in alertlib_test.ALERTLIB_MODULES:
    importlib.import_module('alertlib.%s' % module)


# TODO(benkraft): These tests print a bunch of ResourceWarnings, I think
# because we don't wait on the processes after killing them, so we leave them
# as zombies.  Fix.
class TestTimeout(unittest.TestCase):
    def test_times_out(self):
        # TODO(csilvers): mock out the clock in some way for this?
        rc = timeout.main('1 sleep 2'.split())
        self.assertEqual(124, rc)

    def test_does_not_time_out(self):
        rc = timeout.main('1 true'.split())
        self.assertEqual(0, rc)

    def test_forwards_return_value(self):
        rc = timeout.main('1 grep asfa /notafile'.split())
        self.assertEqual(2, rc)

    def test_kills_subprocesses(self):
        ps_output = subprocess.check_output(['ps', 'x'])
        num_sleeps_before = ps_output.count(b'sleep 200')

        rc = timeout.main(['1', 'sh', '-c', 'sleep 200'])
        self.assertEqual(124, rc)

        # This isn't a perfect test, but will probably work.
        ps_output = subprocess.check_output(['ps', 'x'])
        num_sleeps_after = ps_output.count(b'sleep 200')
        self.assertEqual(num_sleeps_before, num_sleeps_after)

    def test_kill_after(self):
        # If a nohup.out file already exists in this directory, bail
        # so we don't overwrite it.
        if os.path.exists('nohup.out'):
            return

        ps_output = subprocess.check_output(['ps', 'x'])
        num_sleeps_before = ps_output.count(b'sleep 200')

        # We'll send a HUP (signal 1) to a process that ignores it,
        # and then send a real kill sometime after.
        # This crates a nohup.out file, which we clean up.
        rc = timeout.main(['-s1', '-k1', '1', 'nohup', 'sleep', '200'])
        os.unlink('nohup.out')
        self.assertEqual(124, rc)

        ps_output = subprocess.check_output(['ps', 'x'])
        num_sleeps_after = ps_output.count(b'sleep 200')
        self.assertEqual(num_sleeps_before, num_sleeps_after)

    def test_zero_timeout_always_fails(self):
        rc = timeout.main('0 true'.split())
        self.assertEqual(124, rc)

    def test_cwd(self):
        rc = timeout.main('--cwd=/etc 10 grep -q . passwd'.split())
        self.assertEqual(0, rc)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        # We run timeout.py with -n, which causes alertlib to log what
        # it would do without doing it.  Make sure we can see those
        # logs.
        self.sent_to_info_log = []

        for module in alertlib_test.ALERTLIB_MODULES:
            alertlib_module = getattr(alertlib, module)
            logging_module = getattr(alertlib_module, 'logging')
            self.mock(logging_module, 'info',
                      lambda *args: self.sent_to_info_log.append(args))
            self.mock(logging_module, 'log',
                      lambda severity, message: (
                          self.sent_to_info_log.append((message,))
                          if severity == logging.INFO else None))

        self.maxDiff = None

    def mock(self, container, var_str, new_value):
        old_value = getattr(container, var_str)
        self.addCleanup(lambda: setattr(container, var_str, old_value))
        setattr(container, var_str, new_value)

    def test_alerts_on_timeout(self):
        timeout.main('-n --hipchat=testroom --mail=tim '
                     '--pagerduty=time! --logs --graphite=stats.alert '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to hipchat room testroom: '
              'TIMEOUT running true',),
             ("alertlib: would send email to "
              "['tim@khanacademy.org'] "
              "(from alertlib <no-reply@khanacademy.org> CC [] BCC []): "
              "(subject ERROR: TIMEOUT running true) TIMEOUT running true",),
             ("alertlib: would send pagerduty email to "
              "['time@khan-academy.pagerduty.com'] "
              "(subject ERROR: TIMEOUT running true) TIMEOUT running true",),
             ('alertlib: would send to graphite: stats.alert 1',)
             ],
            self.sent_to_info_log)

    def test_alerts_with_severity(self):
        timeout.main('-n --severity=info --hipchat=testroom --mail=tim '
                     '--pagerduty=time! --logs --graphite=stats.alert '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to hipchat room testroom: '
              'TIMEOUT running true',),
             ("alertlib: would send email to "
              "['tim@khanacademy.org'] "
              "(from alertlib <no-reply@khanacademy.org> CC [] BCC []): "
              "(subject TIMEOUT running true) TIMEOUT running true",),
             ("alertlib: would send pagerduty email to "
              "['time@khan-academy.pagerduty.com'] "
              "(subject TIMEOUT running true) TIMEOUT running true",),
             ('TIMEOUT running true',),
             ('alertlib: would send to graphite: stats.alert 1',)
             ],
            self.sent_to_info_log)

    def test_multiple_arguments(self):
        timeout.main('-n --hipchat=testroom,room2 --mail=tim,tam '
                     '--pagerduty=time!,flies! --logs '
                     '--graphite=stats.alert,stats.bad,stats.reallybad '
                     '--cc you,would-like --bcc to,know '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to hipchat room testroom: '
              'TIMEOUT running true',),
             ('alertlib: would send to hipchat room room2: '
              'TIMEOUT running true',),
             ("alertlib: would send email to "
              "['tim@khanacademy.org', 'tam@khanacademy.org'] "
              "(from alertlib <no-reply@khanacademy.org> "
              "CC ['you@khanacademy.org', 'would-like@khanacademy.org'] "
              "BCC ['to@khanacademy.org', 'know@khanacademy.org']): "
              "(subject ERROR: TIMEOUT running true) TIMEOUT running true",),
             ("alertlib: would send pagerduty email to "
              "['time@khan-academy.pagerduty.com', "
              "'flies@khan-academy.pagerduty.com'] "
              "(subject ERROR: TIMEOUT running true) TIMEOUT running true",),
             ('alertlib: would send to graphite: stats.alert 1',),
             ('alertlib: would send to graphite: stats.bad 1',),
             ('alertlib: would send to graphite: stats.reallybad 1',),
             ],
            self.sent_to_info_log)

    def test_sender_suffix(self):
        timeout.main('-n --summary timeout-test --sender-suffix=filter '
                     '--mail=tim --pagerduty=time! '
                     '0 true'.split())
        self.assertEqual(
            [("alertlib: would send email to ['tim@khanacademy.org'] "
              "(from alertlib <no-reply+filter@khanacademy.org> "
              "CC [] BCC []): "
              "(subject timeout-test) TIMEOUT running true",),
             ("alertlib: would send pagerduty email to "
              "['time@khan-academy.pagerduty.com'] "
              "(subject timeout-test) TIMEOUT running true",),
             ],
            self.sent_to_info_log)

    def test_summary(self):
        timeout.main('-n --summary timeout-test --hipchat=testroom --mail=tim '
                     '--pagerduty=time! --logs --graphite=stats.alert '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to hipchat room testroom: '
              'timeout-test',),
             ('alertlib: would send to hipchat room testroom: '
              'TIMEOUT running true',),
             ("alertlib: would send email to "
              "['tim@khanacademy.org'] "
              "(from alertlib <no-reply@khanacademy.org> CC [] BCC []): "
              "(subject timeout-test) TIMEOUT running true",),
             ("alertlib: would send pagerduty email to "
              "['time@khan-academy.pagerduty.com'] "
              "(subject timeout-test) TIMEOUT running true",),
             ('alertlib: would send to graphite: stats.alert 1',)
             ],
            self.sent_to_info_log)

    def test_graphite_value(self):
        timeout.main('-n --graphite_value=12.4 '
                     '--graphite=stats.alert,stats.bad '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to graphite: stats.alert 12.4',),
             ('alertlib: would send to graphite: stats.bad 12.4',)
             ],
            self.sent_to_info_log)

        self.sent_to_info_log = []
        timeout.main('-n --graphite_value=12 '
                     '--graphite=stats.alert,stats.bad '
                     '0 true'.split())
        self.assertEqual(
            [('alertlib: would send to graphite: stats.alert 12',),
             ('alertlib: would send to graphite: stats.bad 12',)
             ],
            self.sent_to_info_log)


if __name__ == '__main__':
    unittest.main()
