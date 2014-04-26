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
                              'May specify --color and/or --notify.'))
    parser.add_argument('--mail',
                        help=('Send to KA email.  Argument is a comma-'
                              'separated list of usernames. '
                              'Must specify --subject. '
                              'May specify --cc and/or --bcc.'))
    parser.add_argument('--pagerduty',
                        help=('Send to PagerDuty.  Argument is a comma-'
                              'separated list of PagerDuty services. '
                              'May specify --subject as a brief summary.'))
    parser.add_argument('--logs',
                        choice=['debug', 'info', 'warning', 'error',
                                'crictical'],
                        help=('Send to syslog.  Argument is severity.'))
    parser.add_argument('--color', default='purple',
                        choice=['yellow', 'red', 'green', 'purple',
                                'gray', 'random'],
                        help=('Background color when sending to hipchat'))
    parser.add_argument('--notify', action='store_true',
                        help=('Cause a beep when sending to hipchat'))
    parser.add_argument('--subject',
                        help=('Subject for email; summary for PagerDuty'))
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

    if args.mail:
        assert args.subject, 'Must specify --subject with --email'

    message = sys.stdin.read().strip()

    a = alertlib.Alert(message)

    for room in hipchat:
        a.send_to_hipchat(room, args.color, args.notify)

    if email:
        a.send_to_email(email, args.subject, cc, bcc)

    for service in pagerduty:
        a.send_to_pagerduty(service, args.subject)

    if args.logs:
        # Map the value to the corresponding loglevel
        priority = getattr(logging, args.logs.upper(), 0)
        a.send_to_logs(priority)
