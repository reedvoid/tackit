"""Shared fixtures for the test suite.

`store_path` gives a freshly-initialized tackit store in a tmp dir; `core` opens a
Core over it. Both mirror the local fixtures in test_tackit.py so the newer,
adapter/edge-focused test modules can share them.
"""

import os

import pytest

from tackit.core import Core
from tackit.db import init_store

# M376: every tackit write commits SQLite and rewrites the store dump, so on disk the
# suite waits on I/O (measured: 658s serial on disk, 31.5s serial with temp files in
# RAM). Put pytest's temp root on the RAM disk when the machine has one and the caller
# did not choose a root; an explicit --basetemp still wins. Speed only: elsewhere the
# default temp dir is used unchanged.
_RAM_DISK = "/dev/shm"


def ram_temp_root(environ, ram_disk=_RAM_DISK):
    """The temp root to set, or None to leave pytest's choice alone: a root the
    caller already chose wins, and a missing or read-only RAM disk means none."""
    if "PYTEST_DEBUG_TEMPROOT" in environ:
        return None
    if not (os.path.isdir(ram_disk) and os.access(ram_disk, os.W_OK)):
        return None
    return ram_disk


_root = ram_temp_root(os.environ)
if _root is not None:
    os.environ["PYTEST_DEBUG_TEMPROOT"] = _root

# M376: the Hypothesis property tests take 6-31s each while every other test takes
# under 2s, so under pytest-xdist the run cannot finish before they do. Collected in
# file order they start late and the wall time swings with when a worker reaches
# them; scheduling them first lets the rest of the suite run alongside.
SLOW_FIRST = (
    "tests/test_property.py::",
    "tests/test_edit_diff.py::test_property_",
)


def pytest_collection_modifyitems(config, items):
    slow = []
    rest = []
    for item in items:
        if item.nodeid.startswith(SLOW_FIRST):
            slow.append(item)
        else:
            rest.append(item)
    items[:] = slow + rest


@pytest.fixture
def store_path(tmp_path):
    init_store(tmp_path)
    return tmp_path


@pytest.fixture
def core(store_path):
    c = Core.open(start=store_path)
    yield c
    c.close_conn()
