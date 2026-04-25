import sqlite3
from pathlib import Path
from datetime import datetime

DB_BASE = Path(__file__).parent.parent / "databases"


def _db_path(db_name: str) -> Path:
    return DB_BASE / db_name / "memory.sqlite"


def _connect(db_name: str) -> sqlite3.Connection:
    path = _db_path(db_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_name: str) -> None:
    with _connect(db_name) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT    NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id)")


def save_message(db_name: str, session_id: str, role: str, content: str) -> None:
    with _connect(db_name) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )


def get_history(db_name: str, session_id: str, limit: int = 20) -> list[dict]:
    with _connect(db_name) as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ) ORDER BY timestamp ASC
            """,
            (session_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def clear_history(db_name: str, session_id: str) -> int:
    with _connect(db_name) as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,)
        )
        return cur.rowcount
