"""D2/D7 — the typed boundary (models.py) re-checks the stale=>open invariant, so a
malformed row can never round-trip even if logic somewhere tried to set it.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tackit.models import Task


def _now():
    return datetime.now(timezone.utc)


def test_task_stale_and_closed_is_rejected():
    with pytest.raises(ValidationError):
        Task(id=1, name="x", status="closed", stale=True, created_at=_now(), updated_at=_now())


def test_task_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        Task(id=1, name="", created_at=_now(), updated_at=_now())


def test_task_stale_and_open_is_allowed():
    t = Task(id=1, name="x", status="open", stale=True, created_at=_now(), updated_at=_now())
    assert t.stale is True and t.status == "open"
