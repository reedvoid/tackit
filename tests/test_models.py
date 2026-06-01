"""D2/D7 — the typed boundary (models.py) accepts the relaxed D7 invariant:
stale=True is allowed on either status, since T123 (2026-06-01) stopped the
cascade from force-opening closed neighbors. v0.4 (D28) makes closed-stale
record-only: the flag stays for archaeology but it's not on the worklist.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tackit.models import Task


def _now():
    return datetime.now(timezone.utc)


def test_task_stale_and_closed_is_allowed():
    """T123 / D28: closed-stale is the cascade signal that a closed task's
    upstream changed. Under v0.4 the flag is record-only (not on the worklist),
    but the model accepts both statuses with stale=True."""
    t = Task(id=1, name="x", status="closed", stale=True, created_at=_now(), updated_at=_now())
    assert t.stale is True and t.status == "closed"


def test_task_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        Task(id=1, name="", created_at=_now(), updated_at=_now())


def test_task_stale_and_open_is_allowed():
    t = Task(id=1, name="x", status="open", stale=True, created_at=_now(), updated_at=_now())
    assert t.stale is True and t.status == "open"
