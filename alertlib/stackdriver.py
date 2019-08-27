"""Mixin for send_to_stackdriver()."""

from __future__ import absolute_import
import json
import six.moves.http_client
import logging
import re
import socket
import time

stackdriver_not_allowed = None
try:
    import httplib2
    import apiclient.discovery
    import oauth2client.client
    import oauth2client.service_account
    # Work around https://github.com/tr2000/google-api-python-client/issues/225
    logging.getLogger("oauth2client.util").addHandler(logging.StreamHandler())
    logging.getLogger("oauth2client.util").setLevel(logging.ERROR)
except ImportError:
    stackdriver_not_allowed = (
            "ImportError occurred. Did you install the required libraries?"
            " Take a look at the README for details. You may need to run"
            " `pip install httplib2 oauth2client google-api-python-client`")

from . import base


DEFAULT_STACKDRIVER_PROJECT = 'khan-academy'
DEFAULT_STACKDRIVER_VALUE = 1

_GOOGLE_API_CLIENT = None


def _get_google_apiclient(google_creds):
    """Build an http client authenticated with service account credentials.

    We support both the ancient (1.5-era) oauth2client, and the more
    modern one.
    """
    global _GOOGLE_API_CLIENT
    if _GOOGLE_API_CLIENT is None:
        try:
            creds = (oauth2client.service_account.ServiceAccountCredentials.
                     from_json_keyfile_dict(
                         google_creds,
                         ['https://www.googleapis.com/auth/monitoring']))
        except AttributeError:
            # Perhaps it's an old oauth2client, which doesn't support
            # from_json_keyfile_dict.
            creds = oauth2client.client.SignedJwtAssertionCredentials(
                google_creds['client_email'], google_creds['private_key'],
                'https://www.googleapis.com/auth/monitoring')
        http = creds.authorize(httplib2.Http())
        _GOOGLE_API_CLIENT = apiclient.discovery.build(
                serviceName='monitoring',
                version='v3', http=http)
    return _GOOGLE_API_CLIENT


def _call_stackdriver_with_retries(fn, num_retries=9, wait_time=0.5):
    """Run fn (a network command) up to 9 times for non-fatal errors."""
    for i in range(num_retries + 1):     # the last time, we re-raise
        try:
            return fn()
        except (socket.error, six.moves.http_client.HTTPException,
                oauth2client.client.Error):
            if i == num_retries:
                raise
            pass
        except apiclient.errors.HttpError as e:
            if i == num_retries:
                raise
            code = int(e.resp['status'])
            if (code == 500 and
                'One or more of the points specified was older than the most '
                    'recent stored point' in str(e)):
                # This can happen when writing data that has already been
                # written. In practice this occurs when retrying after there
                # is an internal error in stackdriver when uploading data. Some
                # of the data is written during the failed request, so we get
                # an error when retrying with the same data.
                return

            elif code == 403 or code >= 500:     # 403: rate-limiting probably
                pass
            elif (code == 400 and
                  'Timeseries data must be more recent' in str(e)):
                # This error just means we uploaded the same data
                # twice by accident (probably because the first time
                # the connection to google died before we got their ACK).
                # We just pretend the call magically succeeded.
                return
            elif (code == 400 and
                  'One or more TimeSeries could not be written' in str(e)):
                # This can happen if the timestamp that we give is a little
                # bit in the future according to google (due to clock skew?)
                # We just wait a little bit and try again.
                pass
            else:
                raise
        time.sleep(wait_time)     # wait a bit before the next request


def _get_custom_metric_name(name):
    """Make a metric suitable for sending to Cloud Monitoring's API.

    Metric names must not exceed 100 characters.

    For now we limit to alphanumeric plus dot and underscore. Invalid
    characters are converted to underscores.
    """
    # Don't guess at automatic truncation. Let the caller decide.
    prefix = 'custom.googleapis.com/'
    maxlen = 100 - len(prefix)
    if len(name) > maxlen:
        raise ValueError('Metric name too long: %d (limit %d): %s'
                         % (len(name), maxlen, name))
    return ('%s%s' % (prefix, re.sub(r'[^\w_.]', '_', name)))


def _get_timeseries_data(metric_name, metric_labels,
                         monitored_resource_type,
                         monitored_resource_labels,
                         value, when):
    # Datetime formatted per RFC 3339.
    if when is None:
        when = time.time()
    time_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))

    name = _get_custom_metric_name(metric_name)
    timeseries_data = {
        "metric": {
            "type": name,
        },
        "points": [
            {
                "interval": {
                    "startTime": time_str,
                    "endTime": time_str,
                },
                "value": {
                    "doubleValue": value,
                }
            }
        ]
    }
    if metric_labels:
        timeseries_data["metric"]["labels"] = metric_labels
    if monitored_resource_type:
        timeseries_data["resource"] = {"type": monitored_resource_type,
                                       "labels": monitored_resource_labels}
    return timeseries_data


