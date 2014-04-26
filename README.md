alertlib
========

A small library to make it easy to send alerts to various platforms.

This library consists of a python module that will let you send an
alert to any or all of

   * HipChat
   * PagerDuty
   * khanacademy.org email lists
   * GAE logs and/or syslog

You must provide a decrypted secrets.py (from the webapp repo) to use
these services.
