"""Mixin for _send_to_jira().

This Mixin should now only be accessed via the send_to_bugtracker() Mixin
rather than directly by callers in our codebase. Any changes to this
Mixin's interface need to be in sync with the interface of bugtracker.
"""

from __future__ import absolute_import
import json
import logging
import six

from . import base

_BASE_JIRA_API_URL = 'https://khanacademy.atlassian.net/rest/api/2'

_SEVERITY_TO_JIRA_PRIORITY = {
    logging.CRITICAL: '2',  # P1
    logging.ERROR: '3',     # P2
    logging.WARNING: '4',   # P3
    logging.INFO: '5',      # P4
    logging.DEBUG: '5',     # P4
    logging.NOTSET: '5',    # Should not be used if avoidable
}

# Associates initiative names with their corresponding Jira project keys
_BUGTRACKER_TO_JIRA_PROJ = {
    "Architecture": "ARCH",
    "Teacher Experience": "CLASS",
    "Content Library": "CL",
    "Content Platform": "CP",
    "Data Infrastructure": "DI",
    "Districts": "DIST",
    "Frontend Infrastructure": "FEI",
    "Tutor Platform": "TUT",
    "Infrastructure": "INFRA",
    "Learning Components": "LC",
    "Marketing & Philanthropy Product": "MPP",
    "MPP": "MPP",

    # These are deprecated and will be removed soon.
    "Classroom": "CLASS",
    "Learning Platform": "LP",
    "Test Prep": "TP",
    "Guided Learning": "GL",
    "Test": "TEST",
}

# Associates a Jira project key with an issue type ID for that project's
# default issue type
_PROJECT_TO_ISSUETYPE_ID = {
    "ARCH": "10201",   # Support
    "CLASS": "10201",  # Support
    "CL": "10201",     # Support
    "CP": "10201",     # Support
    "DI": "10201",     # Support
    "DIST": "10201",   # Support
    "FEI": "10201",    # Support
    "GL": "10201",     # Support
    "INFRA": "10103",  # Bug
    "LC": "10201",     # Support
    "MPP": "10201",    # Support
    "LP": "10201",     # Support
    "TP": "10201",     # Support
    "TEST": "10201",   # Support
    "TUTT": "10201",   # Support
}


def _call_jira_api(endpoint, payload_json=None):
    # This is a separate function just to make it easy to mock for tests.
    jira_api_key = base.secret('jira_api_key')
    req = six.moves.urllib.request.Request(_BASE_JIRA_API_URL + endpoint)
    req.add_header('Authorization', 'Basic %s' % jira_api_key)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    if isinstance(payload_json, str):
        payload_json = payload_json.encode('utf-8')
    res = six.moves.urllib.request.urlopen(req, payload_json)
    res_json = res.read()

    # In the case that we're adding a watcher to an issue, success is
    # indicated by 204, no content in the reseponse
    if res.getcode() == 204:
        return
    # When looking for a username via _get_jira_usernames, Jira API returns 404
    # if there is no user corresponding to that email address in their system
    # This should not cause us to error out, just log that a user wasn't found.
    elif res.getcode() == 404:
        return
    elif res.getcode() >= 300:
        raise ValueError(res.read())
    else:
        return json.loads(res_json)


def _make_jira_api_call(endpoint, payload_json=None):
    """Make a GET or POST request to Jira API."""
    if not base.secret('jira_api_key'):
        logging.error("Not sending to Jira (no API key found): %s",
                      payload_json)
        return

    try:
        return _call_jira_api(endpoint, payload_json)
    except Exception as e:
        logging.error("Failed sending %s to Jira: %s"
                      % (payload_json, e))


def _get_jira_project_keys():
    """Return a list of all project keys."""
    res = _make_jira_api_call('/project') or []
    return [proj['key'] for proj in res]


def _get_jira_usernames(watchers):
    """Return a list of Jira usernames to be added as watchers.

    This takes a list of email addresses as provided from --cc and
    individually queries Jira's user search for the Jira username
    corresponding to that email.
    """
    all_watchers = []
    for watcher in watchers:
        params = six.moves.urllib.parse.urlencode({'username': watcher})
        req_url = '/user/search?%s' % params
        res = _make_jira_api_call(req_url)
        if res:
            all_watchers.append(res[0]['key'])
        else:
            logging.warning('Unable to find a Jira user associated with '
                            'the email address: %s' % watcher)
    return all_watchers


