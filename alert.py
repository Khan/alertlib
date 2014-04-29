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


class _MakeList(argparse.Action):
    """Parse the argument as a comma-separated list."""
    def __call__(self, parser, namespace, value, option_string=None):
        if not value:
            return []
        setattr(namespace, self.dest, [x.strip() for x in value.split(',')])


class _ParseSeverity(argparse.Action):
    """Parse the argument as a logging.<severity>."""
    def __call__(self, parser, namespace, value, option_string=None):
        setattr(namespace, self.dest, getattr(logging, value.upper()))


def setup_parser():
    """Create an ArgumentParser for alerting."""
    parser = argparse.ArgumentParser(
        description=('Send a message to one or more alerting services. '
                     'The message is read from stdin.'))
    parser.add_argument('--hipchat', default=[], action=_MakeList,
                        help=('Send to hipchat.  Argument is a comma-'
                              'separated list of room names. '
                              'May specify --severity and/or --summary. '
                              'May specify --color and/or --notify '
                              '(if omitted we determine automatically).'))
    parser.add_argument('--mail', default=[], action=_MakeList,
                        help=('Send to KA email.  Argument is a comma-'
                              'separated list of usernames. '
                              'May specify --summary as a subject line; '
                              'if missing we figure it out automatically. '
                              'May specify --cc and/or --bcc.'))
    parser.add_argument('--pagerduty', default=[], action=_MakeList,
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
    parser.add_argument('--severity', default=logging.INFO,
                        choices=['debug', 'info', 'warning', 'error',
                                 'critical'],
                        action=_ParseSeverity,
                        help=('Severity of the message, which may affect '
                              'how we alert (default: INFO)'))
    parser.add_argument('--color', default=None,
                        choices=['yellow', 'red', 'green', 'purple',
                                 'gray', 'random'],
                        help=('Background color when sending to hipchat'))
    parser.add_argument('--notify', action='store_true', default=None,
                        help=('Cause a beep when sending to hipchat'))
    parser.add_argument('--cc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--bcc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    return parser


def alert(message, args):
    a = alertlib.Alert(message, args.summary, args.severity, html=False)

    for room in args.hipchat:
        a.send_to_hipchat(room, args.color, args.notify)

    if args.mail:
        a.send_to_email(args.mail, args.cc, args.bcc)

    for service in args.pagerduty:
        a.send_to_pagerduty(service)

    if args.logs:
        a.send_to_logs()


def main():
    parser = setup_parser()
    args = parser.parse_args()

    if sys.stdin.isatty():
        print >>sys.stderr, '>> Enter the message to alert, then hit control-D'
    message = sys.stdin.read().strip()

    alert(message, args)


if __name__ == '__main__':
    main()
