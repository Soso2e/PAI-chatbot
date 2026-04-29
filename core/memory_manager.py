import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_BASE = Path(__file__).parent.parent / "databases"


def _db_path(db_name: str) -> Path:
    return DB_BASE / db_name / "memory.sqlite"


def _json_path(db_name: str) -> Path:
    return DB_BASE / db_name / "memory.json"


def _connect(db_name: str) -> sqlite3.Connection:
    path = _db_path(db_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _empty_store() -> dict:
    return {
        "messages": [],
        "memory_entries": [],
        "meta": {"storage": "json-fallback"},
    }


def _load_store(db_name: str) -> dict:
    path = _json_path(db_name)
    if not path.exists():
        return _empty_store()
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    store = _empty_store()
    store.update(data)
    store["messages"] = data.get("messages", [])
    store["memory_entries"] = data.get("memory_entries", [])
    return store


def _save_store(db_name: str, store: dict) -> None:
    import json

    path = _json_path(db_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_available(db_name: str) -> bool:
    try:
        with _connect(db_name) as conn:
            conn.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


def init_db(db_name: str) -> None:
    try:
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    content     TEXT    NOT NULL,
                    author_id   TEXT,
                    source      TEXT    NOT NULL DEFAULT 'manual',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_created_at ON memory_entries(created_at DESC)")
    except sqlite3.Error:
        _save_store(db_name, _load_store(db_name))


def save_message(db_name: str, session_id: str, role: str, content: str) -> None:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
        return

    store = _load_store(db_name)
    store["messages"].append(
        {
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": _utc_now(),
        }
    )
    _save_store(db_name, store)


def get_history(db_name: str, session_id: str, limit: int = 20) -> list[dict]:
    init_db(db_name)
    if _sqlite_available(db_name):
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

    messages = [
        item
        for item in _load_store(db_name)["messages"]
        if item["session_id"] == session_id
    ]
    messages = messages[-limit:]
    return [{"role": item["role"], "content": item["content"]} for item in messages]


def clear_history(db_name: str, session_id: str) -> int:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            cur = conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            return cur.rowcount

    store = _load_store(db_name)
    before = len(store["messages"])
    store["messages"] = [
        item for item in store["messages"] if item["session_id"] != session_id
    ]
    _save_store(db_name, store)
    return before - len(store["messages"])


def save_memory(db_name: str, content: str, author_id: str = "", source: str = "manual") -> int:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            cur = conn.execute(
                "INSERT INTO memory_entries (content, author_id, source) VALUES (?, ?, ?)",
                (content, author_id, source),
            )
            return int(cur.lastrowid)

    store = _load_store(db_name)
    next_id = max((item["id"] for item in store["memory_entries"]), default=0) + 1
    store["memory_entries"].append(
        {
            "id": next_id,
            "content": content,
            "author_id": author_id,
            "source": source,
            "created_at": _utc_now(),
        }
    )
    _save_store(db_name, store)
    return next_id


def list_memories(db_name: str, limit: int = 10) -> list[dict]:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            rows = conn.execute(
                """
                SELECT id, content, author_id, source, created_at
                FROM memory_entries
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    memories = list(_load_store(db_name)["memory_entries"])
    memories.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return memories[:limit]


def delete_memory(db_name: str, memory_id: int) -> bool:
    """Delete a single memory entry by ID. Returns True if a row was deleted."""
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            cur = conn.execute("DELETE FROM memory_entries WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    store = _load_store(db_name)
    before = len(store["memory_entries"])
    store["memory_entries"] = [e for e in store["memory_entries"] if e["id"] != memory_id]
    if len(store["memory_entries"]) < before:
        _save_store(db_name, store)
        return True
    return False


def vacuum_db(db_name: str) -> bool:
    """Run VACUUM to reclaim disk space and defragment the SQLite file."""
    if not _sqlite_available(db_name):
        return False
    with _connect(db_name) as conn:
        conn.execute("VACUUM")
    return True


def get_all_memories(db_name: str) -> list[dict]:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            rows = conn.execute(
                "SELECT id, content, author_id, source, created_at FROM memory_entries ORDER BY id ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    return list(_load_store(db_name)["memory_entries"])


def replace_all_memories(db_name: str, contents: list[str], author_id: str = "", source: str = "db_refresh") -> list[int]:
    init_db(db_name)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            conn.execute("DELETE FROM memory_entries")
            ids = []
            for content in contents:
                cur = conn.execute(
                    "INSERT INTO memory_entries (content, author_id, source) VALUES (?, ?, ?)",
                    (content, author_id, source),
                )
                ids.append(int(cur.lastrowid))
        return ids

    store = _load_store(db_name)
    store["memory_entries"] = []
    ids = []
    for i, content in enumerate(contents, 1):
        store["memory_entries"].append({
            "id": i,
            "content": content,
            "author_id": author_id,
            "source": source,
            "created_at": _utc_now(),
        })
        ids.append(i)
    _save_store(db_name, store)
    return ids


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return (
        0x3000 <= cp <= 0x9FFF   # CJK unified, hiragana, katakana
        or 0xF900 <= cp <= 0xFFEF  # CJK compatibility
        or 0x20000 <= cp <= 0x2FA1F  # CJK extensions B-F
    )


def _tokenize_query(query: str) -> set[str]:
    """Tokenize query using whitespace splitting; add CJK bigrams for Japanese/Chinese text."""
    word_tokens = [
        token.lower()
        for token in query.replace("\n", " ").split()
        if len(token.strip()) >= 2
    ]
    terms: set[str] = set(word_tokens)
    for token in word_tokens:
        if any(_is_cjk(c) for c in token):
            # Character bigrams enable meaningful matching without a morphological analyzer
            for i in range(len(token) - 1):
                terms.add(token[i : i + 2])
    return terms


def find_relevant_memories(db_name: str, query: str, limit: int = 5) -> list[dict]:
    init_db(db_name)
    terms = _tokenize_query(query)
    if _sqlite_available(db_name):
        with _connect(db_name) as conn:
            rows = conn.execute(
                """
                SELECT id, content, author_id, source, created_at
                FROM memory_entries
                ORDER BY created_at DESC, id DESC
                LIMIT 50
                """
            ).fetchall()
        candidates = [dict(row) for row in rows]
    else:
        candidates = list_memories(db_name, limit=50)

    scored: list[tuple[int, dict]] = []
    for row in candidates:
        content = row["content"].lower()
        score = sum(1 for term in terms if term in content)
        if not terms:
            score = 1
        elif score == 0:
            continue
        scored.append((score, row))

    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], -item[1]["id"]))
    return [dict(row) for _, row in scored[:limit]]
