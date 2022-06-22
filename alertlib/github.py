"""Mixin for Github-related status updates."""

from __future__ import absolute_import
import json
import logging
import six

from . import base

_BASE_GITHUB_API_URL = 'https://api.github.com'


def _call_github_api(endpoint, payload_json=None):
    # This is a separate function just to make it easy to mock for tests.
    github_api_key = base.secret('github_repo_status_deployment_pat')
    req = six.moves.urllib.request.Request(_BASE_GITHUB_API_URL + endpoint)
    req.add_header('Authorization', 'token %s' % github_api_key)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    res = six.moves.urllib.request.urlopen(req, payload_json)
    res_json = res.read()

    if res.getcode() >= 300:
        raise ValueError(res.read())
    else:
        return json.loads(res_json)


def _make_github_api_call(endpoint, payload_json=None):
    """Make a GET or POST request to Github API."""
    if not base.secret('github_repo_status_deployment_pat'):
        logging.error("Not sending to Github (no API key found): %s",
                      payload_json)
        return

    try:
        return _call_github_api(endpoint, payload_json)
    except Exception as e:
        logging.error("Failed sending %s to Github: %s"
                      % (payload_json, e))


class Mixin(base.BaseMixin):
    """Mixin for Github API calls."""

    def send_to_github_commit_status(self,
                                     sha,
                                     state=None,
                                     target_url=None,
                                     description=None,
                                     context=None,
                                     owner="Khan",
                                     repo="webapp"):
        """Update the status for a particular commit in Github.

        For more information:
        https://docs.github.com/en/rest/commits/statuses#create-a-commit-status
        """

        if not self._passed_rate_limit('github_commit_status'):
            return self

        # If no state is provided then we attempt to use the severity
        if not state:
            if self.severity == 'error':
                state = 'error'
            elif self.severity == 'critical':
                state = 'failure'
            else:
                state = 'pending'

        payload = {'state': state}

        if target_url:
            payload['target_url'] = target_url
        if description:
            payload['description'] = description
        if context:
            payload['context'] = context

        payload_json = json.dumps(payload, sort_keys=True,
                                  ensure_ascii=False).encode('utf-8')

        if self._in_test_mode():
            logging.info("alertlib: would send to github commit status: %s"
                         % (payload_json))
        else:
            _make_github_api_call(
                '/repos/%s/%s/statuses/%s' % (owner, repo, sha), payload_json)

        return self      # so we can chain the method calls
