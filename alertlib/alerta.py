"""Mixin for send_to_alerta().

This mixin sends alerts to Alerta--a monitoring system used to consolidate
and de-duplicate alerts from multiple sources. It should be called from any
cron jobs or various monitoring tools only when something fails--with the 
goal of providing the Alerta dashboard with a snapshop of what's broken at
any given time. With KA permissions, this dashboard can be viewed on
https://alerta.khanacademy.org.

Within the cron job or other origin, this mixin must be called using not
only --aggregator, but also the additional args --aggregator-resource and
--aggregator-event-name. All are required in order for an alert to be
sent through successfully.
"""

from __future__ import absolute_import
import json
import logging
import six

from . import base


_BASE_ALERTA_API_URL = "https://alerta.khanacademy.org/api"

# Any new resources that will be originating alerts should be 
# added to the MAP_RESOURCE_TO_ENV_SERVICE_AND_GROUP below
# with their corresponding environment, service, and group. 
# The following are the current classification choices:
# NOTE: this is a wip, please build these out!
#   Environment = 'Production', 'Development'
#   (These environment options are fixed; call will be rejected by
#   Alerta w/403 if not in this list)
#   Service = 'Khanacademy.org', 'deployment'
#   Group = 'web', 'mobile'

_ENV_PROD = 'Production'
_ENV_DEV = 'Development'
_SERVICE_KA = 'Khanacademy.org'
_SERVICE_DEPLOY = 'Deployment'
_SERVICE_WEBSERVER = 'Internal Webserver'
_SERVICE_TEST = 'Test'
_GROUP_WEB = 'web'
_GROUP_MOBILE = 'mobile'
_GROUP_TOOLS = 'tools'
_GROUP_TEST = 'test'

# If resources are added to this list, please update --aggregator-resource
# help string in alert.py to reflect new valid resources to select from.
MAP_RESOURCE_TO_ENV_SERVICE_AND_GROUP = {
    'webapp': {'env': _ENV_PROD,
               'service': [_SERVICE_KA],
               'group': _GROUP_WEB,
               },
    'mobile': {'env': _ENV_PROD,
               'service': [_SERVICE_KA],
               'group': _GROUP_MOBILE,
               },
    'jenkins': {'env': _ENV_DEV,
                'service': [_SERVICE_DEPLOY],
                'group': _GROUP_TOOLS,
                },
    'toby': {'env': _ENV_PROD,
             'service': [_SERVICE_WEBSERVER],
             'group': _GROUP_TOOLS,
             },
    'test': {'env': _ENV_DEV,
             'service': [_SERVICE_TEST],
             'group': _GROUP_TEST,
             }
}

_SEVERITY_TO_ALERTA_FORMAT = {
    logging.CRITICAL: 'critical',
    logging.ERROR: 'major',
    logging.WARNING: 'warning',
    logging.INFO: 'informational',
    logging.DEBUG: 'debug',
    logging.NOTSET: 'unknown',  # Should not be used if avoidable
}


def _make_alerta_api_call(payload_json):
    # This is a separate function just to make it easy to mock for tests.
    alerta_api_key = base.secret('alerta_api_key')
    req = six.moves.urllib.request.Request(_BASE_ALERTA_API_URL + '/alert')
    req.add_header('Authorization', 'Key %s' % alerta_api_key)
    req.add_header('Content-Type', 'application/json')
    res = six.moves.urllib.request.urlopen(req, payload_json.encode('utf-8'))

    # 202 is Alerta's response code during a planned blackout period
    if res.getcode() not in [201, 202]:
        raise ValueError(res.read())


def _post_to_alerta(payload_json):
    """Makes POST request to alerta API. """

    if not base.secret('alerta_api_key'):
        logging.warning("Not sending to Alerta (no API key found): %s",
                        payload_json)
        return

    try:
        _make_alerta_api_call(payload_json)
    except Exception as e:
        logging.error("Failed sending %s to Alerta: %s"
                      % (payload_json, e))


class Mixin(base.BaseMixin):
    """Mixin for send_to_alerta()."""

    def send_to_alerta(self,
                       initiative,
                       resource=None,
                       event=None):
        """Sends alert to Alerta.

        This is intended to be used for more urgent 'things are broken'
        alerts. In the case of KA, Alerta (alerta.io) is serving the purpose of 
        aggregating alerts from multiple sources. The API used to interface 
        with dashboard can be accessed via endpoints found on api.alerta.io/.

        Arguments:
            initiative: Value to be included in the attributes dictionary of
                custom key-value pairs. This value should correspond to the
                relevant initiative that needs to pay attention to the alert.
                e.g. 'infrastructure' or 'independent-learning'
            resource: Which resource is under alarm. Given this resource where
                the alert originated, aggregator.py does a static lookup for
                corresponding environment, service, and group.
                e.g. 'webapp' or 'jenkins'
            event: Event name
                e.g. 'ServiceDown' or 'Errors'
        """

        if not self._passed_rate_limit('aggregator'):
            return self

        if resource is None:
            logging.error('Resource must be provided. '
                          'Failed to send to aggregator.')
            return self

        if event is None:
            logging.error('Event name must be provided. '
                          'Failed to send to aggregator.')
            return self

        resource_classifiers = MAP_RESOURCE_TO_ENV_SERVICE_AND_GROUP[resource]
        environment = resource_classifiers['env'].capitalize()
        service = resource_classifiers['service']
        group = resource_classifiers['group']
        severity = _SEVERITY_TO_ALERTA_FORMAT[self.severity]
        text = self._get_summary().encode('utf-8')
        # additional custom key: value pairs should be added to attributes
        attributes = {"initiative": initiative}

        payload = {"resource": resource,
                   "event": event,
                   "environment": environment, 
                   "severity": severity,
                   "service": service,
                   "group": group,
                   "text": text,
                   "attributes": attributes,
                   }

        payload_json = json.dumps(payload)

        if self._in_test_mode():
            logging.info("alertlib: would send to aggregator: %s"
                         % (payload_json))
        else:
            _post_to_alerta(payload_json)

        return self      # so we can chain the method calls


