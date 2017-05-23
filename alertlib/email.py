"""Mixin for send_to_email().

This automatically sends using Google AppEngine email-sending in an
appengine environment, or sendgrid if the sendgrid secrets are set, or
sendmail otherwise.
"""

from __future__ import absolute_import
import logging
import re
import six

try:
    # We use the simpler name here just to make it easier to mock for tests
    import google.appengine.api.mail as google_mail
except ImportError:
    try:
        import google_mail      # defined by alertlib_test.py
    except ImportError:
        pass

try:
    import sendgrid
except ImportError:
    pass

try:
    import email
    import email.mime.text
    import email.utils
    import smtplib
except ImportError:
    pass

from . import base


def _get_sender(sender):
    sender_addr = 'no-reply'
    if sender:
        # Replace everything that's not alphanumeric with '-'
        sender_addr += '+' + re.sub(r'\W', '-', sender)
    return 'alertlib <%s@khanacademy.org>' % sender_addr


class Mixin(base.BaseMixin):
    """Mixin for send_to_email()."""
    def _send_to_sendgrid(self, message, email_addresses, cc=None, bcc=None,
                          sender=None):
        username = (base.secret('sendgrid_low_priority_username') or
                    base.secret('sendgrid_username'))
        password = (base.secret('sendgrid_low_priority_password') or
                    base.secret('sendgrid_password'))
        assert username and password, "Can't find sendgrid username/password"
        client = sendgrid.SendGridClient(username, password, raise_errors=True)

        # The sendgrid API client auto-utf8-izes to/cc/bcc, but not
        # subject/text.  Shrug.
        msg = sendgrid.Mail(
            subject=self._get_summary().encode('utf-8'),
            to=email_addresses,
            cc=cc,
            bcc=bcc)
        if self.html:
            # TODO(csilvers): convert the html to text for 'body'.
            # (see base.py about using html2text or similar).
            msg.set_text(message.encode('utf-8'))
            msg.set_html(message.encode('utf-8'))
        else:
            msg.set_text(message.encode('utf-8'))
        # Can't be keyword arg because those don't parse "Name <email>"
        # format.
        msg.set_from(_get_sender(sender))

        client.send(msg)

    def _send_to_gae_email(self, message, email_addresses, cc=None, bcc=None,
                           sender=None):
        gae_mail_args = {
            'subject': self._get_summary(),
            'sender': _get_sender(sender),
            'to': email_addresses,      # "x@y" or "Full Name <x@y>"
        }
        if cc:
            gae_mail_args['cc'] = cc
        if bcc:
            gae_mail_args['bcc'] = bcc
        if self.html:
            # TODO(csilvers): convert the html to text for 'body'.
            # (see base.py about using html2text or similar).
            gae_mail_args['body'] = message
            gae_mail_args['html'] = message
        else:
            gae_mail_args['body'] = message
        google_mail.send_mail(**gae_mail_args)

    def _send_to_sendmail(self, message, email_addresses, cc=None, bcc=None,
                          sender=None):
        msg = email.mime.text.MIMEText(base.handle_encoding(message),
                                       'html' if self.html else 'plain')
        msg['Subject'] = base.handle_encoding(self._get_summary())
        msg['From'] = _get_sender(sender)
        msg['To'] = ', '.join(email_addresses)
        # We could pass the priority in the 'Importance' header, but
        # since nobody pays attention to that (and we can't even set
        # that header when sending from appengine), we just use the
        # fact it's embedded in the subject line.
        if cc:
            if not isinstance(cc, six.string_types):
                cc = ', '.join(cc)
            msg['Cc'] = cc
        if bcc:
            if not isinstance(bcc, six.string_types):
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

        # Try using the sendgrid service first.
        try:
            self._send_to_sendgrid(message, email_addresses, cc, bcc, sender)
            return
        except (NameError, AssertionError) as why:
            pass

        # Then try sending via the appengine API.
        try:
            self._send_to_gae_email(message, email_addresses, cc, bcc, sender)
            return
        except (NameError, AssertionError) as why:
            pass

        # Finally, try using local smtp.
        try:
            self._send_to_sendmail(message, email_addresses, cc, bcc, sender)
            return
        except (NameError, smtplib.SMTPException) as why:
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
            if isinstance(lst, six.string_types):
                lst = [lst]
            for i in range(len(lst)):
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
                          % (email_addresses, _get_sender(sender),
                             cc, bcc, self._get_summary(), self.message))
        if self._in_test_mode():
            logging.info("alertlib: would send %s" % email_contents)
        else:
            try:
                self._send_to_email(email_addresses, cc, bcc, sender)
            except Exception as why:
                logging.error('Failed sending %s: %s' % (email_contents, why))

        return self
