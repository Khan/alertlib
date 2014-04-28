#!/usr/bin/env python

"""A front-end for the alertlib library.

This is just a simple frontend to allow using alertlib from the
commandline.  You should prefer to use the library directly for uses
within Python.
"""

import argparse
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alertlib


def _make_list(s):
    if not s:
        return []
    return [x.strip() for x in s.split(',')]


def main():
    parser = argparse.ArgumentParser(
        description=('Send a message to one or more alerting services. '
                     'The message is read from stdin.'))
    parser.add_argument('--hipchat',
                        help=('Send to hipchat.  Argument is a comma-'
                              'separated list of room names. '
                              'May specify --severity and/or --summary. '
                              'May specify --color and/or --notify '
                              '(if omitted we determine automatically).'))
    parser.add_argument('--mail',
                        help=('Send to KA email.  Argument is a comma-'
                              'separated list of usernames. '
                              'May specify --summary as a subject line; '
                              'if missing we figure it out automatically. '
                              'May specify --cc and/or --bcc.'))
    parser.add_argument('--pagerduty',
                        help=('Send to PagerDuty.  Argument is a comma-'
                              'separated list of PagerDuty services. '
                              'May specify --summary as a brief summary; '
                              'if missing we figure it out automatically. '))
    parser.add_argument('--logs', action='store_true',
                        help=('Send to syslog.  May specify --severity.'))

    parser.add_argument('--summary', default=None,
                        help=('Summary used as subject lines for emails, etc. '
                              'If omitted, we figure it out automatically '
                              'from the alert message.  To suppress entirely, '
                              'pass --summary=""'))
    parser.add_argument('--severity', default='info',
                        choices=['debug', 'info', 'warning', 'error',
                                 'critical'],
                        help=('Severity of the message, which may affect '
                              'how we alert (default: %(default)s)'))
    parser.add_argument('--color', default=None,
                        choices=['yellow', 'red', 'green', 'purple',
                                 'gray', 'random'],
                        help=('Background color when sending to hipchat'))
    parser.add_argument('--notify', action='store_true', default=None,
                        help=('Cause a beep when sending to hipchat'))
    parser.add_argument('--cc', default='',
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--bcc', default='',
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    args = parser.parse_args()

    hipchat = _make_list(args.hipchat)
    email = _make_list(args.mail)
    pagerduty = _make_list(args.pagerduty)
    cc = _make_list(args.cc)
    bcc = _make_list(args.cc)

    severity = getattr(logging, args.severity.upper())   # INFO, etc.

    if sys.stdin.isatty():
        print >>sys.stderr, '>> Enter the message to alert, then hit control-D'
    message = sys.stdin.read().strip()

    a = alertlib.Alert(message, args.summary, severity, html=False)

    for room in hipchat:
        a.send_to_hipchat(room, args.color, args.notify)

    if email:
        a.send_to_email(email, cc, bcc)

    for service in pagerduty:
        a.send_to_pagerduty(service)

    if args.logs:
        a.send_to_logs()


if __name__ == '__main__':
    main()
