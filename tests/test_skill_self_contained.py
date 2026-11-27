"""T347 / D207 - the shipped SKILL.md's form.

The skill ships to every tackit user, so it is self-contained (D207):
- it cites no task ids: an id from this repo's store ("D245", "#179") names an
  unrelated row in another user's store, and that reader cannot open ours;
- it carries no capitals for stress (plain register);
- every section another surface cites by name (a docstring, CLI help, README or
  a shipped command saying `SKILL.md "Edits aren't free"`) exists as a heading.
It also keeps the passages two other specs require: D245's four decision-homing
surfaces and D234's section position.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "src" / "tackit" / "data" / "SKILL.md"

# A kind letter followed directly by digits (D245, S37, T179, M115), or a
# hash id (#179). Placeholders (`T<id>`, `<D#>`, `#id`, "D#/S#") don't match.
TASK_ID = re.compile(r"\b[DSTM]\d+\b|(?<![\w&#])#\d+\b")

# Upper-case words that are names, not stress.
ACRONYMS = {"MCP", "CLI", "TBD", "TODO", "SQL", "FTS", "README", "SKILL", "JSON"}

CITED_SECTION = re.compile(
    r"""SKILL(?:\.md)?`?\s+(?:"([^"]{3,80})"|'([^']{3,80})'|“([^”]{3,80})”)"""
)


def _skill() -> str:
    return CANONICAL.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def task_ids_in(text: str) -> list[str]:
    return TASK_ID.findall(text)


def stress_capitals_in(text: str) -> list[str]:
    # Code spans and quoted strings may hold literal upper-case tokens
    # (`INSERT…status`, a "SUPERSEDES" marker); only prose counts.
    prose = re.sub(r"`[^`]*`", " ", text)
    prose = re.sub(r'"[^"\n]*"', " ", prose)
    return [w for w in re.findall(r"\b[A-Z]{3,}\b", prose) if w not in ACRONYMS]


def headings_of(text: str) -> set[str]:
    return {m.group(1).strip() for m in re.finditer(r"^#{1,4}\s+(.+)$", text, re.M)}


def cited_sections(text: str) -> list[str]:
    # Join Python's adjacent string literals and unescape quotes so a citation
    # split across lines of CLI help reads whole.
    text = re.sub(r'"\s*\n\s*"', "", text).replace('\\"', '"')
    out = []
    for m in CITED_SECTION.finditer(_norm(text)):
        out.append(next(g for g in m.groups() if g))
    return out


def _citing_files() -> list[pathlib.Path]:
    files = sorted((ROOT / "src" / "tackit").glob("*.py"))
    files.append(ROOT / "README.md")
    files.extend(sorted((ROOT / "src" / "tackit" / "data" / "workflow").rglob("*.md")))
    return files


# --- no task ids --------------------------------------------------------------


def test_shipped_skill_cites_no_task_ids():
    found = task_ids_in(_skill())
    assert found == [], (
        f"SKILL.md cites task ids {found}; the shipped skill is self-contained "
        f"(D207). State the rule or incident in words instead."
    )


def test_the_id_check_catches_each_id_form():
    # Converse: a planted id of each form is caught.
    assert task_ids_in("Classify by scope.\nSee D245 for the rule.\n") == ["D245"]
    assert task_ids_in("the M115 case and S37's shape") == ["M115", "S37"]
    assert task_ids_in("as task #179 showed") == ["#179"]


def test_placeholders_are_not_ids():
    assert task_ids_in("`# T<id>: ...`, <T#> ↔ <D#>, D#/S# ids, `#id`, ## Kinds") == []


# --- plain register -------------------------------------------------------------


def test_shipped_skill_has_no_stress_capitals():
    found = stress_capitals_in(_skill())
    assert found == [], f"SKILL.md uses capitals for stress: {found} (D207 plain register)."


def test_the_capitals_check_catches_stress_but_not_code():
    assert stress_capitals_in("this OVERRIDES the bundle; do NOT skip") == ["OVERRIDES", "NOT"]
    assert stress_capitals_in("grep `INSERT…status`; a \"SUPERSEDES\" block; the MCP tool") == []


# --- cited headings exist -------------------------------------------------------


def test_every_section_cited_by_another_surface_is_a_skill_heading():
    headings = headings_of(_skill())
    missing = []
    for path in _citing_files():
        for name in cited_sections(path.read_text(encoding="utf-8")):
            if name not in headings:
                missing.append(f"{path.relative_to(ROOT)}: {name!r}")
    assert missing == [], (
        f"these cite a SKILL.md section that no longer exists (D207: cited "
        f"headings are a contract): {missing}"
    )


def test_the_citation_scan_finds_split_and_quoted_citations():
    # Converse and reach: the scan must see a citation split across CLI help
    # string literals, single-quoted, and curly-quoted forms.
    src = 'help="... See SKILL.md "\n        "\\"Edits aren\'t free\\" for it."'
    assert cited_sections(src) == ["Edits aren't free"]
    assert cited_sections("see SKILL 'Wire links explicitly'") == ["Wire links explicitly"]
    assert cited_sections("See `SKILL.md` “Fold-backs” for the format") == ["Fold-backs"]
    # A citation of a heading that doesn't exist is reported as missing.
    assert "No such section" not in headings_of(_skill())
    assert cited_sections('see SKILL.md "No such section"') == ["No such section"]


def test_the_scan_sees_the_known_citations():
    # Reach: the scan finds the citations that exist today, so an empty scan
    # can't pass the contract test vacuously.
    found = set()
    for path in _citing_files():
        found.update(cited_sections(path.read_text(encoding="utf-8")))
    assert {"Edits aren't free", "File side-door work the moment it appears",
            "Wire links explicitly", "Fold-backs"} <= found


# --- passages other specs require ------------------------------------------------


def test_the_four_decision_homing_surfaces_are_present():
    # D245: create split, fold-back routing, the report tag, the reconcile port.
    text = _norm(_skill())
    assert "A task that both decides and builds splits into a slice plus a production task" in text
    assert "A decision** (changes what's decided or the store's shape): the slice" in text
    assert "decision: which slice; correction: which body; progress: git or memory" in text
    assert "reconciling the slice means writing the decision into it" in text


def test_authority_vs_copy_section_follows_granular_description():
    # D234: the section sits directly after the granular-description discipline.
    order = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", _skill(), re.M)]
    i = order.index("The granular-description discipline")
    assert order[i + 1] == "A spec slice holds decisions, not code's literals"
