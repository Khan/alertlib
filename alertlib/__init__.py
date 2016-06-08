"""Library for alerting various backends from within an app.

The goal of alert-lib is to make it easy to send alerts when
appropriate from your code.  For instance, you might send an alert to
hipchat + email when your long-running task is done, or an alert to
pagerduty when your long-running task fails.

USAGE:
   alertlib.Alert("message")
       .send_to_hipchat(...)
       .send_to_email(...)
       .send_to_pagerduty(...)
       .send_to_logs(...)
       .send_to_graphite(...)

or, if you don't like chaining:
   alert = alertlib.Alert("message")
   alert.send_to_hipchat(...)
   alert.send_to_email(...)
   [etc]


The backends supported are:

    * KA HipChat room
    * KA Slack channel
    * KA Asana tasks
    * KA email
    * KA PagerDuty account
    * Logs -- GAE logs on appengine, or syslogs on a unix box
    * Graphite -- update a counter to indicate this alert happened

You can send an alert to one or more of these.

Some advice on how to choose:

* Are you alerting about something that needs to be fixed?  Send it to
  PagerDuty, which is the only one of these that keeps track of
  whether a problem is fixed or not.

* Are you alerting about something you want people to know about right
  away?  Send it to an email role account that forward to those
  people, or send a HipChat message that mentions those people with
  @name.

* Are you alerting about something that is nice-to-know?  ("Regular
  cron task X has finished" often falls into this category.)  Send it
  to HipChat.

When sending to email, we try using both google appengine (for when
you're using this within an appengine app) and sendmail.
"""

import json
import logging
import re
import socket
import time
import urllib
import urllib2

try:
    # We use the simpler name here just to make it easier to mock for tests
    import google.appengine.api.mail as google_mail
except ImportError:
    try:
        import google_mail      # defined by alertlib_test.py
    except ImportError:
        pass

try:
    import email
    import email.mime.text
    import email.utils
    import smtplib
except ImportError:
    pass

try:
    import syslog
except ImportError:
    pass

try:
    # KA-specific hack: ka_secrets is a superset of secrets.
    try:
        import ka_secrets as secrets
    except ImportError:
        import secrets
    hipchat_token = secrets.hipchat_alertlib_token
    hostedgraphite_api_key = secrets.hostedgraphite_api_key
    slack_webhook_url = secrets.slack_alertlib_webhook_url
    asana_api_token = secrets.asana_api_token
except ImportError:
    # If this fails, you don't have secrets.py set up as needed for this lib.
    hipchat_token = None
    hostedgraphite_api_key = None
    slack_webhook_url = None
    asana_api_token = None


# We want to convert a PagerDuty service name to an email address
# using the same rules pager-duty does.  From experimentation it seems
# to ignore everything but a-zA-Z0-9_-., and lowercases all letters.
_PAGERDUTY_ILLEGAL_CHARS = re.compile(r'[^A-Za-z0-9._-]')


_GRAPHITE_SOCKET = None
_LAST_GRAPHITE_TIME = None


_TEST_MODE = False

# Map string Asana project names to a list of int Asana project ids
_CACHED_ASANA_PROJECT_MAP = {}

# Map string Asana tag names to a list of int Asana tag ids
# For some reason tag names and project names are not necessarily unique in
# Asana
_CACHED_ASANA_TAG_MAP = {}

# Map string Asana user email address to int Asana user id
_CACHED_ASANA_USER_MAP = {}


def enter_test_mode():
    """In test mode, we just log what we'd do, but don't actually do it."""
    global _TEST_MODE
    _TEST_MODE = True


def exit_test_mode():
    """Exit test mode, and resume actually performing operations."""
    global _TEST_MODE
    _TEST_MODE = False


def _graphite_socket(graphite_hostport):
    """Return a socket to graphite, creating a new one every 10 minutes.

    We re-create every 10 minutes in case the DNS entry has changed; that
    way we lose at most 10 minutes' worth of data.  graphite_hostport
    is, for instance 'carbon.hostedgraphite.com:2003'.  This should be
    for talking the TCP protocol (to mark failures, we want to be more
    reliable than UDP!)
    """
    global _GRAPHITE_SOCKET, _LAST_GRAPHITE_TIME
    if _GRAPHITE_SOCKET is None or time.time() - _LAST_GRAPHITE_TIME > 600:
        if _GRAPHITE_SOCKET:
            _GRAPHITE_SOCKET.close()
        (hostname, port_string) = graphite_hostport.split(':')
        host_ip = socket.gethostbyname(hostname)
        port = int(port_string)
        _GRAPHITE_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _GRAPHITE_SOCKET.connect((host_ip, port))
        _LAST_GRAPHITE_TIME = time.time()

    return _GRAPHITE_SOCKET


