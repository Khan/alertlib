alertlib
========

> :speaker: A small library to make it easy to send alerts to various platforms.

This library consists of a Python module that will let you send an
alert to any or all of:

  * HipChat
  * Slack
  * Asana
  * Jira
  * PagerDuty
  * khanacademy.org email lists
  * GAE logs and/or syslog
  * Graphite/StatsD
  * Stackdriver (also known as Google Cloud Monitoring)
  * Alerta (alerting aggregator)

You must provide a decrypted `secrets.py` (from the webapp repo) to use
these services.

## Usage
```python
alert = alertlib.Alert("message")
alert.send_to_*
```

It is good form to specify the severity of the alert, using standard logging
constants, e.g.:

```python
    alertlib.Alert("out of disk space", severity=logging.CRITICAL)
```

For example the above would generate an email subject of `**CRITICAL ERROR**:
out of disk space` and would send to Hipchat (or Slack) with an angry red
background:

<img src='http://f.cl.ly/items/3A0P3Y3w2X1K2Z0t2N37/ss-alertigator.png' width=236>

See the docstrings for `Alert` for the full list of available parameters, such
as rate limiting, etc.


### Chaining destinations

```python
alertlib.Alert("It's time for a walk!")                 \
   .send_to_hipchat("1s and 0s")                        \
   .send_to_slack("#1s-and-0s")                         \
   .send_to_email("dogs-all", cc=["toby","fleetwood"])  \
   .send_to_pagerduty(...)                              \
   .send_to_logs(...)                                   \
   .send_to_graphite(...)                               \
   .send_to_stackdriver(...)                            \
   .send_to_aggregator(...)
```

### Secrets

To send data to these services, you will need to provide a `secrets.py` file
in the following format:

```
hostedgraphite_api_key = "VALUE"
slack_alertlib_api_token = "VALUE"
asana_api_token = "VALUE"
hipchat_alertlib_token = "VALUE"
google_alertlib_service_account = '{}'
sendgrid_username = "VALUE"
sendgrid_password = "VALUE"
alerta_api_key = "VALUE"
```

You only need to include the secrets for the services you are using.  If you set
the environment variable `ALERTLIB_SECRETS_DIR`, alertlib will look for
`secrets.py` in that directory, and will exit if it's not found.

For Slack, we support either `slack_alertlib_api_token`, set to a bot's API
token, or `slack_alertlib_webhook_url`, set to an incoming webhook's secret
URL.  Most functionality may be used with either, but certain features are only
supported with the API token.  For this reason we prefer the API token, if both
are set.

### HTML formatting (for HipChat and email)
Alert messages may contain HTML markup if you set the `html=True` parameter on
the Alert.

This option should not be used -- and your message should not contain
html -- if you are sending the message to Slack, since Slack uses
markdown rather than HTML.  If you want to send a message to slack
and another destination, use two alerts:

```python
alertlib.Alert("Message for <b>HipChat<b>", html=True).send_to_hipchat(...)
alertlib.Alert("Message for *everywhere*").send_to_slack(...).send_to_email(...)
```

Note that a safer multi-destination procedure for the long-term is to use simple
markdown formatting (which looks good even as plain text!), and pass an optional
`Attachment` when posting to Slack to enhance the formatting there if you want
to do something really fancy.


### Formatting messages for Slack
There are three primary ways to format Alert messages for Slack.

#### Default AlertLib style

For the default case, AlertLib will handle message display for you.
The color of the message will be based on the `Alert.severity`.

```python
a1 = alertlib.Alert("""The following dogs completed walks:
    - fleetwood
    - toby
    - fozzie
    - jak
    - betsy""", summary="Dog walk report", severity=logging.INFO)
a1.send_to_slack("#bot-testing", sender="Dog Walker", icon_emoji=":dog:")

a2 = alertlib.Alert("""The following dogs missed their walks:
    - stuart""", summary="Missing dog walk alert", severity=logging.CRITICAL)
a2.send_to_slack("#bot-testing", sender="Dog Walker", icon_emoji=":dog:")
```

