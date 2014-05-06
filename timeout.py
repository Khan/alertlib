#!/usr/bin/env python

"""Run the specified command, and alert if it doesn't finish in time.

We return the return-code of the specified command, or 124 if the
command did not finish in time.  (124 is taken to be consistent with
timeout(1): http://linux.die.net/man/1/timeout)

The basic recipe is taken from
   http://stackoverflow.com/questions/1191374/subprocess-with-timeout
"""

import argparse
import logging
import os
import signal
import subprocess
import sys

import alert
import alertlib


def setup_parser():
    """Create an ArgumentParser for timeout-alerting."""
    # If logging the fact a timeout happened, ERROR is a more
    # reasonable default logging error than INFO.
    alert.DEFAULT_SEVERITY = logging.ERROR

    parser = alert.setup_parser()

    # Add a few timeout-specified flags, taken from 'man timeout.'
    parser.add_argument('-k', '--kill-after', type=int,
                        help=('Also send a KILL signal if COMMAND is still '
                              'running this long after the initial signal '
                              'was sent.'))
    parser.add_argument('-s', '--signal', type=int, default=15,
                        help=('The signal to be sent on timeout, as an int. '
                              'See "kill -l" for a list of signals.'))
    parser.add_argument('--cwd', default=None,
                        help=('The directory to change to before running cmd'))

    parser.add_argument('duration', type=int,
                        help=('How many seconds to let the command run.'))
    parser.add_argument('command',
                        help=('The command to run'))
    parser.add_argument('arg', nargs=argparse.REMAINDER,
                        help=('Arguments to the command'))

    return parser


class _Alarm(Exception):
    pass


def _get_process_children(pid):
    # TODO(csilvers): get this working with OS X as well.
    # Can do via: "ps -o pid,ppid" and grep for ppid's that match str(pid).
    p = subprocess.Popen(
        ['ps', '--no-headers', '-o', 'pid', '--ppid', str(pid)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (stdout, stderr) = p.communicate()
    return [int(l) for l in stdout.split()]


def _run_with_timeout(p, timeout, kill_signal, kill_tree=True):
    """Return False if we timed out, True else."""
    def alarm_handler(signum, frame):
        raise _Alarm

    if timeout == 0:       # this is mostly useful for testing
        return False

    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(timeout)

    try:
        p.communicate()
        signal.alarm(0)
        return True
    except _Alarm:
        pids = [p.pid]
        if kill_tree:
            pids.extend(_get_process_children(p.pid))
        for pid in pids:
            # process might have died before getting to this line
            # so wrap to avoid OSError: no such process
            try:
                os.kill(pid, kill_signal)
            except OSError:
                pass
        return False


def run_with_timeout(timeout, args, kill_signal, kill_after=None,
                     cwd=None, kill_tree=True):
    """Run a command with a timeout after which it will be forcibly killed.

    If we forcibly kill, we return rc 124, otherwise we return whatever
    the command would.
    """
    p = subprocess.Popen(args, shell=False, cwd=cwd)

    finished = _run_with_timeout(p, timeout, kill_signal, kill_tree)
    if not finished:
        if kill_after:
            _run_with_timeout(p, kill_after, signal.SIGKILL, kill_tree)
    return p.returncode if finished else 124


def main(argv):
    parser = setup_parser()
    args = parser.parse_args(argv)
    if args.dry_run:
        logging.getLogger().setLevel(logging.INFO)
        alertlib.enter_test_mode()

    rc = run_with_timeout(args.duration, [args.command] + args.arg,
                          args.signal, args.kill_after, args.cwd)
    if rc == 124:
        alert.alert('TIMEOUT running %s' % args.command, args)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