class Alert(object):
    """An alert message that can be sent to multiple destinations."""

    def __init__(self, message, summary=None, severity=logging.INFO,
                 html=False, rate_limit=None):
        """Create a new Alert.

        Arguments:
            message: the message to alert.  The message may be either
                unicode or utf-8 (but is stored internally as unicode).
            summary: a summary of the message, used as subject lines for
                email, for instance.  If omitted, the summary is taken as
                the first sentence of the message (but only when
                html==False), up to 60 characters.  The summary may be
                either unicode or utf-8 (but is stored internally as
                unicode.)
            severity: can be any logging level (ERROR, CRITICAL, INFO, etc).
                We do our best to encode the severity into each backend to
                the extent it's practical.
            html: True if the message should be treated as html, not text.
                TODO(csilvers): accept markdown instead, and convert it
                to html (or text) for clients that want that.
            rate_limit: if not None, this Alert object will only emit
                messages of a certain kind (hipchat, log, etc) once every
                rate_limit seconds.
        """
        self.message = message
        self.summary = summary
        self.severity = severity
        self.html = html
        self.rate_limit = rate_limit
        self.last_sent = {}

        if isinstance(self.message, str):
            self.message = self.message.decode('utf-8')
        if isinstance(self.summary, str):
            self.summary = self.summary.decode('utf-8')

    def _passed_rate_limit(self, service_name):
        if not self.rate_limit:
            return True
        now = time.time()
        if now - self.last_sent.get(service_name, -1000000) > self.rate_limit:
            self.last_sent[service_name] = now
            return True
        return False

    def _get_summary(self):
        """Return the summary as given, or auto-extracted if necessary."""
        if self.summary is not None:
            return self.summary

        if not self.message:
            return ''

        # TODO(csilvers): turn html to text, *then* extract the summary.
        # Maybe something like:
        # s = lxml.html.fragment_fromstring(
        #       "  Hi there,\n<a href='/'> Craig</a> ", create_parent='div'
        #       ).xpath("string()")
        # ' '.join(s.split()).strip()
        # Or https://github.com/aaronsw/html2text
        if self.html:
            return ''

        summary = (self.message or '').splitlines()[0][:60]
        if '.' in summary:
            summary = summary[:summary.find('.')]

        # Let's indicate the severity in the summary, as well
        log_priority_to_prefix = {
            logging.DEBUG: "(debug info) ",
            logging.INFO: "",
            logging.WARNING: "WARNING: ",
            logging.ERROR: "ERROR: ",
            logging.CRITICAL: "**CRITICAL ERROR**: ",
        }
        summary = log_priority_to_prefix.get(self.severity, "") + summary

        return summary

    def _mapped_severity(self, severity_map):
        """Given a map from log-level to stuff, return the 'stuff' for us.

        If the map is missing an entry for a given severity level, then we
        return the value for map[INFO].
        """
        return severity_map.get(self.severity, severity_map[logging.INFO])

    # ----------------- HIPCHAT ------------------------------------------

    _LOG_PRIORITY_TO_HIPCHAT_COLOR = {
        logging.DEBUG: "gray",
        logging.INFO: "purple",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
    }

    def _make_hipchat_api_call(self, post_dict_with_secret_token):
        # This is a separate function just to make it easy to mock for tests.
        r = urllib2.urlopen('https://api.hipchat.com/v1/rooms/message',
                            urllib.urlencode(post_dict_with_secret_token))
        if r.getcode() != 200:
            raise ValueError(r.read())

    def _post_to_hipchat(self, post_dict):
        if not hipchat_token:
            logging.warning("Not sending this to hipchat (no token found): %s"
                            % post_dict)
            return

        # We need to send the token to the API!
        post_dict_with_secret_token = post_dict.copy()
        post_dict_with_secret_token['auth_token'] = hipchat_token

        # urlencode requires that all fields be in utf-8.
        for (k, v) in post_dict_with_secret_token.iteritems():
            if isinstance(v, unicode):
                post_dict_with_secret_token[k] = v.encode('utf-8')

        try:
            self._make_hipchat_api_call(post_dict_with_secret_token)
        except Exception, why:
            logging.error('Failed sending %s to hipchat: %s'
                          % (post_dict, why))

    def send_to_hipchat(self, room_name, color=None,
                        notify=None, sender='AlertiGator'):
        """Send the alert message to HipChat.

        Arguments:
            room_name: e.g. '1s and 0s'.
            color: background color, one of "yellow", "red", "green",
                "purple", "gray", or "random".  If None, we pick the
                color automatically based on self.severity.
            notify: should we cause hipchat to beep when sending this.
                If None, we pick the notification automatically based
                on self.severity
        """
        if not self._passed_rate_limit('hipchat'):
            return self

        if color is None:
            color = self._mapped_severity(self._LOG_PRIORITY_TO_HIPCHAT_COLOR)

        if notify is None:
            notify = (self.severity == logging.CRITICAL)

        def _nix_bad_emoticons(text):
            """Remove troublesome emoticons so, e.g., '(128)' renders properly.

            By default (at least in 'text' mode), '8)' is replaced by
            a sunglasses-head emoticon.  There is no way to send
            sunglasses-head using alertlib.  This is a feature.
            """
            return text.replace(u'8)', u'8\u200b)')   # zero-width space

        if self.summary:
            if _TEST_MODE:
                logging.info("alertlib: would send to hipchat room %s: %s"
                             % (room_name, self.summary))
            else:
                self._post_to_hipchat({
                    'room_id': room_name,
                    'from': sender,
                    'message': _nix_bad_emoticons(self.summary),
                    'message_format': 'text',
                    'notify': 0,
                    'color': color})

                # Note that we send the "summary" first, and then the "body".
                # However, these back-to-back calls sometimes swap order en
                # route to HipChat. So, let's sleep for 1 second to avoid that.
                time.sleep(1)

        # hipchat has a 10,000 char limit on messages, we leave some leeway
        message = self.message[:9000]

        if _TEST_MODE:
            logging.info("alertlib: would send to hipchat room %s: %s"
                         % (room_name, message))
        else:
            self._post_to_hipchat({
                'room_id': room_name,
                'from': sender,
                'message': (message if self.html else
                            _nix_bad_emoticons(message)),
                'message_format': 'html' if self.html else 'text',
                'notify': int(notify),
                'color': color})

        return self      # so we can chain the method calls

    # ----------------- SLACK ---------------------------------------------
    # 'good'=green, 'warning'=yellow, 'danger'=red, or use hex colors
    _LOG_PRIORITY_TO_SLACK_COLOR = {
        logging.DEBUG: "",  # blank = uses default color which is light grayish
        logging.INFO: "",
        logging.WARNING: "warning",
        logging.ERROR: "danger",
        logging.CRITICAL: "danger"
    }

    def _make_slack_webhook_post(self, payload_json):
        # This is a separate function just to make it easy to mock for tests.
        req = urllib2.Request(slack_webhook_url)
        req.add_header("Content-Type", "application/json")
        res = urllib2.urlopen(req, payload_json)
        if res.getcode() != 200:
            raise ValueError(res.read())

    def _post_to_slack(self, payload_json):
        if not slack_webhook_url:
            logging.warning("Not sending to slack (no webhook url found): %s",
                            payload_json)
            return
        try:
            self._make_slack_webhook_post(payload_json)
        except Exception as e:
            logging.error("Failed sending %s to slack: %s" % (payload_json, e))

    def _slack_payload(self, channel,
                       simple_message,
                       intro,
                       attachments,
                       link_names,
                       unfurl_links,
                       unfurl_media,
                       icon_url,
                       icon_emoji,
                       sender):
        payload = {"channel": channel, "link_names": link_names,
                   "unfurl_links": unfurl_links, "unfurl_media": unfurl_media}
        # hipchat has a 10,000 char limit on messages, we leave some leeway
        # not sure what slack's limit is (undocumented?) so for now just use
        # the same as hipchat and see what happens.
        # TODO(mroth): test and find the actual limit (or ask SlackHQ)
        message = self.message[:9000]

        if icon_url:
            payload["icon_url"] = icon_url
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji
        if sender:
            payload["username"] = sender

        if simple_message:                          # "simple message" case
            payload["text"] = message
        elif attachments:                           # "attachments" case
            # the documentation tells people only to pass us a list even with a
            # single element (since we want them to be familiar with Slack API)
            # but if they just pass us a dict, be nice and handle for them,
            # since its a very common use-case to want to send a single
            # attachment dict and easy to forget to wrap it
            if not isinstance(attachments, list):
                attachments = [attachments]
            for attachment in attachments:
                # Many times, when writing a custom attachment, people forget
                # to specify a fallback.  Se we'll do it for them.  Slack will
                # automatically convert "<url|text>" to "text url" on fallback
                # clients; for any other markdown we may have put in, we let it
                # be; a lot of IRC clients will interpret it anyway, and it's
                # still readable if they don't.
                if 'fallback' not in attachment:
                    texts = []
                    if 'pretext' in attachment:
                        texts.append(attachment['pretext'])
                    if 'text' in attachment:
                        texts.append(attachment['text'])
                    if 'fields' in attachment:
                        for field in attachment['fields']:
                            texts.append("%s: %s" % (field['title'],
                                                     field['value']))
                    attachment['fallback'] = '\n'.join(texts)
            payload["attachments"] = attachments
            payload['text'] = intro + '\n'

        else:                                       # "alertlib style" case
            color = self._mapped_severity(self._LOG_PRIORITY_TO_SLACK_COLOR)
            fallback = ("{}\n{}".format(self.summary, message)
                        if self.summary else message)
            attachment = {
                "text": message,
                "color": color,
                "fallback": fallback,
                "mrkdwn_in": ["text", "pretext"],
            }
            payload['text'] = intro + '\n'
            if self.summary:
                attachment["pretext"] = self.summary
            payload["attachments"] = [attachment]

        return payload

    def send_to_slack(self, channel,
                      simple_message=False,
                      intro='',
                      attachments=None,
                      link_names=1,
                      unfurl_links=False,
                      unfurl_media=True,
                      icon_url=None,
                      icon_emoji=None,
                      sender=None):
        """Send the alert message to Slack.

        This wraps a subset of the Slack API incoming webhook in order to
        make it behave closer to the default expectations of an AlertLib user,
        while still enabling customization of results.

        If the alert is HTML formatted, it will not be displayed correctly on
        Slack, but for now this method will only WARN in the logs rather than
        error in this condition. In the future, this may change.

        For the default case, try to emulate HipChat style msgs from AlertLib.
        The color of the message will be based on the `Alert.severity`.

        There are two notable exceptions regarding formatting style.

        ### Simple Messages
        First, if `simple_message=True` is passed, the message will be passed
        along to Slack using normal simple Markdown formatting, instead of
        being rendered as "attachment" style.

        ### Attachments
        Second, if an "attachments" dict list is passed, these will be passed
        along to Slack to enable very detailed message display parameters.

        See https://api.slack.com/docs/attachments for attachment details, and
        https://api.slack.com/docs/formatting for more on formatting.  If you
        don't specify fallback text, AlertLib will fill it in for you.

        Note that when passing attachments to Slack, AlertLib will by default
        ignore the `Alert.message`, on the assumption that you will be
        providing your entire UI via the attachment.

        Arguments:
            channel: Slack channel name or encoded ID.
                e.g. '#1s-and-0s' or 'hip-slack' or 'C1234567890'.
                (Note that channel names start with a hashtag whereas private
                groups do not.)

            simple_message: If True, send as a simple Slack message rather than
                constructing a standard AlertLib style formatted message.
                A simple message does not use the 'attachment' mechanism,
                meaning the text is not indented nor does it have a colored
                side-bar.  If True, @mentions in self.message will trigger
                notifications.

            intro: Text that will go ahead of the message and attachments.
                @mentions in this text will trigger desktop and phone
                notifications.  Will be ignored if simple_message is True.

            attachments: List of "attachments" dicts for advanced formatting.
                Even if you are only sending one attachment, you must place it
                in a list.  @mentions in attachment texts and pretexts do not
                trigger notifications.

            link_names: Automatically link channels and usernames in message.
                If disabled, user and channel names will need need to be
                explicitly marked up in order to be linked, e.g. <@mroth> or
                <#hipslack>.

            unfurl_links: Enable unfurling -- showing a content preview --
                of primarily text-based content.

            unfurl_media: Enable unfurling -- showing a content preview --
                of media content.

            icon_url: URL to an image to use as the icon for this message.

            icon_emoji: Emoji to use as the icon for this message.
                Default is to use the Slack integration's default setting,
                which is likely :crocodile:.

            sender: Name of the bot.
                Default is to use the Slack integration's default setting,
                which is likely "AlertiGator".
        """
        if not self._passed_rate_limit('slack'):
            return self

        if self.html:
            logging.warning("Unsupported HTML msg being sent to Slack!: %s",
                            self.message)

        payload = self._slack_payload(
            channel, simple_message=simple_message, intro=intro,
            attachments=attachments, link_names=link_names,
            unfurl_links=unfurl_links, unfurl_media=unfurl_media,
            icon_url=icon_url, icon_emoji=icon_emoji, sender=sender)
        payload_json = json.dumps(payload)
        if _TEST_MODE:
            logging.info("alertlib: would send to slack channel %s: %s"
                         % (channel, payload_json))
        else:
            self._post_to_slack(payload_json)

        return self      # so we can chain the method calls

    # ----------------- ASANA --------------------------------------------
    _LOG_PRIORITY_TO_ASANA_TAG = {
        logging.INFO: 'P4',
        logging.WARNING: 'P3',
        logging.ERROR: 'P2',
        logging.CRITICAL: 'P1'
    }

    def _call_asana_api(self, req_url_path, post_dict=None):
        """GET from Asana API if no post_dict, otherwise POST post_dict.

        Returns the `data` field of the response content from Asana API
        on success. Returns None on error.
        """
        if not asana_api_token:
            logging.warning("Not sending this to asana (no token found): %s"
                            % post_dict)
            return None

        host_url = 'https://app.asana.com'
        req = urllib2.Request(host_url + req_url_path)
        req.add_header('Authorization', 'Bearer %s' % asana_api_token)

        if post_dict is not None:
            # urlencode requires that all fields be in utf-8.
            post_dict_copy = {'data': {}}
            for (k, v) in post_dict['data'].iteritems():
                if isinstance(v, unicode):
                    post_dict_copy['data'][k] = v.encode('utf-8')
                else:
                    post_dict_copy['data'][k] = v
            req.add_header("Content-Type", "application/json")
            post_dict = json.dumps(post_dict_copy)

        try:
            res = urllib2.urlopen(req, post_dict)
        except Exception as e:
            logging.error('Failed sending %s to asana because of %s'
                          % (post_dict, e))
            return None
        if res.getcode() >= 300:
            logging.error('Failed sending %s to asana with code %d'
                          % (post_dict, res.getcode()))
            return None
        return json.loads(res.read())['data']

    def _check_task_already_exists(self, post_dict):
        """Check whether the new task in post_dict is already in Asana.

        Gets all tasks with tag matching the last tag in post_dict (which)
        should always be the "Auto generated" tag. Check if the name of the new
        task in post_dict matches the name of any previously Auto generated,
        unfinished task in Asana and return True if it does.

        NOTE(alexanderforsyth) this will fail if there are more than about
        1000 current tasks (not closed) with tag "Auto generated".
        TODO(alexanderforsyth) add support for more than 1000 tasks via
        pagination?

        NOTE(alexanderforsyth) upon failure for any reason including the above,
        this returns False meaning that a duplicate task might be created.
        """
        req_url_path = ('/api/1.0/tasks?tag=%s'
                        '&opt_fields=completed,name'
                        % post_dict['data']['tags'][-1])

        res = self._call_asana_api(req_url_path)
        if res is None:
            logging.warning('Failed testing current task for uniqueness. '
                            'This task might be created as a duplicate.')
            return False

        # check whether any current Auto generated tasks have the same name
        for item in res:
            is_same = item['name'] == post_dict['data']['name']
            if is_same and not item['completed']:
                return True
        return False

    def _get_asana_user_ids(self, user_emails, workspace):
        """Returns the list of asana user ids for the given user emails.

        If _CACHED_ASANA_USER_MAP, this looks up the user_ids for each
        user_email via the cache and returns them, not adding a user_id
        for any invalid user_email. If the cache is empty, this builds the
        asana user cache. Returns [] if there is an error building the cache.

        After a cache miss, this looks up all user email to user id mappings
        from the Asana API and caches the result.
        """
        if not _CACHED_ASANA_USER_MAP:
            req_url_path = ('/api/1.0/users?workspace=%s&opt_fields=id,email'
                            % str(workspace))
            res = self._call_asana_api(req_url_path)
            if res is None:
                logging.error('Failed to build Asana user cache. Fields '
                              'involving user IDs such as followers might'
                              'not be included in this task.')
                return []

            for user_item in res:
                user_email = user_item['email']
                user_id = int(user_item['id'])
                _CACHED_ASANA_USER_MAP[user_email] = user_id

        asana_user_ids = []
        for user_email in user_emails:
            if user_email in _CACHED_ASANA_USER_MAP:
                asana_user_ids.append(_CACHED_ASANA_USER_MAP[user_email])
            else:
                logging.error('Invalid asana user email: %s; '
                              'Fields involving this user such as follower '
                              'will not added to task.' % user_email)
        return asana_user_ids

    def _get_asana_tag_ids(self, tag_names, workspace):
        """Returns the list of tag ids for the given tag names.

        If _CACHED_ASANA_TAG_MAP, this looks up the tag_ids for each tag_name
        via the cache and returns them, not adding a tag_id for any invalid
        tag_name. If the cache is empty, this builds the asana tags cache.
        Returns None if there is an error building the cache.

        This looks up all tag name to tag id mappings from the Asana
        API and caches the result. Note that names do not necessarily uniquely
        map to an id so multiple ids can be returned for each tag.
        """
        if not _CACHED_ASANA_TAG_MAP:
            req_url_path = ('/api/1.0/tags?workspace=%s'
                            % str(workspace))
            res = self._call_asana_api(req_url_path)
            if res is None:
                logging.error('Failed to build Asana tags cache. '
                              'Task will not be created')
                return None

            for tag_item in res:
                tag_name = tag_item['name']
                tag_id = int(tag_item['id'])
                if tag_name not in _CACHED_ASANA_TAG_MAP:
                    _CACHED_ASANA_TAG_MAP[tag_name] = []
                _CACHED_ASANA_TAG_MAP[tag_name].append(tag_id)

        asana_tag_ids = []
        for tag_name in tag_names:
            if tag_name in _CACHED_ASANA_TAG_MAP:
                asana_tag_ids.extend(_CACHED_ASANA_TAG_MAP[tag_name])
            else:
                logging.error('Invalid asana tag name: %s; '
                              'tag not added to task.' % tag_name)
        return asana_tag_ids

    def _get_asana_project_ids(self, project_name, workspace):
        """Returns the list of project ids for the given project name.

        If _CACHED_ASANA_PROJECT_MAP, this looks up the project_ids for
        project_name in the cache and returns it (None if not found). If the
        cache is empty, this builds the asana projects cache.

        This looks up all project name to project id mappings from the Asana
        API and caches the result. Note that names do not necessarily uniquely
        map to an id so multiple ids can be returned.
        """
        if _CACHED_ASANA_PROJECT_MAP:
            return _CACHED_ASANA_PROJECT_MAP.get(project_name)

        req_url_path = ('/api/1.0/projects?workspace=%s'
                        % str(workspace))

        res = self._call_asana_api(req_url_path)
        if res is None:
            return None

        for project_item in res:
            p_name = project_item['name']
            p_id = int(project_item['id'])
            if p_name not in _CACHED_ASANA_PROJECT_MAP:
                _CACHED_ASANA_PROJECT_MAP[p_name] = []
            _CACHED_ASANA_PROJECT_MAP[p_name].append(p_id)

        return _CACHED_ASANA_PROJECT_MAP.get(project_name)

    # Obtained via app.asana.com/api/1.0/workspaces/
    KA_ASANA_WORKSPACE_ID = 1120786379245

    def send_to_asana(self,
                      project,
                      tags=None,
                      workspace=KA_ASANA_WORKSPACE_ID,
                      followers=None):
        """Automatically create an Asana task for the alert.

        Arguments (reference asana.com/developers/api-reference/tasks#create):

            project: string name of the project for this task.
                The project name must be a valid name of a project in
                workspace on Asana. If it is not a correct name, the task will
                not be created, but no exception will be raised.
                Example: 'Engineering support'

            tags: list of string names of tags for this task.
                Each tag name should be a valid, existing tag name in
                workspace on Asana. If one is not a valid name, it will not
                be added to the list of tags, and no exceptions will be thrown.
                The "Auto generated" tag is always added to the end of the
                supplied list (and should not be included in this parameter).
                A P# tag will be added to this task if severity is included in
                this Alert and no P# tag is given in the supplied tags list.
                Example: ['P3', 'quick']

            workspace: int id of the workspace for the task to be posted in.
                Default 1120786379245 for khanacademy.org

            followers: array of asana user email addresses of followers for
                this task. Note that a user's email in Asana is not always
                ka_username@khanacademy.org, and should be verified by looking
                up that user in the Asana search bar.
                Default: []

        The Alert message should ideally contain where the alert is coming
        from. E.g. it could include "Top daily error from error-monitor-db"
        """

        if not self._passed_rate_limit('asana'):
            return self

        followers = followers or []
        tags = tags or []

        # check that user specified no P# values before adding alert severity
        # level P# tag
        # existing_p_tags is the set of current P# tags where P# is a value in
        # self._LOG_PRIORITY_TO_ASANA_TAG
        p_tags = set(self._LOG_PRIORITY_TO_ASANA_TAG.values())
        existing_p_tags = set(tags).intersection(p_tags)

        severity_tag_name = self._LOG_PRIORITY_TO_ASANA_TAG.get(self.severity)
        if severity_tag_name and not existing_p_tags:
            tags.append(severity_tag_name)

        # auto-generated is always the last tag
        tags.append('Auto generated')

        task_name = self.summary or ('New Auto generated Asana task')

        asana_project_ids = self._get_asana_project_ids(project, workspace)
        if not asana_project_ids:
            logging.error('Invalid asana project name; task not created.')
            return self

        asana_follower_ids = self._get_asana_user_ids(followers, workspace)

        asana_tag_ids = self._get_asana_tag_ids(tags, workspace)
        if asana_tag_ids is None:
            logging.error('Failed to retrieve asana tag name to tag id'
                          ' mapping. Task will not be created.')
            return self

        post_dict = {'data': {
                     'followers': asana_follower_ids,
                     'name': task_name,
                     'notes': self.message,
                     'projects': asana_project_ids,
                     'tags': asana_tag_ids,
                     'workspace': workspace}}

        if _TEST_MODE:
            logging.info('alertlib: would send to asana: %s'
                         % json.dumps(post_dict))
        else:
            # check that the post_dict task does not already exist
            if not self._check_task_already_exists(post_dict):
                req_url_path = '/api/1.0/tasks'
                self._call_asana_api(req_url_path, post_dict)

        return self

    # ----------------- EMAIL --------------------------------------------

    def _get_sender(self, sender):
        sender_addr = 'no-reply'
        if sender:
            # Replace everything that's not alphanumeric with '-'
            sender_addr += '+' + re.sub(r'\W', '-', sender)
        return 'alertlib <%s@khanacademy.org>' % sender_addr

    def _send_to_gae_email(self, message, email_addresses, cc=None, bcc=None,
                           sender=None):
        gae_mail_args = {
            'subject': self._get_summary(),
            'sender': self._get_sender(sender),
            'to': email_addresses,      # "x@y" or "Full Name <x@y>"
        }
        if cc:
            gae_mail_args['cc'] = cc
        if bcc:
            gae_mail_args['bcc'] = bcc
        if self.html:
            # TODO(csilvers): convert the html to text for 'body'.
            # (see above about using html2text or similar).
            gae_mail_args['body'] = message
            gae_mail_args['html'] = message
        else:
            gae_mail_args['body'] = message
        google_mail.send_mail(**gae_mail_args)

    def _send_to_sendmail(self, message, email_addresses, cc=None, bcc=None,
                          sender=None):
        msg = email.mime.text.MIMEText(message.encode('utf-8'),
                                       'html' if self.html else 'plain')
        msg['Subject'] = self._get_summary().encode('utf-8')
        msg['From'] = self._get_sender(sender)
        msg['To'] = ', '.join(email_addresses)
        # We could pass the priority in the 'Importance' header, but
        # since nobody pays attention to that (and we can't even set
        # that header when sending from appengine), we just use the
        # fact it's embedded in the subject line.
        if cc:
            if not isinstance(cc, basestring):
                cc = ', '.join(cc)
            msg['Cc'] = cc
        if bcc:
            if not isinstance(bcc, basestring):
                bcc = ', '.join(bcc)
            msg['Bcc'] = bcc

        # I think sendmail wants just email addresses, so extract
        # them in case the user specified "Name <email>".
        to_emails = [email.utils.parseaddr(a) for a in email_addresses]
        to_emails = [email_addr for (_, email_addr) in to_emails]

        s = smtplib.SMTP('localhost')
        s.sendmail('no-reply@khanacademy.org', to_emails, msg.as_string())
        s.quit()

    def _send_to_email(self, email_addresses, cc=None, bcc=None, sender=None):
        """An internal routine; email_addresses must be full addresses."""
        # Make sure the email text ends in a single newline.
        message = self.message.rstrip('\n') + '\n'

        # Try sending to appengine first.
        try:
            self._send_to_gae_email(message, email_addresses, cc, bcc, sender)
            return
        except (NameError, AssertionError), why:
            pass

        # Try using local smtp.
        try:
            self._send_to_sendmail(message, email_addresses, cc, bcc, sender)
            return
        except (NameError, smtplib.SMTPException), why:
            pass

        logging.error('Failed sending email: %s' % why)

    def send_to_email(self, email_usernames, cc=None, bcc=None, sender=None):
        """Send the message to a khan academy email account.

        (We *could* send emails outside ka.org, but right now the API
        doesn't allow it just because we haven't needed it.  We could
        consider loosening this restriction if there's a need, but
        better would be to create a new API endpoint like PagerDuty
        does.)

        The subject of the email is taken from the 'summary' field
        given to the Alert constructor, or is taken to be the first
        sentence of the message otherwise.  The subject will also be
        prepended with the severity of the alert, if not INFO.

        Arguments:
            email_usernames: an username to send the email to, or
                else a list of such usernames.  This is the mail
                username: so 'foo' in 'foo@khanacademy.org'.  You do
                not need to specify 'khanacademy.org'.
            cc / bcc: who to cc/bcc on the email.  Takes usernames as
                a string or list, same as email_usernames.
            sender: an optional addition to the sender address, which if
                provided, becomes 'alertlib <no-reply+sender@khanacademy.org>'.
        """
        if not self._passed_rate_limit('email'):
            return self

        def _normalize(lst):
            if lst is None:
                return None
            if isinstance(lst, basestring):
                lst = [lst]
            for i in xrange(len(lst)):
                if not lst[i].endswith('@khanacademy.org'):
                    if '@' in lst[i]:
                        raise ValueError('Specify email usernames, '
                                         'not addresses (%s)' % lst[i])
                    lst[i] += '@khanacademy.org'
            return lst

        email_addresses = _normalize(email_usernames)
        cc = _normalize(cc)
        bcc = _normalize(bcc)

        email_contents = ("email to %s (from %s CC %s BCC %s): (subject %s) %s"
                          % (email_addresses, self._get_sender(sender),
                             cc, bcc, self._get_summary(), self.message))
        if _TEST_MODE:
            logging.info("alertlib: would send %s" % email_contents)
        else:
            try:
                self._send_to_email(email_addresses, cc, bcc, sender)
            except Exception, why:
                logging.error('Failed sending %s: %s' % (email_contents, why))

        return self

    # ----------------- PAGERDUTY ----------------------------------------

    def send_to_pagerduty(self, pagerduty_servicenames):
        """Send an incident report to PagerDuty.

        Arguments:
            pagerduty_servicenames: either a string, or a list of
                 strings, that are the names of PagerDuty services.
                 https://www.pagerduty.com/docs/guides/email-integration-guide/
        """
        if not self._passed_rate_limit('pagerduty'):
            return self

        def _service_name_to_email(lst):
            if isinstance(lst, basestring):
                lst = [lst]
            for i in xrange(len(lst)):
                if '@' in lst[i]:
                    raise ValueError('Specify PagerDuty service names, '
                                     'not addresses (%s)' % lst[i])
                # Convert from a service name to an email address.
                lst[i] = _PAGERDUTY_ILLEGAL_CHARS.sub('', lst[i]).lower()
                lst[i] += '@khan-academy.pagerduty.com'
            return lst

        email_addresses = _service_name_to_email(pagerduty_servicenames)

        email_contents = ("pagerduty email to %s (subject %s) %s"
                          % (email_addresses, self._get_summary(),
                             self.message))
        if _TEST_MODE:
            logging.info("alertlib: would send %s" % email_contents)
        else:
            try:
                self._send_to_email(email_addresses)
            except Exception, why:
                logging.error('Failed sending %s: %s' % (email_contents, why))

        return self

    # ----------------- LOGS ---------------------------------------------

    try:
        _LOG_TO_SYSLOG = {
            logging.DEBUG: syslog.LOG_DEBUG,
            logging.INFO: syslog.LOG_INFO,
            logging.WARNING: syslog.LOG_WARNING,
            logging.ERROR: syslog.LOG_ERR,
            logging.CRITICAL: syslog.LOG_CRIT
        }
    except NameError:     # can't load syslog
        _LOG_TO_SYSLOG = {}

    def send_to_logs(self):
        """Send to logs: either GAE logs (for appengine) or syslog."""
        if not self._passed_rate_limit('logs'):
            return self

        logging.log(self.severity, self.message)

        # Also send to syslog if we can.
        if not _TEST_MODE:
            try:
                syslog_priority = self._mapped_severity(self._LOG_TO_SYSLOG)
                syslog.syslog(syslog_priority, self.message.encode('utf-8'))
            except (NameError, KeyError):
                pass

        return self

    # ----------------- GRAPHITE -----------------------------------------

    DEFAULT_GRAPHITE_HOST = 'carbon.hostedgraphite.com:2003'

    def send_to_graphite(self, statistic, value=1,
                         graphite_host=DEFAULT_GRAPHITE_HOST):
        """Increment the given counter on a graphite/statds instance.

        statistic should be a dotted name as used by graphite: e.g.
        myapp.stats.num_failures.  When send_to_graphite() is called,
        we send the given value for that statistic to graphite.
        """
        if not self._passed_rate_limit('graphite'):
            return self

        # If the value is 12.0, send it as 12, not 12.0
        if int(value) == value:
            value = int(value)

        if _TEST_MODE:
            logging.info("alertlib: would send to graphite: %s %s"
                         % (statistic, value))
        elif not hostedgraphite_api_key:
            logging.warning("Not sending to graphite; no API key found: %s %s"
                            % (statistic, value))
        else:
            try:
                _graphite_socket(graphite_host).send('%s.%s %s\n' % (
                    hostedgraphite_api_key, statistic, value))
            except Exception, why:
                logging.error('Failed sending to graphite: %s' % why)

        return self

__all__ = [
    enter_test_mode,
    exit_test_mode,
    Alert
]
