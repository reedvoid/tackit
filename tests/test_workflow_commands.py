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
        ("commands/firmup.md", '(an operation on one item made "some items succeeded, some failed" impossible;'),
        ("commands/orient.md", "is a decision, never an item in a list of defaults (check the record for what I said)."),
        ("commands/trim.md", "A failure is a disagreement, so consult the AUTHORITY: the memory text is never the authority."),
        ("commands/trim.md", "`M12 → closed in tackit`, `D34 → superseded by D56`"),
        ("commands/diagnose.md", "(parse → validate → query)"),
        ("skills/test-audit/SKILL.md", "costs far more than any audit. So this skill is NOT offered"),
        # T311: the second scan's edits
        ("commands/firmup.md", "sync, streaming, batch, background"),
        ("commands/firmup.md", "the project's do-not-read list, if it has one"),
        ("skills/test-audit/SKILL.md", "must not be read raw is read through the project's own safe route"),
        ("skills/test-audit/SKILL.md", "goes to a separate agent as ONE narrow question"),
        ("skills/test-audit/SKILL.md", 'can produce a delete-cascade "bug"'),
        ("skills/test-audit/references/llm-eval.md", "too little material for the expected label"),
        # T336: the ported rules, in generic wording
        ("commands/orient.md", "the part of the product involved"),
        ("commands/orient.md", "what a user types or does, and what the product does with it now"),
        ("commands/firmup.md", '(e.g. "a row is never deleted", "an id is set once, at creation")'),
        ("commands/firmup.md", 'a change added after the test check leaves "tests rewritten: none"'),
        ("commands/graduate.md", "a dropped item is a defect found only at the next orient."),
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
        ("commands/build.md", "live in its CLAUDE.md"),
        ("commands/trim.md", "TARGET SHAPE — TWO ZONES"),
        ("commands/orient.md", "the layout is defined in /trim"),
        ("commands/trim.md", "SOLO ONLY"),
        ("commands/handoff.md", "ADD-ONLY"),
        ("commands/orient.md", "READ-ONLY"),
        ("commands/diagnose.md", "IRON LAW"),
        ("commands/diagnose.md", "TRACE THE BAD VALUE BACKWARD to its origin: from the line that fails, ask what called it"),
        ("commands/diagnose.md", "run the tests one at a time until the first one that produces it"),
        ("skills/test-audit/SKILL.md", "So this skill is NOT offered"),
        ("skills/test-audit/references/llm-eval.md", "Pass 5"),
        # T336: rules ported from the deployed copies
        ("commands/orient.md", "**DECISIONS FOR ME**"),
        ("commands/orient.md", "CHECK BEFORE SENDING"),
        ("commands/orient.md", "5. FINALLY, close the report with a short **BIG PICTURE** recap"),
        ("commands/firmup.md", "CHECK EVERY TEST CLAIM BY RUNNING IT"),
        ("commands/firmup.md", "the GUARANTEES REMOVED"),
        ("commands/firmup.md", "a removed guarantee with a reader not judged"),
        ("commands/firmup.md", "write every question in the shape /orient's DECISIONS step requires"),
        ("commands/graduate.md", "RE-RUN the firm-up's recorded GUARANTEES REMOVED searches"),
        ("commands/graduate.md", "RECONCILE the cascade RULE BY RULE, never in a batch"),
    ],
)
def test_the_opinions_survive(filename, opinion):
    """The converse of genericizing: D306 keeps the workflow's opinions."""
    assert opinion in _shipped()[filename]


# --- T330: one memory budget, in bytes as well as lines -----------------------

MEMORY_BUDGET_LINE = (
    "MEMORY.md BUDGET: Zone 1 (above the index heading) ≤ 40 lines and ≤ 8 KB; "
    "the whole file ≤ 120 lines and ≤ 12 KB; every Zone 2 line ≤ 300 bytes. "
    "Measure with `wc -lc`, not by eye."
)
MEMORY_SIZE_COMMANDS = ("commands/trim.md", "commands/handoff.md", "commands/orient.md")
LINES_ONLY_PHRASES = ("whole file >~120 lines", "whole file over ~120 lines", "(lines + ~tokens)")


