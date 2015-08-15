#!/usr/bin/env python

"""A front-end for the alertlib library.

This is just a simple frontend to allow using alertlib from the
commandline.  You should prefer to use the library directly for uses
within Python.
"""

import argparse
import logging
import sys

import alertlib


DEFAULT_SEVERITY = logging.INFO


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
                        help=('Send to HipChat.  Argument is a comma-'
                              'separated list of room names. '
                              'May specify --severity and/or --summary '
                              'and/or --chat-sender. '
                              'May specify --color and/or --notify '
                              '(if omitted we determine automatically).'))
    parser.add_argument('--slack', default=[], action=_MakeList,
                        help=('Send to Slack.  Argument is a comma-'
                              'separated list of channel names. '
                              'May specify --severity and/or --summary '
                              'and/or --chat-sender. '))
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
    parser.add_argument('--graphite', default=[], action=_MakeList,
                        help=('Send to graphite.  Argument is a comma-'
                              'separted list of statistics to update. '
                              'May specify --graphite_val and '
                              '--graphite_host.'))

    parser.add_argument('--summary', default=None,
                        help=('Summary used as subject lines for emails, etc. '
                              'If omitted, we figure it out automatically '
                              'from the alert message.  To suppress entirely, '
                              'pass --summary=""'))
    parser.add_argument('--severity', default=DEFAULT_SEVERITY,
                        choices=['debug', 'info', 'warning', 'error',
                                 'critical'],
                        action=_ParseSeverity,
                        help=('Severity of the message, which may affect '
                              'how we alert (default: %(default)s)'))
    parser.add_argument('--html', action='store_true', default=False,
                        help=('Indicate the input should be treated as html'))
    parser.add_argument('--chat-sender', default='AlertiGator',
                        help=('Who we say sent this chat message.'))
    parser.add_argument('--color', default=None,
                        choices=['yellow', 'red', 'green', 'purple',
                                 'gray', 'random'],
                        help=('Background color when sending to hipchat '
                              '(default depends on severity)'))
    parser.add_argument('--notify', action='store_true', default=None,
                        help=('Cause a beep when sending to hipchat'))
    parser.add_argument('--cc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--sender-suffix', default=None,
                        help=('This adds "foo" to the sender address, which '
                              'is alertlib <no-reply+foo@khanacademy.org>.'))
    parser.add_argument('--bcc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--graphite_value', default=1, type=float,
                        help=('Value to send to graphite for each of the '
                              'graphite statistics specified '
                              '(default %(default)s)'))
    parser.add_argument('--graphite_host',
                        default=alertlib.Alert.DEFAULT_GRAPHITE_HOST,
                        help=('host:port to send graphite data to '
                              '(default %(default)s)'))

    parser.add_argument('-n', '--dry-run', action='store_true',
                        help=("Just log what we would do, but don't do it"))

    return parser


def alert(message, args):
    a = alertlib.Alert(message, args.summary, args.severity, html=args.html)

    for room in args.hipchat:
        a.send_to_hipchat(room, args.color, args.notify, args.chat_sender)

    for channel in args.slack:
        a.send_to_slack(channel, sender=args.chat_sender)

    if args.mail:
        a.send_to_email(args.mail, args.cc, args.bcc, args.sender_suffix)

    if args.pagerduty:
        a.send_to_pagerduty(args.pagerduty)

    if args.logs:
        a.send_to_logs()

    for statistic in args.graphite:
        a.send_to_graphite(statistic, args.graphite_value,
                           args.graphite_host)


def main(argv):
    parser = setup_parser()
    args = parser.parse_args(argv)

    if sys.stdin.isatty():
        print >>sys.stderr, '>> Enter the message to alert, then hit control-D'
    message = sys.stdin.read().strip()

    if args.dry_run:
        alertlib.enter_test_mode()
        logging.getLogger().setLevel(logging.INFO)

    alert(message, args)


if __name__ == '__main__':
    main(sys.argv[1:])
