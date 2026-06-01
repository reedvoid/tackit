"""SQLite schema - tables S1-S6 from schema.md.

Each CREATE statement is tagged with its S# so the DDL traces directly to the
schema doc. Acyclicity (S3) and the stale=>open invariant (S1) are NOT expressed
in DDL -- they live in core logic (D14/D7), as the doc notes.
"""

SCHEMA_VERSION = "5"

# D26 task kind taxonomy. The four values are also reserved label strings (D14):
# label_add / load refuse a label equal to any of them, because S1.kind absorbs
# that distinction and a stray label would silently disagree.
KIND_VALUES = ("design", "schema", "production", "meta")
RESERVED_LABELS = KIND_VALUES

# --- S1 `tasks` -------------------------------------------------------------
# The atomic item; single source of truth a task's every view is derived from.
S1_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    kind          TEXT    NOT NULL DEFAULT 'production' CHECK (kind IN ('design', 'schema', 'production', 'meta')),
    status        TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    stale         INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    superseded_by INTEGER REFERENCES tasks(id) CHECK (superseded_by IS NULL OR superseded_by <> id),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
"""

# --- S2 `task_labels` -------------------------------------------------------
# Freeform many-to-many tags; no separate labels dimension table (no attributes).
S2_TASK_LABELS = """
CREATE TABLE IF NOT EXISTS task_labels (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    label   TEXT    NOT NULL,
    PRIMARY KEY (task_id, label)
);
"""

# --- S3 `links` -------------------------------------------------------------
# The single edge type: SYMMETRIC. One row per unordered pair, stored in
# canonical order (task_a < task_b, enforced by CHECK). Queries treat both
# endpoints equivalently. (Renamed from `dependencies` and made symmetric in
# v0.3.0; see migration 003 / T86. Replaces the directional from_task ->
# to_task model with an undirected coupling.)
S3_LINKS = """
CREATE TABLE IF NOT EXISTS links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_a  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    task_b  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    because TEXT    NOT NULL CHECK (length(because) > 0),
    UNIQUE (task_a, task_b),
    CHECK (task_a < task_b)
);
"""

# --- S4 `status_transitions` ------------------------------------------------
# Append-only history of status changes (D8). Never edited or deleted.
S4_STATUS_TRANSITIONS = """
CREATE TABLE IF NOT EXISTS status_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT    NOT NULL,
    changed_at  TEXT    NOT NULL
);
"""

# --- S5 `tasks_fts` ---------------------------------------------------------
# FTS5 virtual table indexing name+description for ranked keyword search (D17).
# Kept in sync with `tasks` by the triggers below. `content='tasks'` makes it an
# external-content index (rowid = tasks.id), so the text isn't stored twice.
S5_TASKS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    name,
    description,
    content='tasks',
    content_rowid='id'
);
"""

# S5 sync triggers: mirror every tasks insert/update/delete into the FTS index.
# The 'delete' command rows are the FTS5-prescribed way to retract external
# content before re-inserting on update.
S5_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS tasks_fts_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;
CREATE TRIGGER IF NOT EXISTS tasks_fts_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
END;
CREATE TRIGGER IF NOT EXISTS tasks_fts_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;
"""

# --- S6 `meta` --------------------------------------------------------------
# Key/value bookkeeping: version (ordering, D18), synced_sql_hash (integrity,
# D18), schema_version (migrations).
S6_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Ordered DDL for a fresh store. tasks before its dependents; FTS table before
# its triggers.
ALL_DDL = [
    S1_TASKS,
    S2_TASK_LABELS,
    S3_LINKS,
    S4_STATUS_TRANSITIONS,
    S5_TASKS_FTS,
    S5_FTS_TRIGGERS,
    S6_META,
]