@pytest.mark.parametrize("filename", MEMORY_SIZE_COMMANDS)
def test_memory_budget_line_is_identical_in_each_size_check(filename):
    """A copy that drifts, or a command that loses the byte limits, fails by name."""
    assert MEMORY_BUDGET_LINE in _shipped()[filename]


@pytest.mark.parametrize("filename", MEMORY_SIZE_COMMANDS)
def test_no_size_check_is_lines_only(filename):
    """The converse: the old lines-only checks let a short file of very long lines pass."""
    text = _shipped()[filename]
    for phrase in LINES_ONLY_PHRASES:
        assert phrase not in text, phrase


# --- T332: the shape rules that keep MEMORY.md from regrowing ------------------

MEMORY_SHAPE_LINE = (
    "MEMORY.md SHAPE: each NEXT item ≤ 250 bytes, each session's NEXT block ≤ 600 bytes, and "
    "the merged NEXT a trim produces ≤ 1.5 KB (ids, the next action, open user calls with their "
    "first-raised date; status, numbers and paths live in the task or a topic file; "
    "lower-priority ids go on one Parked line); RECENT SESSIONS keeps the newest 3, each hook "
    "≤ 150 bytes; a Zone 2 bucket over 10 entries becomes one pointer to an index topic file."
)
ADMISSION_TEST = (
    "a line enters or stays only if no always-loaded CLAUDE.md, loaded command, the tracker "
    "skill or a design/schema slice already carries it, and current or next work needs it"
)


@pytest.mark.parametrize("filename", ("commands/trim.md", "commands/handoff.md"))
def test_memory_shape_line_is_identical_in_trim_and_handoff(filename):
    """The writer and the cleaner hold one shape; a drifted copy fails by name."""
    assert MEMORY_SHAPE_LINE in _shipped()[filename]


@pytest.mark.parametrize("filename", ("commands/trim.md", "commands/handoff.md"))
def test_do_not_rederive_admission_test_in_trim_and_handoff(filename):
    assert ADMISSION_TEST in _shipped()[filename]


def test_trim_cuts_terminal_entries_and_has_no_done_bucket():
    """The converse of the old shape: no DONE bucket, no demote-to-DONE path."""
    text = _shipped()["commands/trim.md"]
    assert "cut: the tracker holds it" in text
    assert "RECENT SESSIONS, DONE, LIVE FACTS" not in text
    assert "one-line DONE pointer" not in text
    assert "drops to a DONE pointer" not in text
    assert "RECENT SESSIONS, LIVE FACTS, FEEDBACK, REFERENCE, ARCHIVE" in text


def test_trim_enforces_the_merged_next_cap():
    """T333: the first live trim merged 9 items into 1,298 B with no stated target."""
    assert "the merged NEXT within 1.5 KB" in _shipped()["commands/trim.md"]


def test_trim_keeps_one_pointer_per_file_and_replaces_long_forms():
    text = _shipped()["commands/trim.md"]
    assert "ARCHIVE is always one pointer to an archive index topic file" in text
    assert "no Zone 2 line for a file Zone 1 already links" in text
    assert "replaces the previous long form" in text


@pytest.mark.parametrize(
    "filename, phrase",
    [
        ("commands/trim.md", "`## ⚠ HAZARDS`"),
        ("commands/trim.md", "only when the source shows the hazard is gone"),
        ("commands/handoff.md", "under `## ⚠ HAZARDS`"),
        ("commands/orient.md", "`## ⚠ HAZARDS`"),
    ],
)
def test_zone1_hazards_are_written_read_and_only_trimmed_on_evidence(filename, phrase):
    assert phrase in _shipped()[filename]


def test_handoff_never_removes_a_hazard_line():
    """The dangerous direction: an add-only writer deleting another session's live hazard."""
    assert "Never remove a hazard line; /trim does that" in _shipped()["commands/handoff.md"]


