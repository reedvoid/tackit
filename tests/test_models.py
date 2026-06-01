"""D2/D7 — the typed boundary (models.py) accepts the relaxed D7 invariant:
stale=True is allowed on either status, since T123 (2026-06-01) stopped the
cascade from force-opening closed neighbors. A closed-stale task signals "the
upstream changed; review for supersede / link migration" while staying closed
and immutable per T118.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tackit.models import Task


def _now():
    return datetime.now(timezone.utc)


def test_task_stale_and_closed_is_allowed():
    """T123: relaxed D7 — closed-stale is the cascade signal that a closed task's
    upstream changed and needs supersede/link review."""
    t = Task(id=1, name="x", status="closed", stale=True, created_at=_now(), updated_at=_now())
    assert t.stale is True and t.status == "closed"


def test_task_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        Task(id=1, name="", created_at=_now(), updated_at=_now())


def test_task_stale_and_open_is_allowed():
    t = Task(id=1, name="x", status="open", stale=True, created_at=_now(), updated_at=_now())
    assert t.stale is True and t.status == "open"
