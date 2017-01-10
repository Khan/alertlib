"""Mixin for send_to_pagerduty()."""

from __future__ import absolute_import
import logging
import re

from . import base


# We want to convert a PagerDuty service name to an email address
# using the same rules pager-duty does.  From experimentation it seems
# to ignore everything but a-zA-Z0-9_-., and lowercases all letters.
_PAGERDUTY_ILLEGAL_CHARS = re.compile(r'[^A-Za-z0-9._-]')


# This class must be mixed in with EmailMixin because it sends an email!

class Mixin(base.BaseMixin):
    """Mixin for send_to_pagerduty()."""
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
        if self._in_test_mode():
            logging.info("alertlib: would send %s" % email_contents)
        else:
            try:
                self._send_to_email(email_addresses)
            except Exception, why:
                logging.error('Failed sending %s: %s' % (email_contents, why))

        return self
