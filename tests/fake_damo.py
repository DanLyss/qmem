#!/usr/bin/env python3
"""Stand-in for damo in tests that run without root or DAMON.

Supports what qmem-record.sh uses:
  damo version
  damo record --do_record access [options] -o <file> <command | pid>
  damo report access -i <file> ... (prints a canned report)

Every call is appended to $FAKE_DAMO_LOG as one JSON line with argv.
"""
import json
import os
import signal
import subprocess
import sys
import time

# Output in the shape of real `damo report access --raw_number` for the --format
# strings that qmem-record.sh passes (shape checked against damo 3.3.0).
CANNED_REPORT = """snapshot 0 100000000
4096         20480       100.000000 200.000000 300000
20480        1048576     0.000000 0.000000 700000

snapshot 100000000 200000000
4096         20480       50.000000 100.000000 400000
20480        1048576     0.000000 0.000000 800000

"""


def log(argv):
    path = os.environ.get('FAKE_DAMO_LOG')
    if path:
        with open(path, 'a') as f:
            f.write(json.dumps(argv) + '\n')


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0)
    with open('/proc/%d/stat' % pid) as f:
        return f.read().rsplit(')', 1)[1].split()[0] != 'Z'


def record(args):
    out = args[args.index('-o') + 1]
    target = args[-1]

    def finish(code):
        with open(out, 'w') as f:
            f.write('fake damon data\n')
        sys.exit(code)

    def on_signal(signum, frame):
        # like real damo: stop recording, save, exit with the signal number,
        # and leave the target process alone
        finish(signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if target.isdigit():
        pid = int(target)
        if not pid_alive(pid):
            print('fake damo: no such process %d' % pid, file=sys.stderr)
            sys.exit(1)
        while pid_alive(pid):
            time.sleep(0.05)
    else:
        child = subprocess.Popen(target, shell=True, executable='/bin/bash')
        child.wait()
    finish(0)


def main():
    argv = sys.argv[1:]
    log(argv)
    if argv[:1] == ['version']:
        print('3.3.0')
    elif argv[:1] == ['record']:
        record(argv[1:])
    elif argv[:2] == ['report', 'access']:
        sys.stdout.write(CANNED_REPORT)
    else:
        print('fake damo: unsupported: %s' % argv, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
