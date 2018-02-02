"""Mixin for send_to_bugtracker()."""

from __future__ import absolute_import

from . import base


class Mixin(base.BaseMixin):
    """Mixin for send_to_bugtracker()."""

    def send_to_bugtracker(self,
                           project_name=None,
                           labels=None,
                           watchers=None):
        """Sends alert to bugtracker.

        This is a wrapper for other bugtracking integrations, such as Asana
        and Jira. This way if bugtracking software is switched to a different
        service, callers in other code bases do not need to be updated, only
        the send_to_bugtracker() mixin needs to be. Future task management
        software integrations should have a uniform interface and be accessible
        through send_to_bugtracker() if possible.

        Arguments:
            project_name (required): The generic project name where alerts
                should be posted to. e.g. 'Infrastructure' or 'Test Prep'
            labels (optional): A list of labels or tags to be added to the
                issue. e.g. ['design', 'awaiting_deploy']
            watchers (optional): A list of email addresses associated with the
                bugtracking account for those that should be added as watchers
                or followers of the issue. e.g. ['jacqueline@khanacademy.org']
        """

        if not self._passed_rate_limit('bugtracker'):
            return self

        self._send_to_jira(project_name, labels, watchers)