def test_handoff_keeps_tracker_action_lists_out_of_memory():
    text = _shipped()["commands/handoff.md"]
    assert "tracker-action lists stay in the session file, never MEMORY.md" in text


def test_handoff_bloat_alert_reports_bytes_and_the_limit_hit():
    """The alert is the line a reader acts on; a revert to `~Xk tok` fails here."""
    text = _shipped()["commands/handoff.md"]
    assert "`⚠ MEMORY.md BLOATED (N lines / N bytes; <which limit>)" in text
    assert "~Xk tok" not in text


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


def test_no_shipped_file_depends_on_a_plugin():
    """D306: the set depends on no plugin. A user can turn a plugin off, and a
    plain-file install cannot enforce one, so no command may point at one."""
    shipped = _shipped()
    assert "commands/diagnose.md" in shipped
    for name, text in shipped.items():
        assert "superpowers" not in text, name
        assert "root-cause-tracing.md" not in text, name


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


# --- T309: the README and project CLAUDE.md say so ----------------------------

REPO = Path(__file__).resolve().parent.parent


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_readme_describes_the_set_under_install():
    raw = (REPO / "README.md").read_text(encoding="utf-8")
    assert raw.index("## Install") < raw.index("### Optional: the workflow commands") < raw.index("## Using tackit")
    text = _normalized(REPO / "README.md")
    for command in COMMANDS:
        assert f"`/{command}`" in text
    assert "`/plain`" not in text
    assert "The `test-audit` skill comes with them." in text
    assert "They are opinionated and Claude Code only. To install them, tell your agent:" in text
    assert "superpowers" not in text
    assert "> Run `tackit setup` and carry out the optional workflow-commands step." in text


def test_project_claude_md_names_the_repo_as_master():
    text = _normalized(REPO / "CLAUDE.md")
    assert "## The workflow commands — the repo is the master copy" in text
    assert "Edit `src/tackit/data/workflow/`, then copy to `~/.claude`." in text
    assert "An edit made only in `~/.claude` drifts and never ships." in text
    assert "Everything in that folder is public: keep private project names and vocabulary out." in text


# --- T338 / T340: shipped text must survive Claude Code's argument substitution ---

# Claude Code replaces `$ARGUMENTS`, `$0`, `$1`, ... (and named `$name` arguments) in
# command and skill text with what the user typed; a literal "$0" meaning "zero-cost"
# reached the agent as the first argument word ("a M500 repro").
ARGUMENT_PLACEHOLDER = re.compile(r"\$(?!ARGUMENTS\b)[0-9A-Za-z_]")


@pytest.mark.parametrize("planted", ["a $0 repro", "see $1 here", "the $name value", "$ARGUMENTSX"])
def test_placeholder_scan_can_fail(planted):
    assert ARGUMENT_PLACEHOLDER.findall(f"text {planted} more") != []


def test_placeholder_scan_allows_the_arguments_slot():
    assert ARGUMENT_PLACEHOLDER.findall("Target: $ARGUMENTS\nand $ARGUMENTS again") == []


def test_no_shipped_file_carries_a_stray_argument_placeholder():
    shipped = _shipped()
    arguments_slots = 0
    for name, text in shipped.items():
        assert ARGUMENT_PLACEHOLDER.findall(text) == [], name
        arguments_slots += text.count("$ARGUMENTS")
    assert arguments_slots >= len(COMMANDS)  # the scan really read the commands



@pytest.mark.parametrize("name", COMMANDS)
def test_each_command_keeps_its_arguments_slot(name):
    """The converse of the placeholder guard: rewording "$0" must not cost a command
    the one placeholder it needs, or the user's argument never reaches the agent."""
    assert "$ARGUMENTS" in _shipped()[f"commands/{name}.md"]

