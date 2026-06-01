"""D24 - bulk plan import: parse a lightweight, dependency-by-key plan into task
specs that ``core.load()`` creates atomically.

Format (no external dependency; the design.md D#/S# slices nearly conform):

    # comments and blank lines are ignored
    [token-endpoint] Build the auth token endpoint
      kind: production
      labels: auth

    [key-rotation] Rotate JWT signing keys on the token endpoint
      kind: production
      desc: replace the static signing key with rotating keys
      labels: auth, security
      depends_on: token-endpoint

A ``[key] Name`` line starts a task; indented ``kind:`` / ``desc:`` / ``labels:`` /
``depends_on:`` lines are its fields. ``kind`` is required (D26: design | schema |
production | meta); a row missing it is refused, the whole import rolls back (T94).
``depends_on`` references other keys in the same plan. Anything malformed fails loud
(D2) before the store is touched.

Multi-line ``desc``: a ``desc:`` field may span several lines. Lines indented
*deeper* than the ``desc:`` keyword are **continuation lines** of the description;
each is appended as its own line (its leading indentation stripped), so a
multi-paragraph description round-trips faithfully — the migration case the
single-line-only parser silently truncated. The block ends at the first blank line,
the next ``field:``/``[key]`` line (i.e. any line not indented deeper than ``desc:``),
or end of input. Continuation only applies inside a ``desc`` field; an
equal-or-lesser-indented line that is neither a known field nor a ``[key]`` still
fails loud, exactly as before.

    [d1] D1 — Persistent task store
      depends_on: s1
      desc: First paragraph of the description, which may itself be long.
        A second paragraph, kept as its own line.
"""

from __future__ import annotations

import re

from .errors import ValidationError
from .schema import KIND_VALUES

_KEY_LINE = re.compile(r"^\[([A-Za-z0-9_.-]+)\]\s*(.*)$")
_FIELD_LINE = re.compile(r"^\s+([A-Za-z_]+):\s*(.*)$")
_FIELDS = {"kind", "desc", "labels", "depends_on"}


def _split_csv(value: str) -> list[str]:
    out: list[str] = []
    for part in value.split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def parse_plan(text: str) -> list[dict]:
    """Parse plan text into ordered task specs:
    ``{"key", "name", "desc", "labels": [...], "depends_on": [...]}``. Fails loud on
    a bad line, a duplicate key, a field outside a task, or an unknown field."""
    items: list[dict] = []
    seen_keys: set[str] = set()
    current: dict | None = None
    # When inside a multi-line ``desc``, the indentation width of the ``desc:``
    # keyword line; deeper-indented lines that follow are its continuation. None
    # whenever we are not collecting a desc.
    desc_indent: int | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        # A desc continuation line: deeper-indented than its `desc:` keyword. Checked
        # FIRST so description text may itself contain a leading `#` or a `word:` that
        # would otherwise be read as a comment or a field.
        if current is not None and desc_indent is not None and stripped and indent > desc_indent:
            if current["desc"]:
                current["desc"] = current["desc"] + "\n" + stripped
            else:
                current["desc"] = stripped
            continue
        # Blank line ends a desc block; blank lines and comments are otherwise ignored.
        if not stripped or stripped.startswith("#"):
            if not stripped:
                desc_indent = None
            continue
        # Any structural line (key / field) ends a desc block.
        desc_indent = None
        key_match = _KEY_LINE.match(raw.rstrip())
        if key_match:
            key = key_match.group(1)
            name = key_match.group(2).strip()
            if not name:
                raise ValidationError(f"plan line {lineno}: task [{key}] has no name.")
            if key in seen_keys:
                raise ValidationError(f"plan line {lineno}: duplicate key '{key}'.")
            seen_keys.add(key)
            current = {
                "key": key,
                "name": name,
                "kind": None,
                "desc": "",
                "labels": [],
                "depends_on": [],
            }
            items.append(current)
            continue
        field_match = _FIELD_LINE.match(raw)
        if field_match:
            field = field_match.group(1)
            value = field_match.group(2).strip()
            if current is None:
                raise ValidationError(f"plan line {lineno}: '{field}:' before any [key] task.")
            if field not in _FIELDS:
                raise ValidationError(
                    f"plan line {lineno}: unknown field '{field}' "
                    f"(expected one of kind, desc, labels, depends_on)."
                )
            if field == "kind":
                # T94 / D26: validate per-row at parse time so a bad plan never
                # touches the store. The op layer re-checks (defense in depth).
                if value not in KIND_VALUES:
                    raise ValidationError(
                        f"plan line {lineno}: kind {value!r} is not valid; "
                        f"must be one of {{{', '.join(KIND_VALUES)}}} (D26 / T94)."
                    )
                current["kind"] = value
            elif field == "desc":
                current["desc"] = value
                desc_indent = indent  # subsequent deeper-indented lines continue it
            elif field == "labels":
                current["labels"] = _split_csv(value)
            else:
                current["depends_on"] = _split_csv(value)
            continue
        raise ValidationError(f"plan line {lineno}: cannot parse: {raw.strip()!r}")
    if not items:
        raise ValidationError("plan is empty (no [key] task lines found).")
    # T94 / D26: every task must declare a kind. Refused after parse so the
    # error names the specific [key] missing the field (vs a generic "kind
    # required" at the op layer with no key context).
    for item in items:
        if item["kind"] is None:
            raise ValidationError(
                f"task '{item['key']}' is missing required `kind:` field. "
                f"Add a `kind: <design|schema|production|meta>` line (D26 / T94)."
            )
    return items
