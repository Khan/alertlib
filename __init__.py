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

or, if you don't like chaining:
   alert = alertlib.Alert("message")
   alert.send_to_hipchat(...)
   alert.send_to_email(...)
   alert.send_to_pagerduty(...)


The backends supported are:

    * KA hipchat room
    * KA email
    * KA PagerDuty account
    * Logs -- GAE logs on appengine, or syslogs on a unix box

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
import urllib
import urllib2

try:
    import google.appengine.api.mail
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


# We want to convert a PagerDuty service name to an email address
# using the same rules pager-duty does.  From experimentation it seems
# to ignore everything but a-zA-Z0-0_-., and lowercases all letters.
_PAGERDUTY_ILLEGAL_CHARS = re.compile(r'[!A-Za-z0-9._-]')


class Alert(object):
    def __init__(self, message, html=False):
        """html: True if the message should be treated as html, not text."""
        self.message = message
        self.html = html

    def send_to_hipchat(self, room_name, color="purple",
                        notify=False):
        """
        Arguments:
            room_name: e.g. '1s and 0s'.
            color: background color, one of "yellow", "red", "green",
                "purple", "gray", or "random".
            notify: should we cause hipchat to beep when sending this.
        """
        # hipchat has a 10,000 char limit on messages, we leave some leeway
        message = self.message[:9000]

        post_data = urllib.urlencode({
                'room_id': room_name,
                'from': 'alertlib',
                'message': message,
                'message_format': 'html' if self.html else 'text',
                'notify': int(notify),
                'color': color})
        r = urllib2.urlopen('https://api.hipchat.com/v1/rooms/message'
                            '?auth_token=%s' % hipchat_token,
                            post_data)
        if r.getcode() != 200:
            logging.error('Failed sending to hipchat: %s' % r.read())

        return self      # so we can chain the method calls

    def _send_to_gae_email(self, email_addresses, subject, cc=None, bcc=None):
        gae_mail_args = {
            'subject': subject,
            'sender': 'alertlib <no-reply@khanacademy.org>',
            'to': email_addresses,      # "x@y" or "Full Name <x@y>"
            }
        if cc:
            gae_mail_args['cc'] = cc
        if bcc:
            gae_mail_args['bcc'] = bcc
        if self.html:
            # TODO(csilvers): convert the html to text for 'body'.
            gae_mail_args['body'] = self.message
            gae_mail_args['html'] = self.message
        else:
            gae_mail_args['body'] = self.message
        google.appengine.api.mail.send_mail(**gae_mail_args)

    def _send_to_sendmail(self, email_addresses, subject, cc=None, bcc=None):
        msg = email.mime.text.MIMEText(self.message,
                                       'html' if self.html else 'text')
        msg['Subject'] = subject
        msg['From'] = 'alertlib <no-reply@khanacademy.org>'
        msg['To'] = email_addresses
        if cc:
            if not isinstance(cc, basestring):
                cc = ', '.join(cc)
            msg['cc'] = cc
        if bcc:
            if not isinstance(bcc, basestring):
                bcc = ', '.join(bcc)
            msg['bcc'] = bcc

        # I think sendmail wants just email addresses, so extract
        # them in case the user specified "Name <email>".
        to_emails = [email.utils.parseaddr(a) for a in email_addresses]
        to_emails = [email_addr for (_, email_addr) in to_emails]

        s = smtplib.SMTP('localhost')
        s.sendmail('no-reply@khanacademy.org', to_emails, msg.as_string())
        s.quit()

    def _send_to_email(self, email_addresses, subject, cc=None, bcc=None):
        """An internal routine; email_addresses must be full addresses."""
        # Try sending to appengine first.
        try:
            self._send_to_gae_email(email_addresses, subject, cc, bcc)
            return
        except (NameError, AssertionError), why:
            pass

        # Try using local smtp.
        try:
            self._send_to_sendmail(email_addresses, subject, cc, bcc)
            return
        except (NameError, smtplib.SMTPException), why:
            pass

        logging.error('Failed sending email: %s' % why)

    def send_to_email(self, email_usernames, subject, cc=None, bcc=None):
        """Send the message to a khan academy email account.

        (We *could* send emails outside ka.org, but right now the API
        doesn't allow it just because we haven't needed it.  We could
        consider loosening this restriction if there's a need, but
        better would be to create a new API endpoint like PagerDuty
        does.)

        Arguments:
            email_usernames: an username to send the email to, or
                else a list of such usernames.  This is the mail
                username: so foo in 'foo@khanacademy.org'.  You do
                not need to specify 'khanacademy.org'.
            subject: the subject line of the email.
            cc / bcc: who to cc/bcc on the email.  Takes usernames as
                a string or list, same as email_usernames.
        """
        def _normalize(lst):
            if lst is None:
                return None
            if isinstance(lst, basestring):
                lst = list(lst)
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

        self._send_to_email(email_addresses, subject, cc, bcc)

    def send_to_pagerduty(self, pagerduty_servicenames, summary=''):
        """Send an incident report to PagerDuty.

        Arguments:
            pagerduty_servicenames: either a string, or a list of
                 strings, that are the names of PagerDuty services.
                 https://www.pagerduty.com/docs/guides/email-integration-guide/
            subject: A short summary of the incident.
        """
        def _service_name_to_email(lst):
            if isinstance(lst, basestring):
                lst = list(lst)
            for i in xrange(len(lst)):
                if '@' in lst[i]:
                    raise ValueError('Specify PagerDuty service names, '
                                     'not addresses (%s)' % lst[i])
                # Convert from a service name to an email address.
                lst[i] = _PAGERDUTY_ILLEGAL_CHARS.sub('', lst[i]).lower()
                lst[i] += '@khan-academy.pagerduty.com'
            return lst

        if not summary:
            # Use the first 60 chars, or first sentence, as the summary
            summary = self.message.splitlines()[0][:60]
            if '.' in summary:
                summary = summary[:summary.find('.')]

        email_addresses = _service_name_to_email(pagerduty_servicenames)
        self._send_to_email(email_addresses, summary)

    def send_to_logs(self, priority):
        """Send to logs: either GAE logs (for appengine) or syslog.

        Arguments:
            priority: one of logging.DEBUG, logging.INFO, logging.WARNING,
                logging.ERROR, or logging.CRITICAL.
        """
        logging.log(priority, self.message)

        # Also send to syslog if we can.
        try:
            log_priority_to_syslog_priority = {
                logging.DEBUG: syslog.LOG_DEBUG,
                logging.INFO: syslog.LOG_INFO,
                logging.WARNING: syslog.LOG_WARNING,
                logging.ERROR: syslog.LOG_ERR,
                logging.CRITICAL: syslog.LOG_CRIT
                }
            try:
                syslog_priority = log_priority_to_syslog_priority[priority]
            except KeyError:
                raise ValueError('priority must be one of the logging levels.')
            syslog.syslog(self.message, syslog_priority)
        except NameError:
            pass
