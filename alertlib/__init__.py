"""Library for alerting various backends from within an app.

The goal of alert-lib is to make it easy to send alerts when
appropriate from your code.  For instance, you might send an alert to
hipchat + email when your long-running task is done, or an alert to
pagerduty when your long-running task fails.

USAGE:
   alertlib.Alert("message")
       .send_to_hipchat(...)
       .send_to_email(...)
       .send_to_pagerduty(...)
       .send_to_logs(...)
       .send_to_graphite(...)
       .send_to_alerta(...)

or, if you don't like chaining:
   alert = alertlib.Alert("message")
   alert.send_to_hipchat(...)
   alert.send_to_email(...)
   [etc]


The backends supported are:

    * KA HipChat room
    * KA Slack channel
    * KA Asana tasks
    * KA email
    * KA PagerDuty account
    * KA Alerta account
    * Logs -- GAE logs on appengine, or syslogs on a unix box
    * Graphite -- update a counter to indicate this alert happened

You can send an alert to one or more of these.

Some advice on how to choose:

* Are you alerting about something that needs to be fixed?  Send it to
  PagerDuty, which keeps track of whether a problem is fixed or not,
  and send it to Alerta, which compiles a dashboard of what's broken.

* Are you alerting about something you want people to know about right
  away?  Send it to an email role account that forward to those
  people, send a HipChat/Slack message that mentions those people
  with @name, and post it to Alerta so it makes it to the dashboard.

* Are you alerting about something that is nice-to-know?  ("Regular
  cron task X has finished" often falls into this category.)  Send it
  to HipChat/Slack..

When sending to email, we try using both google appengine (for when
you're using this within an appengine app) and sendmail.
"""

from .base import enter_test_mode, exit_test_mode, BaseMixin

# These each define a mixin that we incorporate to the Alert object
# to define send_to_foo().
from . import hipchat        # send_to_hipchat()
from . import slack          # send_to_slack()
from . import asana          # send_to_asana()
from . import email          # send_to_email()
from . import pagerduty      # send_to_pagerduty()
from . import logs           # send_to_logs()
from . import graphite       # send_to_graphite()
from . import stackdriver    # send_to_stackdriver()
from . import alerta         # send_to_alerta()
from . import bugtracker     # send_to_bugtracker()
from . import jira           # _send_to_jira()


class Alert(hipchat.Mixin,
            slack.Mixin,
            asana.Mixin,
            email.Mixin,
            pagerduty.Mixin,
            logs.Mixin,
            graphite.Mixin,
            stackdriver.Mixin,
            alerta.Mixin,
            jira.Mixin,
            bugtracker.Mixin,
            BaseMixin):
    """An alert message that can be sent to multiple destinations."""
    # BaseMixin defines __init__.
    pass


__all__ = [
    'enter_test_mode',     # defined in base.py
    'exit_test_mode',      # defined in base.py
    'Alert'
]
