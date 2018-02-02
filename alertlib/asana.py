"""Mixin for send_to_asana().

This Mixin should now only be accessed via the send_to_bugtracker() Mixin
rather than directly by callers in our codebase. Any changes to this
Mixin's interface need to be in sync with the interface of bugtracker.

TODO(jacqueline): If KA ever shifts back over to Asana, we'll need to
add a BUGTRACKER_TO_ASANA_PROJ mapper to account for nuances in Asana
project names.
"""

from __future__ import absolute_import
import json
import logging
import six

from . import base


# Obtained via app.asana.com/api/1.0/workspaces/
KA_ASANA_WORKSPACE_ID = 1120786379245

_LOG_PRIORITY_TO_ASANA_TAG = {
    logging.INFO: 'P4',
    logging.WARNING: 'P3',
    logging.ERROR: 'P2',
    logging.CRITICAL: 'P1'
}


# Map string Asana project names to a list of int Asana project ids
_CACHED_ASANA_PROJECT_MAP = {}

# Map string Asana tag names to a list of int Asana tag ids
# For some reason tag names and project names are not necessarily unique in
# Asana
_CACHED_ASANA_TAG_MAP = {}

# Map string Asana user email address to int Asana user id
_CACHED_ASANA_USER_MAP = {}


def _call_asana_api(req_url_path, post_dict=None):
    """GET from Asana API if no post_dict, otherwise POST post_dict.

    Returns the `data` field of the response content from Asana API
    on success. Returns None on error.
    """
    asana_api_token = base.secret('asana_api_token')
    if not asana_api_token:
        logging.warning("Not sending this to asana (no token found): %s"
                        % post_dict)
        return None

    host_url = 'https://app.asana.com'
    req = six.moves.urllib.request.Request(host_url + req_url_path)
    req.add_header('Authorization', 'Bearer %s' % asana_api_token)

    if post_dict is not None:
        post_dict_copy = {'data': {}}
        for (k, v) in post_dict['data'].items():
            post_dict_copy['data'][k] = base.handle_encoding(v)
        req.add_header("Content-Type", "application/json")
        post_dict = json.dumps(post_dict_copy, sort_keys=True,
                               ensure_ascii=False).encode('utf-8')

    try:
        res = six.moves.urllib.request.urlopen(req, post_dict)
    except Exception as e:
        if isinstance(post_dict, six.binary_type):
            post_dict = post_dict.decode('utf-8')
        logging.error('Failed sending %s to asana because of %s'
                      % (post_dict, e))
        return None
    if res.getcode() >= 300:
        if isinstance(post_dict, six.binary_type):
            post_dict = post_dict.decode('utf-8')
        logging.error('Failed sending %s to asana with code %d'
                      % (post_dict, res.getcode()))
        return None
    return json.loads(res.read())['data']


def _check_task_already_exists(post_dict):
    """Check whether the new task in post_dict is already in Asana.

    Gets all tasks with tag matching the last tag in post_dict (which)
    should always be the "Auto generated" tag. Check if the name of the new
    task in post_dict matches the name of any previously Auto generated,
    unfinished task in Asana and return True if it does.

    NOTE(alexanderforsyth) this will fail if there are more than about
    1000 current tasks (not closed) with tag "Auto generated".
    TODO(alexanderforsyth) add support for more than 1000 tasks via
    pagination?

    NOTE(alexanderforsyth) upon failure for any reason including the above,
    this returns False meaning that a duplicate task might be created.
    """
    req_url_path = ('/api/1.0/tasks?tag=%s'
                    '&opt_fields=completed,name'
                    % post_dict['data']['tags'][-1])

    res = _call_asana_api(req_url_path)
    if res is None:
        logging.warning('Failed testing current task for uniqueness. '
                        'This task might be created as a duplicate.')
        return False

    # check whether any current Auto generated tasks have the same name
    for item in res:
        is_same = item['name'] == post_dict['data']['name']
        if is_same and not item['completed']:
            return True
    return False


def _get_asana_user_ids(user_emails, workspace):
    """Returns the list of asana user ids for the given user emails.

    If _CACHED_ASANA_USER_MAP, this looks up the user_ids for each
    user_email via the cache and returns them, not adding a user_id
    for any invalid user_email. If the cache is empty, this builds the
    asana user cache. Returns [] if there is an error building the cache.

    After a cache miss, this looks up all user email to user id mappings
    from the Asana API and caches the result.
    """
    if not _CACHED_ASANA_USER_MAP:
        req_url_path = ('/api/1.0/users?workspace=%s&opt_fields=id,email'
                        % str(workspace))
        res = _call_asana_api(req_url_path)
        if res is None:
            logging.error('Failed to build Asana user cache. Fields '
                          'involving user IDs such as followers might'
                          'not be included in this task.')
            return []

        for user_item in res:
            user_email = user_item['email']
            user_id = int(user_item['id'])
            _CACHED_ASANA_USER_MAP[user_email] = user_id

    asana_user_ids = []
    for user_email in user_emails:
        if user_email in _CACHED_ASANA_USER_MAP:
            asana_user_ids.append(_CACHED_ASANA_USER_MAP[user_email])
        else:
            logging.error('Invalid asana user email: %s; '
                          'Fields involving this user such as follower '
                          'will not added to task.' % user_email)
    return asana_user_ids