def _check_issue_already_exists(project, summary):
    """Check whether the new issue has already been created in Jira.

    Gets any issues created with the same project + summary combination that
    have not already been resolved. If the API call fails, this will not
    error out but will return False and could potentially result in a dup.
    """
    jql_string = ('project="%s" AND summary~"%s" AND status!="Done"'
                  % (project, summary))
    params = six.moves.urllib.parse.urlencode({'jql': jql_string})
    req_url = '/search?%s' % params
    res = _make_jira_api_call(req_url)

    if res is None:
        logging.error('Failed testing current issue for uniqueness. '
                      'This issue might be created as a duplicate.')
        return False

    return res['total'] > 0


def _format_labels(labels):
    """Remove any spaces in a label name."""
    return ['_'.join(label.split()) for label in labels]


def _add_watchers(issue_key, watchers):
    """Add a list of Jira usernames as watchers to a given issue.

    Jira's /issue endpoint does not support adding a list of watchers
    during issue creation, but rather requires adding the issue first,
    then capturing the newly created issue key and making a separate
    call to issue/<issue_key>/watchers.

    Furthermore, there is a known bug in the formatting of Jira usernames
    when adding them via this endpoint, which requires an extra set of
    quotes. See https://jira.atlassian.com/browse/JRASERVER-29304.
    TODO(jacqueline): Once this Jira Bug has been resolved, we'll need to
    amend this formatting.
    """
    for watcher in watchers:
        watcher = "\"%s\"" % watcher
        _make_jira_api_call('/issue/%s/watchers' % issue_key,
                            "%s" % watcher)


class Mixin(base.BaseMixin):
    """Mixin for _send_to_jira()."""

    def _send_to_jira(self,
                      project_name=None,
                      labels=None,
                      watchers=None):
        """Send alert to Jira.

        This is not intended to be publicly accessible function as all
        send to jira usage should originate with the bugtracker, a wrapper
        which can be modified to redirect to preferred bug tracking systems.
        See bugtracker Mixin for more on its wrapper functionality.

        Arguments:
            project_name: The generic project name, which will be converted
                into a Jira project key, that the alert should be posted to.
                e.g. 'Infrastructure' or 'Test Prep'
            labels: A list of labels to be added to the Jira issue.
                e.g. ['design', 'awaiting_deploy']
            watchers: A list of emails that should be converted to Jira
                usernames and added as watchers on the issue.
                e.g. ['jacqueline@khanacademy.org']
        """

        if not self._passed_rate_limit('jira'):
            return self

        labels = labels or []
        watchers = watchers or []
        project_key = _BUGTRACKER_TO_JIRA_PROJ.get(project_name)

        if project_key is None:
            logging.error('Invalid Jira project name or no name provided. '
                          'Failed to send to Jira.')
            return self
        else:
            all_projects = _get_jira_project_keys()
            if all_projects and project_key not in all_projects:
                logging.error('This is no longer a valid Jira project key. '
                              'The bugtracker to jira project map may need to '
                              'be updated.')
                return self
            elif not all_projects:
                logging.error('Unable to verify Jira project key. This issue '
                              'may not be created successfully.')

        issue_type = _PROJECT_TO_ISSUETYPE_ID[project_key]
        priority = _SEVERITY_TO_JIRA_PRIORITY[self.severity]
        issue_title = self._get_summary() or ('New Auto generated Jira task')
        description = self.message
        labels.append('auto_generated')
        jira_labels = _format_labels(labels)
        jira_watchers = _get_jira_usernames(watchers)

        payload = {"fields": {
                       "project": {"key": project_key},
                       "issuetype": {"id": issue_type},
                       "reporter": {"name": "jirabot"},
                       "priority": {"id": priority},
                       "labels": jira_labels,
                       "summary": issue_title,
                       "description": description,
                      }
                   }

        payload_json = json.dumps(payload, sort_keys=True,
                                  ensure_ascii=False).encode('utf-8')

        if self._in_test_mode():
            logging.info("alertlib: would send to jira: %s"
                         % (payload_json))
        else:
            # check that the issue does not already exist
            if not _check_issue_already_exists(project_key, self.summary):
                r = _make_jira_api_call('/issue', payload_json)
                if jira_watchers:
                    issue_key = r['key']
                    _add_watchers(issue_key, jira_watchers)

        return self      # so we can chain the method calls
