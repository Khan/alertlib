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

    * KA hipchat room
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

# If this fails, you don't have secrets.py set up as needed for this lib.
import secrets
hipchat_token = secrets.hipchat_deploy_token
hostedgraphite_api_key = secrets.hostedgraphite_api_key


# We want to convert a PagerDuty service name to an email address
# using the same rules pager-duty does.  From experimentation it seems
# to ignore everything but a-zA-Z0-9_-., and lowercases all letters.
_PAGERDUTY_ILLEGAL_CHARS = re.compile(r'[^A-Za-z0-9._-]')


_GRAPHITE_SOCKET = None
_LAST_GRAPHITE_TIME = None


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
    def __init__(self, message, summary=None, severity=logging.INFO,
                 html=False):
        """Arguments:

        message: the message to alert
        summary: a summary of the message, used as subject lines for email,
            for instance.  If omitted, the summary is taken as the first
            sentence of the message (but only when html==False), up to
            60 characters.
        severity: can be any logging level (ERROR, CRITICAL, INFO, etc).
            We do our best to encode the severity into each backend to
            the extent it's practical.
        html: True if the message should be treated as html, not text.
        """
        self.message = message
        self.summary = summary
        self.severity = severity
        self.html = html

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

    _LOG_PRIORITY_TO_COLOR = {
        logging.DEBUG: "gray",
        logging.INFO: "purple",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
        }

    def _post_to_hipchat(self, post_dict):
        r = urllib2.urlopen('https://api.hipchat.com/v1/rooms/message'
                            '?auth_token=%s' % hipchat_token,
                            urllib.urlencode(post_dict))
        if r.getcode() != 200:
            logging.error('Failed sending to hipchat: %s' % r.read())

    def send_to_hipchat(self, room_name, color=None,
                        notify=None):
        """
        Arguments:
            room_name: e.g. '1s and 0s'.
            color: background color, one of "yellow", "red", "green",
                "purple", "gray", or "random".  If None, we pick the
                color automatically based on self.severity.
            notify: should we cause hipchat to beep when sending this.
                If None, we pick the notification automatically based
                on self.severity
        """
        if color is None:
            color = self._mapped_severity(self._LOG_PRIORITY_TO_COLOR)

        if notify is None:
            notify = (self.severity == logging.CRITICAL)

        if self.summary:
            self._post_to_hipchat({
                'room_id': room_name,
                'from': 'AlertiGator',
                'message': self.summary,
                'message_format': 'text',
                'notify': 0,
                'color': color})

        # hipchat has a 10,000 char limit on messages, we leave some leeway
        message = self.message[:9000]

        self._post_to_hipchat({
                'room_id': room_name,
                'from': 'AlertiGator',
                'message': message,
                'message_format': 'html' if self.html else 'text',
                'notify': int(notify),
                'color': color})

        return self      # so we can chain the method calls

    # ----------------- EMAIL --------------------------------------------

    def _send_to_gae_email(self, message, email_addresses, cc=None, bcc=None):
        gae_mail_args = {
            'subject': self._get_summary(),
            'sender': 'alertlib <no-reply@khanacademy.org>',
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

    def _send_to_sendmail(self, message, email_addresses, cc=None, bcc=None):
        msg = email.mime.text.MIMEText(message,
                                       'html' if self.html else 'text')
        msg['Subject'] = self._get_summary()
        msg['From'] = 'alertlib <no-reply@khanacademy.org>'
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

    def _send_to_email(self, email_addresses, cc=None, bcc=None):
        """An internal routine; email_addresses must be full addresses."""
        # Make sure the email text ends in a single newline.
        message = self.message.rstrip('\n') + '\n'

        # Try sending to appengine first.
        try:
            self._send_to_gae_email(message, email_addresses, cc, bcc)
            return
        except (NameError, AssertionError), why:
            pass

        # Try using local smtp.
        try:
            self._send_to_sendmail(message, email_addresses, cc, bcc)
            return
        except (NameError, smtplib.SMTPException), why:
            pass

        logging.error('Failed sending email: %s' % why)

    def send_to_email(self, email_usernames, cc=None, bcc=None):
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
        """
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

        self._send_to_email(email_addresses, cc, bcc)

        return self

    # ----------------- PAGERDUTY ----------------------------------------

    def send_to_pagerduty(self, pagerduty_servicenames):
        """Send an incident report to PagerDuty.

        Arguments:
            pagerduty_servicenames: either a string, or a list of
                 strings, that are the names of PagerDuty services.
                 https://www.pagerduty.com/docs/guides/email-integration-guide/
        """
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
        self._send_to_email(email_addresses)

        return self

    # ----------------- LOGS ---------------------------------------------

    _LOG_TO_SYSLOG = {
        logging.DEBUG: syslog.LOG_DEBUG,
        logging.INFO: syslog.LOG_INFO,
        logging.WARNING: syslog.LOG_WARNING,
        logging.ERROR: syslog.LOG_ERR,
        logging.CRITICAL: syslog.LOG_CRIT
        }

    def send_to_logs(self):
        """Send to logs: either GAE logs (for appengine) or syslog."""
        logging.log(self.severity, self.message)

        # Also send to syslog if we can.
        try:
            syslog_priority = self._mapped_severity(self._LOG_TO_SYSLOG)
            syslog.syslog(syslog_priority, self.message)
        except NameError:
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
        _graphite_socket(graphite_host).send('%s.%s %s' % (
                hostedgraphite_api_key, statistic, value))

        return self
