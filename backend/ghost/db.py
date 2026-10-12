"""SQLite access layer: connection handling, migrations and small query helpers."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_local = threading.local()
_migrate_lock = threading.Lock()


def now_real() -> int:
    return int(time.time())


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path or settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn() -> sqlite3.Connection:
    """Thread-local connection (FastAPI runs sync endpoints in a thread pool)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def reset_thread_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending numbered migrations. Safe to run repeatedly."""
    with _migrate_lock:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_real INTEGER NOT NULL)"
        )
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        done = []
        for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if file.stem in applied:
                continue
            conn.executescript("BEGIN;" + file.read_text(encoding="utf-8") + ";COMMIT;")
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (file.stem, now_real()))
            done.append(file.stem)
        return done


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    conn = conn or get_conn()
    if conn.in_transaction:  # nested: let the outer transaction own commit/rollback
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def rows(sql: str, params: Any = (), conn: sqlite3.Connection | None = None) -> list[dict]:
    return [dict(r) for r in (conn or get_conn()).execute(sql, params).fetchall()]


def row(sql: str, params: Any = (), conn: sqlite3.Connection | None = None) -> dict | None:
    r = (conn or get_conn()).execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: Any = (), conn: sqlite3.Connection | None = None) -> Any:
    r = (conn or get_conn()).execute(sql, params).fetchone()
    return r[0] if r else None


def execute(sql: str, params: Any = (), conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
    return (conn or get_conn()).execute(sql, params)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def get_state(key: str, default: Any = None, conn: sqlite3.Connection | None = None) -> Any:
    r = row("SELECT value FROM system_state WHERE key=?", (key,), conn)
    return json.loads(r["value"]) if r else default


def set_state(key: str, value: Any, conn: sqlite3.Connection | None = None) -> None:
    execute(
        "INSERT INTO system_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, dumps(value)),
        conn,
    )
