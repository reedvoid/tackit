"""M376: the suite's own run settings, set in tests/conftest.py.

The temp root goes to the RAM disk when the machine has one (disk I/O was ~95% of
the serial run), and the slow Hypothesis tests are scheduled first so a parallel
run is not left waiting on one of them at the end.
"""

import os
from pathlib import Path

import pytest

from conftest import SLOW_FIRST

_RAM_DISK = Path("/dev/shm")


@pytest.mark.skipif(
    not (_RAM_DISK.is_dir() and os.access(_RAM_DISK, os.W_OK)),
    reason="no writable RAM disk on this machine",
)
def test_tmp_path_lives_on_the_ram_disk(tmp_path):
    # Fails if the conftest stops setting the root, or sets it after pytest has
    # already chosen a disk directory. It guards the default run: a run that passes
    # its own --basetemp on the RAM disk (a mutant copy) passes it regardless.
    assert Path(os.path.realpath(tmp_path)).is_relative_to(_RAM_DISK.resolve())


def test_a_caller_chosen_temp_root_is_left_alone(tmp_path):
    from conftest import ram_temp_root

    assert ram_temp_root({"PYTEST_DEBUG_TEMPROOT": "/elsewhere"}, ram_disk=str(tmp_path)) is None


def test_no_ram_disk_means_no_override(tmp_path):
    from conftest import ram_temp_root

    assert ram_temp_root({}, ram_disk=str(tmp_path / "missing")) is None
    assert ram_temp_root({}, ram_disk=str(tmp_path)) == str(tmp_path)


_REPO = Path(__file__).resolve().parent.parent


def test_every_slow_first_prefix_still_names_a_test():
    # A renamed or moved slow test would silently stop being prioritized.
    for prefix in SLOW_FIRST:
        path, _, name_prefix = prefix.partition("::")
        source = (_REPO / path).read_text(encoding="utf-8")
        if name_prefix:
            assert f"def {name_prefix}" in source, prefix
        else:
            assert "def test_" in source or "TestCase" in source or "runTest" in source, prefix


class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_the_hook_moves_slow_tests_to_the_front_keeping_order():
    import conftest

    ids = [
        "tests/test_a.py::test_one",
        "tests/test_property.py::TestTackitStateMachine::runTest",
        "tests/test_b.py::test_two",
        "tests/test_edit_diff.py::test_property_edit_append_concatenates",
        "tests/test_edit_diff.py::test_plain_case",
    ]
    items = [_Item(i) for i in ids]
    conftest.pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == [
        "tests/test_property.py::TestTackitStateMachine::runTest",
        "tests/test_edit_diff.py::test_property_edit_append_concatenates",
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
        "tests/test_edit_diff.py::test_plain_case",
    ]
