import sys

PAGE = 4096


def die(msg):
    sys.exit('qmem-build: ' + msg)


def warn(msg):
    print('qmem-build: ' + msg, file=sys.stderr)
