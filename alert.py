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
import alertlib.alerta


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
                        help=('DEPRECATED: Use --bugtracker.'))
    parser.add_argument('--pagerduty', default=[], action=_MakeList,
                        help=('Send to PagerDuty.  Argument is a comma-'
                              'separated list of PagerDuty services. '
                              'May specify --summary as a brief summary; '
                              'if missing we figure it out automatically. '))
    parser.add_argument('--logs', action='store_true',
                        help=('Send to syslog.  May specify --severity.'))
    parser.add_argument('--graphite', default=[], action=_MakeList,
                        help=('Send to graphite.  Argument is a comma-'
                              'separated list of statistics to update. '
                              'May specify --graphite_value and '
                              '--graphite_host.'))
    parser.add_argument('--stackdriver', default=[], action=_MakeList,
                        help=('Send to Stackdriver.  Argument is a comma-'
                              'separated list of metrics to update, with '
                              'metric-label-values separated by pipes like so:'
                              ' `logs.500|module=i18n`. '
                              'May specify --stackdriver_value'))
    parser.add_argument('--aggregator', default=[], action=_MakeList,
                        help=('Send to aggregator such as Alerta.io. Argument'
                              'is comma-separated list of initiatives.'
                              'Must specify --aggregator-resource and '
                              '--aggregator-event-name and may specify '
                              '--severity and/or --summary'))
    parser.add_argument('--bugtracker', default=[], action=_MakeList,
                        help=('Make issue in the bugtracker (right now that '
                              'means Jira, though Asana is also an option). '
                              'Argument is comma-separated list of initiatives'
                              ' (e.g. "Infrastructure", "Test Prep") '
                              'May specify --cc with emails of those '
                              'who should be added as followers/watchers or '
                              'add --bug-tags for adding tags/labels to issue'
                              'May add --severity and/or --summary'))

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
                              'In HipChat and with Slack bot tokens, defaults '
                              'to AlertiGator.  With slack webhooks, defaults '
                              "to whatever the webhook's default is, likely "
                              'also AlertiGator.'))
    parser.add_argument('--icon-emoji', default=None,
                        help=('The emoji sender to use for this message. '
                              'Slack only.  Defaults to whatever the '
                              "webhook's default is, or :crocodile: if "
                              'using a bot token.'))
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
    parser.add_argument('--slack-thread', default=None,
                        help=('A slack message timestamp to thread this with. '
                              'Must be the timestamp of a toplevel message in '
                              'the specified slack channel.'))

    parser.add_argument('--cc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))
    parser.add_argument('--sender-suffix', default=None,
                        help=('This adds "foo" to the sender address, which '
                              'is alertlib <no-reply+foo@khanacademy.org>.'))
    parser.add_argument('--bcc', default=[], action=_MakeList,
                        help=('A comma-separated list of email addresses; '
                              'used with --mail'))

    parser.add_argument('--bug-tags', default=[], action=_MakeList,
                        help=('A list of tags to add to this new task/issue'))

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

    parser.add_argument('--aggregator-resource', default=None, choices=sorted(
                        alertlib.alerta.MAP_RESOURCE_TO_ENV_SERVICE_AND_GROUP),
                        help=('Name of resource where alert originated to '
                              'send to aggregator. Only relevent when used '
                              'in conjunction with --aggregator. Choices '
                              'include: jenkins, mobile, test, toby, webapp'))

    parser.add_argument('--aggregator-event-name', default=None,
                        help=('Name of the event for use by aggregator. '
                              '(e.g. ServiceDown, Error) Only relevent when '
                              'used in conjunction with --aggregator.'))

    parser.add_argument('--aggregator-timeout', default=None, type=int,
                        help='Timeout in seconds before alert is '
                        'automatically resolved. If not specified then the '
                        'default aggregator timeout is used.')

    parser.add_argument('--aggregator-resolve', dest='aggregator_resolve',
                        action='store_true',
                        help='Resolve this alert in the aggregator. '
                        'This will clear the alert from the Alerta dashboard.')

    parser.set_defaults(aggregator_resolve=False)

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
                        attachments=json.loads(args.slack_attachments),
                        thread=args.slack_thread)

    if args.mail:
        a.send_to_email(args.mail, args.cc, args.bcc, args.sender_suffix)

    # TODO(jacqueline): The --asana flag is deprecated and all callers
    # should be shifted to --bugtracker. Remove support for this tag when
    # confirmed that there are no remaining callers using this flag.
    for project in args.asana:
        a.send_to_asana(project, tags=args.bug_tags, followers=args.cc)

    for project in args.bugtracker:
        a.send_to_bugtracker(project, labels=args.bug_tags, watchers=args.cc)

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

    # subject to change if we decide go the route of having an alert 
    # be exclusive to just one initiative
    for initiative in args.aggregator:
        a.send_to_alerta(initiative,
                         resource=args.aggregator_resource,
                         event=args.aggregator_event_name,
                         timeout=args.aggregator_timeout,
                         resolve=args.aggregator_resolve)


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