def send_datapoints_to_stackdriver(timeseries_data,
                                   project=DEFAULT_STACKDRIVER_PROJECT,
                                   ignore_errors=True):
    """A low-level function used by send_to_stackdriver."""
    # This is mostly a separate function just to make it easy to
    # mock for tests.  But we also expose it as part of the public API
    # to make it possible (via complicated mocking) to send multiple
    # stats to stackdriver at the same time.

    if stackdriver_not_allowed:
        logging.error("Unable to send to stackdriver: %s",
                      stackdriver_not_allowed)

    google_creds = base.secret('google_alertlib_service_account') or '{}'
    google_creds = json.loads(google_creds, strict=False)
    client = _get_google_apiclient(google_creds)

    project_resource = "projects/%s" % project

    request = client.projects().timeSeries().create(
        name=project_resource, body={"timeSeries": timeseries_data})

    try:
        _call_stackdriver_with_retries(request.execute)
    except Exception as e:
        # cloud-monitoring API seems to put more content
        # in 'content'.
        if hasattr(e, 'content') and hasattr(request, 'to_json'):
            request_text = str(request.to_json())
            # Get rid of any auth info in the request.
            request_text = re.sub(r'"Authorization":[^,]*,\s*',
                                  '"Authorization": "xxxxxx", ',
                                  request_text)
            msg = ('CLOUD-MONITORING ERROR sending %s: %s'
                   % (request_text, e.content))
        else:
            msg = 'cloud-monitoring error, not sending some data'
        if ignore_errors:
            logging.warning(msg)
        else:
            logging.error(msg)
            raise


class Mixin(base.BaseMixin):
    """Mixin for send_to_stackdriver().

    For send_to_stackdriver(), the message and summary -- in fact, all
    the arguments to __init__ -- are ignored.  Only the values passed
    in to send_to_stackdriver() matter.
    """
    def send_to_stackdriver(self,
                            metric_name,
                            value=DEFAULT_STACKDRIVER_VALUE,
                            metric_labels={},
                            monitored_resource_type=None,
                            monitored_resource_labels={},
                            project=DEFAULT_STACKDRIVER_PROJECT,
                            when=None,
                            ignore_errors=True):
        """Send a new datapoint for the given metric to stackdriver.

        Metric names should be a dotted name as used by stackdriver:
        e.g.  myapp.stats.num_failures.  Metric labels should be a
        list of label-name/label-value pairs, e.g. {'lang': 'en',
        'country': 'br'}.  To understand the difference between metric
        names and metric labels, see:
           https://cloud.google.com/monitoring/api/v3/metrics#concepts
        One way to think of it is that when exploring metrics in
        stackdriver, each metric-name will correspond to a graph, and
        each label-value (e.g. ['en, 'br']) will be a line in the graph.

        When send_to_stackdriver() is called, we add a datapoint for
        the given metric name+labels, with the given value and the
        current timestamp.

        Stackdriver also has a concept of monitored-resource names and
        labels, which you can use if they apply to you, but which you
        will normally leave at the default.  See
           https://cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.monitoredResourceDescriptors/list#try-it
        to see all the monitored-resources available.

        If when is not None, it should be a time_t specifying the
        time associated with the datapoint.  If None, we use now.

        If ignore_errors is True (the default), we silently fail if we
        can't send to stackdriver for some reason.  This mimics the
        behavior of send_to_graphite, which never notices errors since
        the graphite data is sent via UDP.  Since we use HTTP to send
        the data, we *can* (optionally) notice errors.
        """
        if not self._passed_rate_limit('stackdriver'):
            return self

        timeseries_data = _get_timeseries_data(
            metric_name, metric_labels,
            monitored_resource_type, monitored_resource_labels,
            value, when)
        if self._in_test_mode():
            logging.info("alertlib: would send to stackdriver: "
                         "metric_name: %s, value: %s" % (metric_name, value))
        else:
            send_datapoints_to_stackdriver([timeseries_data], project,
                                           ignore_errors)
        return self

    def send_datapoints_to_stackdriver(self, *args, **kwargs):
        """For backwards compat.  Use the global function for new code."""
        return send_datapoints_to_stackdriver(*args, **kwargs)
