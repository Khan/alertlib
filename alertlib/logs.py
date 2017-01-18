"""Mixin for send_to_logs().

On Google AppEngine, this sends to the appengine logs.  Otherwise
(e.g. on ec2 machines), it tries to send to syslog.  We always
log using the python logging mechanism.
"""

from __future__ import absolute_import
import logging

try:
    import syslog
    _LOG_TO_SYSLOG = {
        logging.DEBUG: syslog.LOG_DEBUG,
        logging.INFO: syslog.LOG_INFO,
        logging.WARNING: syslog.LOG_WARNING,
        logging.ERROR: syslog.LOG_ERR,
        logging.CRITICAL: syslog.LOG_CRIT
    }
except ImportError:
    _LOG_TO_SYSLOG = {}

from . import base


class Mixin(base.BaseMixin):
    """A mixin for send_to_logs()."""
    def send_to_logs(self):
        """Send to logs: either GAE logs (for appengine) or syslog."""
        if not self._passed_rate_limit('logs'):
            return self

        logging.log(self.severity, self.message)

        # Also send to syslog if we can.
        if not self._in_test_mode():
            try:
                syslog_priority = self._mapped_severity(_LOG_TO_SYSLOG)
                syslog.syslog(syslog_priority,
                              base.handle_encoding(self.message))
            except (NameError, KeyError):
                pass

        return self
