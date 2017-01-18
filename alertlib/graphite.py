"""Mixin for send_to_graphite()."""

from __future__ import absolute_import
import logging
import socket
import time

from . import base


DEFAULT_GRAPHITE_HOST = 'carbon.hostedgraphite.com:2003'

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


class Mixin(base.BaseMixin):
    """Mixin for send_to_graphite().

    For send_to_graphite(), the message and summary -- in fact, all
    the arguments to __init__ -- are ignored.  Only the values passed
    in to send_to_graphite() matter.
    """
    def send_to_graphite(self, statistic, value=1,
                         graphite_host=DEFAULT_GRAPHITE_HOST):
        """Increment the given counter on a graphite/statds instance.

        statistic should be a dotted name as used by graphite: e.g.
        myapp.stats.num_failures.  When send_to_graphite() is called,
        we send the given value for that statistic to graphite.
        """
        if not self._passed_rate_limit('graphite'):
            return self

        hostedgraphite_api_key = base.secret('hostedgraphite_api_key')

        # If the value is 12.0, send it as 12, not 12.0
        if int(value) == value:
            value = int(value)

        if self._in_test_mode():
            logging.info("alertlib: would send to graphite: %s %s"
                         % (statistic, value))
        elif not hostedgraphite_api_key:
            logging.warning("Not sending to graphite; no API key found: %s %s"
                            % (statistic, value))
        else:
            try:
                _graphite_socket(graphite_host).send('%s.%s %s\n' % (
                    hostedgraphite_api_key, statistic, value))
            except Exception as why:
                logging.error('Failed sending to graphite: %s' % why)

        return self
