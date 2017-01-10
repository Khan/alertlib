"""Mixin for send_to_hipchat()."""

from __future__ import absolute_import
import logging
import time
import urllib
import urllib2

from . import base


_LOG_PRIORITY_TO_HIPCHAT_COLOR = {
    logging.DEBUG: "gray",
    logging.INFO: "purple",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "red",
}


def _make_hipchat_api_call(post_dict_with_secret_token):
    # This is a separate function just to make it easy to mock for tests.
    r = urllib2.urlopen('https://api.hipchat.com/v1/rooms/message',
                        urllib.urlencode(post_dict_with_secret_token))
    if r.getcode() != 200:
        raise ValueError(r.read())


def _post_to_hipchat(post_dict):
    hipchat_token = base.secret('hipchat_alertlib_token')
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
        _make_hipchat_api_call(post_dict_with_secret_token)
    except Exception, why:
        logging.error('Failed sending %s to hipchat: %s'
                      % (post_dict, why))


class Mixin(base.BaseMixin):
    """Defines send_to_hipchat()."""
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
            color = self._mapped_severity(_LOG_PRIORITY_TO_HIPCHAT_COLOR)

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
            if self._in_test_mode():
                logging.info("alertlib: would send to hipchat room %s: %s"
                             % (room_name, self.summary))
            else:
                _post_to_hipchat({
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

        if self._in_test_mode():
            logging.info("alertlib: would send to hipchat room %s: %s"
                         % (room_name, message))
        else:
            _post_to_hipchat({
                'room_id': room_name,
                'from': sender,
                'message': (message if self.html else
                            _nix_bad_emoticons(message)),
                'message_format': 'html' if self.html else 'text',
                'notify': int(notify),
                'color': color})

        return self      # so we can chain the method calls
