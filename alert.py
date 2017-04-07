#!/usr/bin/env python

"""A front-end for the alertlib library.

This is just a simple frontend to allow using alertlib from the
commandline.  You should prefer to use the library directly for uses
within Python.
"""

from __future__ import print_function
import argparse
import json
import logging
import sys

import alertlib
import alertlib.graphite
import alertlib.stackdriver


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
                              'May specify --severity and/or --summary. '
                              'May specify --chat-sender and/or '
                              '--icon-emoji or --icon-url '
                              '(if omitted Slack determines automatically).'
                              'May specify --slack-simple-mesage or '
                              '--slack-attachment.'))
    parser.add_argument('--mail', default=[], action=_MakeList,
                        help=('Send to KA email.  Argument is a comma-'
                              'separated list of usernames. '
                              'May specify --summary as a subject line; '
                              'if missing we figure it out automatically. '
                              'May specify --cc and/or --bcc.'))
    parser.add_argument('--asana', default=[], action=_MakeList,
                        help=('Make an asana task.  Argument is a comma-'
                              'separated list of asana project-names '
                              '(e.g. "Engineering support".) '
                              'May specify --asana-tags; '
                              'may specify --cc as a list of asana email '
                              'addresses to add followers.'))
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
                              'May specify --graphite_value and '
                              '--graphite_host.'))

    parser.add_argument('--stackdriver', default=[], action=_MakeList,
                        help=('Send to Stackdriver.  Argument is a comma-'
                              'separted list of metrics to update, with '
                              'metric-label-values separated by pipes like so:'
                              ' `logs.500|module=i18n`. '
                              'May specify --stackdriver_value'))

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

    parser.add_argument('--chat-sender', default=None,
                        help=('Who we say sent this chat message. '
                              'In HipChat, defaults to AlertiGator. '
                              "In Slack, defaults to whatever the webhook's "
                              'default is, likely also AlertiGator.'))
    parser.add_argument('--icon-emoji', default=None,
                        help=('The emoji sender to use for this message. '
                              'Slack only.  Defaults to whatever the '
                              "webhook's default is, likely :crocodile:"))
    parser.add_argument('--icon-url', default=None,
                        help=('The icon URL to use for this message. '
                              'Slack only.  Overridden by --icon-emoji.'))
    parser.add_argument('--color', default=None,
                        choices=['yellow', 'red', 'green', 'purple',
                                 'gray', 'random'],
                        help=('Background color when sending to hipchat '
                              '(default depends on severity)'))
    parser.add_argument('--notify', action='store_true', default=None,
                        help=('Cause a beep when sending to hipchat'))
    parser.add_argument('--slack-intro', default='',
                        help=('If specified, text to put before the main '
                              'text.  You can use @-alerts in the intro.'))
    parser.add_argument('--slack-simple-message', action='store_true',
                        default=False,
                        help=('Pass message to slack using normal Markdown, '
                              'rather than rendering it "attachment" style.'))
    parser.add_argument('--slack-attachments', default='[]',
                        help=('A list of slack attachment dicts, encoded as '
                              'json. Replaces `message` for sending to slack. '
                              '(See https://api.slack.com/docs/attachments.)'))

    parser.add_argument('--cc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--sender-suffix', default=None,
                        help=('This adds "foo" to the sender address, which '
                              'is alertlib <no-reply+foo@khanacademy.org>.'))
    parser.add_argument('--bcc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))

    parser.add_argument('--asana-tags', default=[], action=_MakeList,
                        help=('A list of tags to tag this new task with'))

    parser.add_argument('--graphite_value', default=1, type=float,
                        help=('Value to send to graphite for each of the '
                              'graphite statistics specified '
                              '(default %(default)s)'))
    parser.add_argument('--graphite_host',
                        default=alertlib.graphite.DEFAULT_GRAPHITE_HOST,
                        help=('host:port to send graphite data to '
                              '(default %(default)s)'))

    parser.add_argument('--stackdriver_value',
                        default=alertlib.stackdriver.DEFAULT_STACKDRIVER_VALUE,
                        type=float,
                        help=('Value to send to stackdriver for each of the '
                              'stackdriver statistics specified '
                              '(default %(default)s)'))
    parser.add_argument('--stackdriver_project',
                        default=(
                            alertlib.stackdriver.DEFAULT_STACKDRIVER_PROJECT),
                        help=('Stackdriver project to send datapoints to '
                              '(default %(default)s)'))

    parser.add_argument('-n', '--dry-run', action='store_true',
                        help=("Just log what we would do, but don't do it"))

    return parser


def alert(message, args):
    a = alertlib.Alert(message, args.summary, args.severity, html=args.html)

    for room in args.hipchat:
        a.send_to_hipchat(room, args.color, args.notify,
                          args.chat_sender or 'AlertiGator')

    for channel in args.slack:
        a.send_to_slack(channel, sender=args.chat_sender,
                        intro=args.slack_intro,
                        icon_url=args.icon_url, icon_emoji=args.icon_emoji,
                        simple_message=args.slack_simple_message,
                        attachments=json.loads(args.slack_attachments))

    if args.mail:
        a.send_to_email(args.mail, args.cc, args.bcc, args.sender_suffix)

    for project in args.asana:
        a.send_to_asana(project, tags=args.asana_tags, followers=args.cc)

    if args.pagerduty:
        a.send_to_pagerduty(args.pagerduty)

    if args.logs:
        a.send_to_logs()

    for statistic in args.graphite:
        a.send_to_graphite(statistic, args.graphite_value,
                           args.graphite_host)

    for statistic in args.stackdriver:
        statistic_parts = statistic.split('|')
        metric_name = statistic_parts[0]
        metric_labels = dict(part.split('=') for part in statistic_parts[1:])
        a.send_to_stackdriver(metric_name, args.stackdriver_value,
                              metric_labels=metric_labels,
                              project=args.stackdriver_project,
                              ignore_errors=False)


def main(argv):
    parser = setup_parser()
    args = parser.parse_args(argv)

    if sys.stdin.isatty():
        print('>> Enter the message to alert, then hit control-D',
              file=sys.stderr)
    message = sys.stdin.read().strip()

    if args.dry_run:
        alertlib.enter_test_mode()
        logging.getLogger().setLevel(logging.INFO)

    alert(message, args)


if __name__ == '__main__':
    main(sys.argv[1:])
