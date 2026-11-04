"""T307 / T310 / D306: the workflow commands shipped under tackit/data/workflow.

Everything here reaches a public package, so this file checks generic patterns
only. It must never quote private names or removed private text: a test listing
them would itself leak them. Names are the release scan's job (D306).
"""

import re
from importlib import resources
from pathlib import Path

import pytest

COMMANDS = ("diagnose", "firmup", "graduate", "build", "orient", "handoff", "trim")
EXPECTED_FILES = {f"commands/{name}.md" for name in COMMANDS} | {
    "skills/test-audit/SKILL.md",
    "skills/test-audit/references/deterministic-code.md",
    "skills/test-audit/references/llm-eval.md",
}
ALLOWED_SLICE_CITATIONS = {"D245", "D250", "D256"}

# /context is a Claude Code built-in. /open is a false match on `NEXT`/open-hub in trim.md.
NON_COMMAND_SLASH_TOKENS = {"/context", "/open"}
SLASH_TOKEN = re.compile(r"(?<![\w/.~-])/[a-z]+\b")
REFERENCE_FILE = re.compile(r"references/[\w-]+\.md")
STORE_STYLE_ID = re.compile(r"\b[MDST]\d{3,}\b")


def _workflow_dir() -> Path:
    return Path(str(resources.files("tackit").joinpath("data/workflow")))


def _shipped() -> dict[str, str]:
    texts = {}
    for path in _workflow_dir().rglob("*"):
        if path.is_file():
            texts[path.relative_to(_workflow_dir()).as_posix()] = path.read_text(encoding="utf-8")
    return texts


def _privacy_findings(text: str) -> list[str]:
    findings = []
    if "/home/" in text:
        findings.append("/home/")
    if "2026-" in text:
        findings.append("2026-")
    for match in STORE_STYLE_ID.findall(text):
        if match not in ALLOWED_SLICE_CITATIONS:
            findings.append(match)
    return findings


def _unresolved_references(text: str) -> list[str]:
    command_tokens = {f"/{name}" for name in COMMANDS}
    unresolved = []
    for token in SLASH_TOKEN.findall(text):
        if token not in command_tokens and token not in NON_COMMAND_SLASH_TOKENS:
            unresolved.append(token)
    test_audit_dir = _workflow_dir() / "skills" / "test-audit"
    for reference in REFERENCE_FILE.findall(text):
        if not (test_audit_dir / reference).is_file():
            unresolved.append(reference)
    return unresolved


def test_shipped_set_is_exactly_the_expected_files():
    """An added file, plain.md or a .bak all go red here."""
    assert set(_shipped()) == EXPECTED_FILES


@pytest.mark.parametrize(
    "planted",
    ["see /home/someone/project", "ruled on 2026-01-02", "closed as M9999", "see D999", "see T1000", "see S123"],
)
def test_privacy_scan_can_fail(planted):
    assert _privacy_findings(f"clean text {planted} more text") != []


def test_privacy_scan_allows_the_cited_slices():
    assert _privacy_findings("tackit D245, D250 and D256, plus M12 and D34") == []


def test_shipped_files_pass_the_privacy_scan():
    shipped = _shipped()
    assert shipped
    for name, text in shipped.items():
        assert _privacy_findings(text) == [], name


def test_shipped_text_does_not_cite_personal_configuration():
    shipped = _shipped()
    assert shipped
    for name, text in shipped.items():
        assert "~/.claude/CLAUDE.md" not in text, name
        assert "global CLAUDE.md" not in text, name
        assert "Quality skills" not in text, name


@pytest.mark.parametrize(
    "filename, replacement",
    [
        ("commands/firmup.md", '(a single-file upload made "some files saved, some rejected" impossible;'),
        ("commands/orient.md", "goes here as a question, never in a list of defaults (check the record for what I said)."),
        ("commands/trim.md", "A failure is a disagreement, so consult the AUTHORITY: the memory text is never the authority."),
        ("commands/trim.md", "`M12 → closed in tackit`, `D34 → superseded by D56`"),
        ("commands/diagnose.md", "(parse → validate → query)"),
        ("skills/test-audit/SKILL.md", "costs far more than any audit. So this skill is NOT offered"),
    ],
)
def test_each_genericizing_edit_landed(filename, replacement):
    assert replacement in _shipped()[filename]


@pytest.mark.parametrize(
    "filename, opinion",
    [
        ("commands/firmup.md", "Pass 5"),
        ("commands/firmup.md", "COVERAGE MATRIX"),
        ("commands/graduate.md", "PRECONDITION"),
        ("commands/build.md", "BILLED EVAL SHIP-GATE"),
        ("commands/build.md", "/context"),
        ("commands/trim.md", "SOLO ONLY"),
        ("commands/handoff.md", "ADD-ONLY"),
        ("commands/orient.md", "READ-ONLY"),
        ("commands/diagnose.md", "IRON LAW"),
        ("commands/diagnose.md", "superpowers:systematic-debugging"),
        ("skills/test-audit/SKILL.md", "So this skill is NOT offered"),
        ("skills/test-audit/references/llm-eval.md", "Pass 5"),
    ],
)
def test_the_opinions_survive(filename, opinion):
    """The converse of genericizing: D306 keeps the workflow's opinions."""
    assert opinion in _shipped()[filename]


@pytest.mark.parametrize(
    "planted",
    ["run /plain next", "see /other", "`references/missing.md`"],
)
def test_reference_check_can_fail(planted):
    assert _unresolved_references(f"text {planted} more") != []


def test_reference_check_ignores_paths_and_known_tokens():
    text = "and/or ~/.claude/commands /firmup then /context, a `NEXT`/open-hub and references/llm-eval.md"
    assert _unresolved_references(text) == []


def test_shipped_cross_references_resolve():
    shipped = _shipped()
    checked = set()
    for name, text in shipped.items():
        assert _unresolved_references(text) == [], name
        checked.update(SLASH_TOKEN.findall(text))
    assert "/firmup" in checked  # the check really saw the commands
    assert "/handoff" in checked
    # diagnose's one outside file belongs to the superpowers plugin, which it names
    assert "root-cause-tracing.md" in shipped["commands/diagnose.md"]


@pytest.mark.parametrize("name", COMMANDS)
def test_command_has_frontmatter_description(name):
    text = _shipped()[f"commands/{name}.md"]
    assert text.startswith("---\n")
    frontmatter = text.split("---\n")[1]
    assert "description:" in frontmatter


def test_test_audit_skill_has_a_name_and_description():
    text = _shipped()["skills/test-audit/SKILL.md"]
    assert text.startswith("---\n")
    frontmatter = text.split("---\n")[1]
    assert "name: test-audit" in frontmatter
    assert "description:" in frontmatter
