"""Shared fixtures for the test suite.

`store_path` gives a freshly-initialized tackit store in a tmp dir; `core` opens a
Core over it. Both mirror the local fixtures in test_tackit.py so the newer,
adapter/edge-focused test modules can share them.
"""

import pytest

from tackit.core import Core
from tackit.db import init_store


@pytest.fixture
def store_path(tmp_path):
    init_store(tmp_path)
    return tmp_path


@pytest.fixture
def core(store_path):
    c = Core.open(start=store_path)
    yield c
    c.close_conn()
