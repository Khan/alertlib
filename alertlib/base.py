"""Defines the "base" mixin that all mixins derive from.

It defines a few helpful utility functions.

To create a new 'contentful' mixin that defines send_to_xx:
1) Create a new file for your new mixin
2) Have it inherit from BaseMixin
3) Include the mixin in Alert in __init__.py.

If possible, your new Mixin class should *ONLY* define send_to_xx.
Everything else should be free functions in your file.  This minimizes
the chance that methods on your mixin will have a name conflict with
methods on another mixin.

If you must have methods on your Mixin (because they need access to
the vars of `self`, try to include the name of your service in the
method name, e.g. `_post_data_to_slack`, not `_post_data`.

See graphite.py for a simple example.
"""

import logging
import os
import six
import sys
import time

# We accept secrets.py either in the current directory, or in another directory
# if this envvar is set.  If the envvar is set, we require that secrets exist;
# otherwise we fall back to running without secrets.
SECRETS_DIR_ENVVAR = 'ALERTLIB_SECRETS_DIR'

try:
    sys.path.insert(0, os.environ.get(SECRETS_DIR_ENVVAR, '.'))
    import secrets
except ImportError:
    # You won't be able to do any alerting that requires a secret
    secrets = None
finally:
    # Clean up the path, so we don't pollute it for others.
    sys.path.pop(0)


_TEST_MODE = False


def enter_test_mode():
    """In test mode, we just log what we'd do, but don't actually do it."""
    global _TEST_MODE
    _TEST_MODE = True


def exit_test_mode():
    """Exit test mode, and resume actually performing operations."""
    global _TEST_MODE
    _TEST_MODE = False


def secret(name):
    """Returns the value for the secret named `name`, or None."""
    return getattr(secrets, name, None)


def handle_encoding(string):
    """There are a some places where unicode is not allowed in python2.x but
    python 3.x handles it fine. This method utf-8 encodes unicode strings
    if in python 2.7 and leaves it as unicode if in python 3.x
    """
    if six.PY2:
        if isinstance(string, unicode):
            return string.encode('utf-8')
        else:
            return string
    elif six.PY3:
        return string


def maybe_from_utf8(string):
    """Convert utf-8 input to unicode, leaving all other input alone."""
    if six.PY2:
        if isinstance(string, str):
            return string.decode('utf-8')
        else:
            return string
    elif six.PY3:
        return string


class BaseMixin(object):
    def __init__(self, message, summary=None, severity=logging.INFO,
                 html=False, rate_limit=None):
        """Create a new Alert.

        The arguments here are things that are common to all alerts.

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

        self.message = maybe_from_utf8(self.message)
        self.summary = maybe_from_utf8(self.summary)

    def _passed_rate_limit(self, service):
        if not self.rate_limit:
            return True
        now = time.time()
        if now - self.last_sent.get(service, -100000) > self.rate_limit:
            self.last_sent[service] = now
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

    def _in_test_mode(self):
        return _TEST_MODE