def test_no_shipped_command_cites_an_outside_methods_phases():
    """T340: "Phase-4" was left over from the outside method T339 removed; the
    commands have steps, not phases. (The T339 guard checks only the plugin's name.)"""
    commands = {name: text for name, text in _shipped().items() if name.startswith("commands/")}
    assert len(commands) == len(COMMANDS)
    for name, text in commands.items():
        assert "Phase-" not in text, name
        assert "Phase " not in text, name


@pytest.mark.parametrize(
    "filename, phrase",
    [
        ("commands/diagnose.md", "fold the confirmed cause + a zero-cost repro INTO the meta body"),
        ("commands/diagnose.md", "plus temporary instrumentation (step 4) that you remove before the report"),
        ("commands/diagnose.md", "carries its evidence (a `file:line`, a commit hash, or a command with its output) or the word UNVERIFIED"),
        ("commands/diagnose.md", "This repro IS the failing test the eventual fix must turn green"),
        ("commands/diagnose.md", "If the cause is a commit with no task, name its hash in the meta body"),
        ("commands/diagnose.md", "<meta id> now carries a PROVEN root cause + zero-cost repro"),
        ("commands/diagnose.md", "Run probes so they leave tracked files untouched"),
        ("commands/build.md", "a zero-cost scorer-can-fail self-test FIRST"),
    ],
)
def test_diagnose_and_build_wording_fixes_landed(filename, phrase):
    assert phrase in _shipped()[filename]


# --- T371 (D306): one planted-mutant run procedure, inline where mutants are demanded ---

MUTANT_RUN_PARAGRAPH = (
    "RUNNING PLANTED MUTANTS: give each mutant its own copy of the source and tests in a "
    "fresh, uniquely named scratch directory (create it new; never clear an existing one), "
    "point the interpreter at that copy and check the imported code resolves into it "
    "(Python: PYTHONPATH to the copy's src), give each run its own test temp root and turn "
    "off shared caches (pytest: `--basetemp` set inside the copy, and `-p no:cacheprovider`), "
    "and put "
    "one break in each copy, never several (grouped breaks hide which break failed which "
    "test). Launch every independent mutant at once, up to the machine's core count; run "
    "them one after another only when one mutant's result decides the next. A sweep "
    "question (which existing tests pin this behavior?) runs the full suite per mutant, "
    "because a test that builds its target set at runtime escapes a grep; proving one new "
    "test can fail runs that test's own file. Report per mutant what was broken and the "
    "failing test ids, or \"0 failed\": no test pins it, so the plan needs a new pin."
)
MUTANT_RUN_FILES = ("commands/firmup.md", "commands/build.md", "skills/test-audit/SKILL.md")


def _files_missing_the_mutant_procedure(shipped: dict[str, str]) -> list[str]:
    """Every shipped file that mentions a mutant must carry the run procedure verbatim."""
    missing = []
    for name, text in sorted(shipped.items()):
        if "mutant" in text.lower() and MUTANT_RUN_PARAGRAPH not in text:
            missing.append(name)
    return missing


@pytest.mark.parametrize("filename", MUTANT_RUN_FILES)
def test_mutant_run_paragraph_is_identical_in_each_demanding_file(filename):
    assert MUTANT_RUN_PARAGRAPH in _shipped()[filename]


def test_every_file_that_mentions_mutants_carries_the_procedure():
    assert _files_missing_the_mutant_procedure(_shipped()) == []


def test_the_mutant_procedure_check_can_fail():
    shipped = dict(_shipped())
    assert shipped["commands/build.md"].count(MUTANT_RUN_PARAGRAPH) == 1
    # a drifted copy (one word changed) is caught by name
    shipped["commands/build.md"] = shipped["commands/build.md"].replace(
        "core count", "CPU count"
    )
    assert _files_missing_the_mutant_procedure(shipped) == ["commands/build.md"]
    # a new file demanding mutants without the procedure is caught too
    shipped = dict(_shipped())
    shipped["commands/trim.md"] = shipped["commands/trim.md"] + "\nPlant a mutant.\n"
    assert _files_missing_the_mutant_procedure(shipped) == ["commands/trim.md"]
