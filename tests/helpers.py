"""Shared builders for the test suite (not a test module)."""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixtures import generate_reports  # noqa: E402


def latest():
    """A deep copy of the latest seeded export, safe to mutate."""
    return copy.deepcopy(generate_reports()[-1])


def incident():
    """A deep copy of the fast-burn seeded export, safe to mutate."""
    return copy.deepcopy(generate_reports()[1])


def all_reports():
    return copy.deepcopy(generate_reports())
