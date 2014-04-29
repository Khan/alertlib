#!/usr/bin/env python

"""Run the specified command, and alert if it doesn't finish in time.

The basic recipe is taken from
   http://stackoverflow.com/questions/1191374/subprocess-with-timeout
"""

import os
import signal
import subprocess

import alert


def setup_parser():
    """Create an ArgumentParser for timeout-alerting."""
    parser = alert.setup_parser()
    # TODO(csilvers): change the default severity to ERROR in this case.

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
    parser.add_argument('arg', nargs='*',
                        help=('Arguments to the command'))

    return parser


class _Alarm(Exception):
    pass


def _get_process_children(pid):
    p = subprocess.Popen(
        ['ps', '--no-headers', '-o', 'pid', '--ppid', str(pid)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (stdout, stderr) = p.communicate()
    return [int(l) for l in stdout.split()]


def _run_with_timeout(p, timeout, kill_signal, kill_tree=True):
    """Return False if we timed out, True else."""
    def alarm_handler(signum, frame):
        raise _Alarm

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
    """Run a command with a timeout after which it will be forcibly killed."""
    p = subprocess.Popen(args, shell=False, cwd=cwd)

    finished = _run_with_timeout(p, timeout, kill_signal, kill_tree)
    if not finished:
        if kill_after:
            _run_with_timeout(p, kill_after, signal.KILL, kill_tree)
    return finished


def main():
    parser = setup_parser()
    args = parser.parse_args()
    finished = run_with_timeout(args.duration, [args.command] + args.arg,
                                args.signal, args.kill_after, args.cwd)
    if not finished:
        alert.alert('TIMEOUT running %s' % args.command, args)

if __name__ == '__main__':
    main()
