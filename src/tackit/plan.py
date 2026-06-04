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
      depends_on:
        token-endpoint :: token endpoint defines the request/response shape this
          rotation must preserve when swapping signing keys

A ``[key] Name`` line starts a task; indented ``kind:`` / ``desc:`` / ``labels:`` /
``depends_on:`` lines are its fields. ``kind`` is required (D26: design | schema |
production | meta); a row missing it is refused, the whole import rolls back (T94).
``depends_on`` references other keys in the same plan. Anything malformed fails loud
(D2) before the store is touched.

D33 / T164 (v0.4): every dep edge MUST carry an explicit per-edge ``because``
rationale describing the coupling. The pre-T164 CSV form (``depends_on: a, b, c``)
is refused with a clear error pointing at the new continuation-block syntax: the
``depends_on:`` keyword line has an empty value, and each dep is its own
deeper-indented continuation line in the form ``<key> :: <because rationale>``.
Continuation lines indented deeper still continue the previous rationale (same
rule as ``desc:`` multi-line).

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
      depends_on:
        s1 :: D1's persistence contract is realized over the S1 schema, so a
          change to S1's columns or indexes shifts what D1 must promise.
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
    ``{"key", "name", "desc", "labels": [...], "depends_on": [{"key", "because"}, ...]}``.
    Fails loud on a bad line, a duplicate key, a field outside a task, or an
    unknown field. Under D33 / T164 (v0.4), every dep entry must carry an
    explicit ``because`` rationale; the pre-T164 CSV form is refused."""
    items: list[dict] = []
    seen_keys: set[str] = set()
    current: dict | None = None
    # When inside a multi-line ``desc``, the indentation width of the ``desc:``
    # keyword line; deeper-indented lines that follow are its continuation. None
    # whenever we are not collecting a desc.
    desc_indent: int | None = None
    # T164: same idea for ``depends_on:`` continuation lines. Each continuation
    # is one dep entry of the form ``<key> :: <because rationale>``.
    deps_indent: int | None = None
    # D40: blank lines seen while collecting a ``desc`` are DEFERRED -- a blank
    # becomes a paragraph break iff a desc continuation line follows; if a
    # field / [key] / EOF follows instead, the trailing blanks are discarded
    # and the block simply ends. Lets multi-paragraph D37-grade bodies
    # round-trip through ``load`` (the parser used to hard-fail on them).
    pending_blank_lines: int = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        # A desc continuation line: deeper-indented than its `desc:` keyword. Checked
        # FIRST so description text may itself contain a leading `#` or a `word:` that
        # would otherwise be read as a comment or a field.
        if current is not None and desc_indent is not None and stripped and indent > desc_indent:
            # D40: a continuation line confirms any deferred blank lines were
            # real paragraph breaks inside the desc -- flush them first.
            if pending_blank_lines:
                current["desc"] = current["desc"] + ("\n" * pending_blank_lines)
                pending_blank_lines = 0
            if current["desc"]:
                current["desc"] = current["desc"] + "\n" + stripped
            else:
                current["desc"] = stripped
            continue
        # T164: a depends_on continuation line: deeper-indented than the
        # `depends_on:` keyword, of the form `<key> :: <because rationale>`.
        if current is not None and deps_indent is not None and stripped and indent > deps_indent:
            if "::" not in stripped:
                raise ValidationError(
                    f"plan line {lineno}: depends_on continuation line "
                    f"{stripped!r} missing the `::` separator. Each dep entry "
                    f"must be `<key> :: <because rationale>` (D33 / T164)."
                )
            dep_key, sep, because = stripped.partition("::")
            dep_key = dep_key.strip()
            because = because.strip()
            if not dep_key:
                raise ValidationError(
                    f"plan line {lineno}: depends_on entry has no key before `::`."
                )
            if not because:
                raise ValidationError(
                    f"plan line {lineno}: depends_on entry for '{dep_key}' has "
                    f"an empty `because` rationale. D33 / T164 requires every "
                    f"dep edge to carry a real one-sentence coupling rationale; "
                    f"the pre-T164 placeholder shortcut is retired."
                )
            current["depends_on"].append({"key": dep_key, "because": because})
            continue
        # Blank/comment handling. A blank line ends a deps block, but inside a
        # desc block it is DEFERRED (D40): remembered as a possible paragraph
        # break, resolved when the next non-blank line arrives (continuation =>
        # paragraph break; structural line / EOF => block already ended).
        if not stripped or stripped.startswith("#"):
            if not stripped:
                deps_indent = None
                if desc_indent is not None:
                    pending_blank_lines += 1
                else:
                    pending_blank_lines = 0
            continue
        # Any structural line (key / field) ends a desc/deps block; trailing
        # deferred blanks before it were NOT part of the desc -- discard them.
        desc_indent = None
        deps_indent = None
        pending_blank_lines = 0
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
            else:  # depends_on
                # D33 / T164: refuse the pre-T164 CSV form `depends_on: a, b, c`.
                # The keyword must be followed by an empty value and a multi-line
                # continuation block where each dep is `<key> :: <because>`.
                if value:
                    raise ValidationError(
                        f"plan line {lineno}: pre-T164 inline `depends_on: {value}` "
                        f"form is retired (D33). The keyword must be on its own "
                        f"line; put each dep on a continuation line of the form "
                        f"`<key> :: <because rationale>`."
                    )
                deps_indent = indent  # subsequent deeper-indented lines are dep entries
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