def _get_asana_tag_ids(tag_names, workspace):
    """Returns the list of tag ids for the given tag names.

    If _CACHED_ASANA_TAG_MAP, this looks up the tag_ids for each tag_name
    via the cache and returns them, not adding a tag_id for any invalid
    tag_name. If the cache is empty, this builds the asana tags cache.
    Returns None if there is an error building the cache.

    This looks up all tag name to tag id mappings from the Asana
    API and caches the result. Note that names do not necessarily uniquely
    map to an id so multiple ids can be returned for each tag.
    """
    if not _CACHED_ASANA_TAG_MAP:
        req_url_path = ('/api/1.0/tags?workspace=%s'
                        % str(workspace))
        res = _call_asana_api(req_url_path)
        if res is None:
            logging.error('Failed to build Asana tags cache. '
                          'Task will not be created')
            return None

        for tag_item in res:
            tag_name = tag_item['name']
            tag_id = int(tag_item['id'])
            if tag_name not in _CACHED_ASANA_TAG_MAP:
                _CACHED_ASANA_TAG_MAP[tag_name] = []
            _CACHED_ASANA_TAG_MAP[tag_name].append(tag_id)

    asana_tag_ids = []
    for tag_name in tag_names:
        if tag_name in _CACHED_ASANA_TAG_MAP:
            asana_tag_ids.extend(_CACHED_ASANA_TAG_MAP[tag_name])
        else:
            logging.error('Invalid asana tag name: %s; '
                          'tag not added to task.' % tag_name)
    return asana_tag_ids


def _get_asana_project_ids(project_name, workspace):
    """Returns the list of project ids for the given project name.

    If _CACHED_ASANA_PROJECT_MAP, this looks up the project_ids for
    project_name in the cache and returns it (None if not found). If the
    cache is empty, this builds the asana projects cache.

    This looks up all project name to project id mappings from the Asana
    API and caches the result. Note that names do not necessarily uniquely
    map to an id so multiple ids can be returned.
    """
    if _CACHED_ASANA_PROJECT_MAP:
        return _CACHED_ASANA_PROJECT_MAP.get(project_name)

    req_url_path = ('/api/1.0/projects?workspace=%s'
                    % str(workspace))

    res = _call_asana_api(req_url_path)
    if res is None:
        return None

    for project_item in res:
        p_name = project_item['name']
        p_id = int(project_item['id'])
        if p_name not in _CACHED_ASANA_PROJECT_MAP:
            _CACHED_ASANA_PROJECT_MAP[p_name] = []
        _CACHED_ASANA_PROJECT_MAP[p_name].append(p_id)

    return _CACHED_ASANA_PROJECT_MAP.get(project_name)


class Mixin(base.BaseMixin):
    """Mixin that provides send_to_asana()."""
    def send_to_asana(self,
                      project,
                      tags=None,
                      workspace=KA_ASANA_WORKSPACE_ID,
                      followers=None):
        """Automatically create an Asana task for the alert.

        Arguments (reference asana.com/developers/api-reference/tasks#create):

            project: string name of the project for this task.
                The project name must be a valid name of a project in
                workspace on Asana. If it is not a correct name, the task will
                not be created, but no exception will be raised.
                Example: 'Engineering support'

            tags: list of string names of tags for this task.
                Each tag name should be a valid, existing tag name in
                workspace on Asana. If one is not a valid name, it will not
                be added to the list of tags, and no exceptions will be thrown.
                The "Auto generated" tag is always added to the end of the
                supplied list (and should not be included in this parameter).
                A P# tag will be added to this task if severity is included in
                this Alert and no P# tag is given in the supplied tags list.
                Example: ['P3', 'quick']

            workspace: int id of the workspace for the task to be posted in.
                Default 1120786379245 for khanacademy.org

            followers: array of asana user email addresses of followers for
                this task. Note that a user's email in Asana is not always
                ka_username@khanacademy.org, and should be verified by looking
                up that user in the Asana search bar.
                Default: []

        The Alert message should ideally contain where the alert is coming
        from. E.g. it could include "Top daily error from error-monitor-db"
        """

        if not self._passed_rate_limit('asana'):
            return self

        followers = followers or []
        tags = tags or []

        # check that user specified no P# values before adding alert severity
        # level P# tag
        # existing_p_tags is the set of current P# tags where P# is a value in
        # _LOG_PRIORITY_TO_ASANA_TAG
        p_tags = set(_LOG_PRIORITY_TO_ASANA_TAG.values())
        existing_p_tags = set(tags).intersection(p_tags)

        severity_tag_name = _LOG_PRIORITY_TO_ASANA_TAG.get(self.severity)
        if severity_tag_name and not existing_p_tags:
            tags.append(severity_tag_name)

        # auto-generated is always the last tag
        tags.append('Auto generated')

        task_name = self._get_summary() or ('New Auto generated Asana task')
        # Task names ending with ':' become a section heading; so, remove it
        if task_name.endswith(':'):
            task_name = task_name[:-1]

        asana_project_ids = _get_asana_project_ids(project, workspace)
        if not asana_project_ids:
            logging.error('Invalid asana project name; task not created.')
            return self

        asana_follower_ids = _get_asana_user_ids(followers, workspace)

        asana_tag_ids = _get_asana_tag_ids(tags, workspace)
        if asana_tag_ids is None:
            logging.error('Failed to retrieve asana tag name to tag id'
                          ' mapping. Task will not be created.')
            return self

        post_dict = {'data': {
                     'followers': asana_follower_ids,
                     'name': task_name,
                     'notes': self.message,
                     'projects': asana_project_ids,
                     'tags': asana_tag_ids,
                     'workspace': workspace}}

        if self._in_test_mode():
            logging.info('alertlib: would send to asana: %s'
                         % json.dumps(post_dict))
        else:
            # check that the post_dict task does not already exist
            if not _check_task_already_exists(post_dict):
                req_url_path = '/api/1.0/tasks'
                _call_asana_api(req_url_path, post_dict)

        return self
