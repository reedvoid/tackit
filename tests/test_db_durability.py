"""D43: commits run WAL with synchronous=NORMAL on every connection tackit opens.

An application crash loses nothing; an OS crash or power loss may lose the last
commits, which D60's newer-dump rebuild recovers. Both settings are read back
from a real connection, so a connect that drops either fails here.
"""

from tackit.core import Core
from tackit.db import Store, connect

NORMAL = 1  # PRAGMA synchronous values: 0 OFF, 1 NORMAL, 2 FULL, 3 EXTRA


def test_connect_sets_wal_and_synchronous_normal(store_path):
    conn = connect(Store(store_path).db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == NORMAL
    finally:
        conn.close()


def test_core_connections_use_synchronous_normal(store_path):
    core = Core.open(start=store_path)
    try:
        assert core.conn.execute("PRAGMA synchronous").fetchone()[0] == NORMAL
    finally:
        core.close_conn()
