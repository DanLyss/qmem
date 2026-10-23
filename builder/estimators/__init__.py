"""Estimators turn the model into the profile's content.

Every estimator is a module with

    def estimate(model, options, profile) -> None

that adds items to the profile or fills fields of items already there.
They run in this order:

    lifecycle    region, map, unmap
    resident     populate
    intensity_*  epoch and access with rate      (--intensity <name>)
    write_*      the write field of access       (--write <name>)
    timing_*     the pattern field of access     (--timing <name>)

A new estimator is a new module estimators/<kind>_<name>.py.
"""
import importlib
from pathlib import Path

CHOOSABLE = ('intensity', 'write', 'timing')
DEFAULTS = {'intensity': 'passthrough', 'write': 'all', 'timing': 'roundrobin'}


def available(kind):
    here = Path(__file__).parent
    return sorted(p.stem[len(kind) + 1:] for p in here.glob(kind + '_*.py'))


def load(name):
    return importlib.import_module('estimators.' + name)


def pipeline(choice):
    """Module names in the order they run, for {kind: name}."""
    return ['lifecycle', 'resident'] + ['%s_%s' % (k, choice[k]) for k in CHOOSABLE]
