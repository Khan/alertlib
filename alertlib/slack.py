"""Mixin for send_to_slack()."""

from __future__ import absolute_import
import json
import logging
import six

from . import base


# 'good'=green, 'warning'=yellow, 'danger'=red, or use hex colors
_LOG_PRIORITY_TO_SLACK_COLOR = {
    logging.DEBUG: "",  # blank = uses default color which is light grayish
    logging.INFO: "",
    logging.WARNING: "warning",
    logging.ERROR: "danger",
    logging.CRITICAL: "danger"
}


def _make_slack_webhook_post(payload_json):
    # This is a separate function just to make it easy to mock for tests.
    webhook = base.secret('slack_alertlib_webhook_url')
    req = six.moves.urllib.request.Request(webhook)
    req.add_header("Content-Type", "application/json")
    res = six.moves.urllib.request.urlopen(req, payload_json.encode('utf-8'))
    if res.getcode() != 200:
        raise ValueError(res.read())


def _post_to_slack(payload_json):
    if not base.secret('slack_alertlib_webhook_url'):
        logging.warning("Not sending to slack (no webhook url found): %s",
                        payload_json)
        return
    try:
        _make_slack_webhook_post(payload_json)
    except Exception as e:
        logging.error("Failed sending %s to slack: %s" % (payload_json, e))


class Mixin(base.BaseMixin):
    """Defines send_to_slack().

    In addition to normal messaging based on the constructor
    (in BaseMixin), send_to_slack() has a bunch of extra parameters
    to control how the message looks on slack.
    """
    def _slack_payload(self, channel,
                       simple_message,
                       intro,
                       attachments,
                       link_names,
                       unfurl_links,
                       unfurl_media,
                       icon_url,
                       icon_emoji,
                       sender):
        payload = {"channel": channel, "link_names": link_names,
                   "unfurl_links": unfurl_links, "unfurl_media": unfurl_media}
        # hipchat has a 10,000 char limit on messages, we leave some leeway
        # not sure what slack's limit is (undocumented?) so for now just use
        # the same as hipchat and see what happens.
        # TODO(mroth): test and find the actual limit (or ask SlackHQ)
        message = self.message[:9000]

        if icon_url:
            payload["icon_url"] = icon_url
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji
        if sender:
            payload["username"] = sender

        if simple_message:                          # "simple message" case
            payload["text"] = message
        elif attachments:                           # "attachments" case
            # the documentation tells people only to pass us a list even with a
            # single element (since we want them to be familiar with Slack API)
            # but if they just pass us a dict, be nice and handle for them,
            # since its a very common use-case to want to send a single
            # attachment dict and easy to forget to wrap it
            if not isinstance(attachments, list):
                attachments = [attachments]
            for attachment in attachments:
                # Many times, when writing a custom attachment, people forget
                # to specify a fallback.  Se we'll do it for them.  Slack will
                # automatically convert "<url|text>" to "text url" on fallback
                # clients; for any other markdown we may have put in, we let it
                # be; a lot of IRC clients will interpret it anyway, and it's
                # still readable if they don't.
                if 'fallback' not in attachment:
                    texts = []
                    if 'pretext' in attachment:
                        texts.append(attachment['pretext'])
                    if 'text' in attachment:
                        texts.append(attachment['text'])
                    if 'fields' in attachment:
                        for field in attachment['fields']:
                            texts.append("%s: %s" % (field['title'],
                                                     field['value']))
                    attachment['fallback'] = '\n'.join(texts)
            payload["attachments"] = attachments
            payload['text'] = intro + '\n'

        else:                                       # "alertlib style" case
            color = self._mapped_severity(_LOG_PRIORITY_TO_SLACK_COLOR)
            fallback = ("{}\n{}".format(self.summary, message)
                        if self.summary else message)
            attachment = {
                "text": message,
                "color": color,
                "fallback": fallback,
                "mrkdwn_in": ["text", "pretext"],
            }
            payload['text'] = intro + '\n'
            if self.summary:
                attachment["pretext"] = self.summary
            payload["attachments"] = [attachment]

        return payload

    def send_to_slack(self, channel,
                      simple_message=False,
                      intro='',
                      attachments=None,
                      link_names=1,
                      unfurl_links=False,
                      unfurl_media=True,
                      icon_url=None,
                      icon_emoji=None,
                      sender=None):
        """Send the alert message to Slack.

        This wraps a subset of the Slack API incoming webhook in order to
        make it behave closer to the default expectations of an AlertLib user,
        while still enabling customization of results.

        If the alert is HTML formatted, it will not be displayed correctly on
        Slack, but for now this method will only WARN in the logs rather than
        error in this condition. In the future, this may change.

        For the default case, try to emulate HipChat style msgs from AlertLib.
        The color of the message will be based on the `Alert.severity`.

        There are two notable exceptions regarding formatting style.

        ### Simple Messages
        First, if `simple_message=True` is passed, the message will be passed
        along to Slack using normal simple Markdown formatting, instead of
        being rendered as "attachment" style.

        ### Attachments
        Second, if an "attachments" dict list is passed, these will be passed
        along to Slack to enable very detailed message display parameters.

        See https://api.slack.com/docs/attachments for attachment details, and
        https://api.slack.com/docs/formatting for more on formatting.  If you
        don't specify fallback text, AlertLib will fill it in for you.

        Note that when passing attachments to Slack, AlertLib will by default
        ignore the `Alert.message`, on the assumption that you will be
        providing your entire UI via the attachment.

        Arguments:
            channel: Slack channel name or encoded ID.
                e.g. '#1s-and-0s' or 'hip-slack' or 'C1234567890'.
                (Note that channel names start with a hashtag whereas private
                groups do not.)

            simple_message: If True, send as a simple Slack message rather than
                constructing a standard AlertLib style formatted message.
                A simple message does not use the 'attachment' mechanism,
                meaning the text is not indented nor does it have a colored
                side-bar.  If True, @mentions in self.message will trigger
                notifications.

            intro: Text that will go ahead of the message and attachments.
                @mentions in this text will trigger desktop and phone
                notifications.  Will be ignored if simple_message is True.

            attachments: List of "attachments" dicts for advanced formatting.
                Even if you are only sending one attachment, you must place it
                in a list.  @mentions in attachment texts and pretexts do not
                trigger notifications.

            link_names: Automatically link channels and usernames in message.
                If disabled, user and channel names will need need to be
                explicitly marked up in order to be linked, e.g. <@mroth> or
                <#hipslack>.

            unfurl_links: Enable unfurling -- showing a content preview --
                of primarily text-based content.

            unfurl_media: Enable unfurling -- showing a content preview --
                of media content.

            icon_url: URL to an image to use as the icon for this message.

            icon_emoji: Emoji to use as the icon for this message.
                Default is to use the Slack integration's default setting,
                which is likely :crocodile:.

            sender: Name of the bot.
                Default is to use the Slack integration's default setting,
                which is likely "AlertiGator".
        """
        if not self._passed_rate_limit('slack'):
            return self

        if self.html:
            logging.warning("Unsupported HTML msg being sent to Slack!: %s",
                            self.message)

        payload = self._slack_payload(
            channel, simple_message=simple_message, intro=intro,
            attachments=attachments, link_names=link_names,
            unfurl_links=unfurl_links, unfurl_media=unfurl_media,
            icon_url=icon_url, icon_emoji=icon_emoji, sender=sender)
        payload_json = json.dumps(payload)

        if self._in_test_mode():
            logging.info("alertlib: would send to slack channel %s: %s"
                         % (channel, payload_json))
        else:
            _post_to_slack(payload_json)

        return self      # so we can chain the method calls