<img src='http://f.cl.ly/items/1N1s040t3u1b1q2T1r2s/ss-dogwalks.png' width=363>

See the docstrings for `Alert.send_to_slack()` for a detailed list of available
parameters.

#### Simple Messages
If `simple_message=True` is passed, the message will be passed along to Slack
using simple Markdown formatting, instead of being automatically rendered as the
above default AlertLib style.

```python
alertlib.Alert("It's time for _bread_! *Yummy!*").send_to_slack("#bot-testing",
    sender="Bread Alerts", icon_emoji=":bread:", simple_message=True)
```

<img src='http://f.cl.ly/items/0p2Y273f2q0j2h1z2I0q/ss-bread.png' width=267>

See https://api.slack.com/docs/formatting for more on formatting.

#### Attachments
If you want full control, if an "attachments" dict list is passed, these will be
passed along to Slack to enable very detailed message display parameters.

```python
alertlib.Alert("No one will see this text!").send_to_slack("#bot-testing",
    sender="Science Scout", icon_emoji=":microscope:", attachments=[{
        "fallback": "New experiment results from Fleetwood - Experiment #123: Canine Cuteness - http://ka.org/xp/123",
        "pretext": "New experiment results from Fleetwood",
        "title": "Experiment #123: Canine Cuteness",
        "title_link": "http://ka.org/xp/123",
        "text": "Attempt to verify which dog at Khan Academy is the cutest.",
        "thumb_url": "http://i.imgur.com/NRVOtRI.jpg",
        "color": "good",
        "fields": [{"title": "Project",
                    "value": "Awesome Project",
                    "short": True},
                   {"title": "Environment",
                    "value": "production",
                    "short": True}],
    }])
```
<img src='http://f.cl.ly/items/3q0t0W00120W310C1I19/ss-attachments.png' width=695>

Note that when passing attachments to Slack, AlertLib will by default ignore the
`Alert.message`, on the assumption that you will be providing your entire UI via
the attachment.  This enables you to use a simple message for the Alert and
still chain it to many destinations, but provide a rich interface for the Slack
version! Note you should probably use that simple text version as the `fallback`
field, which is a _mandatory_ field for Slack attachments which provides support
for plaintext IRC/XMPP/etc clients.

See https://api.slack.com/docs/attachments for more attachment details.


### Stackdriver (Google Cloud Monitoring) Support

Alertlib supports sending metrics to stackdriver for graphing and monitoring. As
an example: `alertlib.Alert('error').send_to_stackdriver('metric.name', 10)`
would send a datapoint of 10 to the `metric.name` metric.

Before using this feature, please ensure that the `httplib2`,
`oauth2client`, and `google-api-python-client` packages are installed
before use. These packages are only required for users that call
`send_to_stackdriver`. To install the latest versions of these
packages that are tested with alertlib, run:

`pip install google-api-python-client pyOpenSSL`

THe latest version of google-api-python-client that this code has been
tested with is 1.6.5.

To authenticate with google,
[follow this doc](https://developers.google.com/identity/protocols/OAuth2ServiceAccount)
to create a Service Account and authorize it appropriately. Include the JSON for
the account in `secrets.py` as a multiline string, assigned to
`google_alertlib_service_account`, so that `secrets.py` looks like:

```
google_alertlib_service_account = '''
{
  "type": "service_account",
  "project_id": "PROJECT_ID",
  "private_key_id": "PROJECT_KEY_ID",
  "private_key": "-----BEGIN PRIVATE KEY-----
....
-----END PRIVATE KEY-----
",
  "client_email": "EMAIL",
  "client_id": "ID",
  "auth_uri": "AUTH_URI",
  "token_uri": "TOKEN_URI",
  "auth_provider_x509_cert_url": "CERT_URL",
  "client_x509_cert_url": "CERT_URL"
}
'''
```
