import json
import re
from pathlib import Path

from core.context_builder import build_messages, list_available_dbs
from core.db_registry import bind_guild_db, register_db, verify_db_password
from core.llm_client import LLMClient
from core.memory_manager import (
    clear_history,
    init_db,
    list_memories,
    save_memory,
    save_message,
)

_llm = LLMClient()
_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")


def _db_dir(db_name: str) -> Path:
    return Path(__file__).parent.parent / "databases" / db_name


def _default_db_config(db_name: str) -> dict:
    return {
        "name": db_name,
        "system_prompt": "You are a helpful assistant. Use saved memories when they are relevant and avoid inventing facts.",
        "style": "friendly",
        "memory_policy": {
            "auto_save": True,
            "max_context_messages": 20,
        },
        "allowed_tools": ["save_memory"],
    }


async def process(
    message: str,
    session_id: str,
    db_name: str = "general",
) -> str:
    init_db(db_name)
    messages = build_messages(db_name, session_id, message)
    reply = await _llm.chat(messages)
    save_message(db_name, session_id, "user", message)
    save_message(db_name, session_id, "assistant", reply)
    return reply


def clear_session(db_name: str, session_id: str) -> int:
    return clear_history(db_name, session_id)


def available_dbs() -> list[str]:
    return list_available_dbs()


def create_db(db_name: str, password: str, guild_id: int) -> None:
    if not _DB_NAME_RE.fullmatch(db_name):
        raise ValueError("DB name must be 3-32 chars and use only letters, numbers, '_' or '-'")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    db_dir = _db_dir(db_name)
    db_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = db_dir / "config.json"
    if not cfg_path.exists():
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(_default_db_config(db_name), f, ensure_ascii=False, indent=2)

    init_db(db_name)
    register_db(db_name, password, guild_id)


def switch_guild_db(guild_id: int, db_name: str, password: str) -> None:
    if db_name not in available_dbs():
        raise ValueError(f"DB '{db_name}' not found")
    if not verify_db_password(db_name, password):
        raise ValueError("Invalid password")
    bind_guild_db(guild_id, db_name)


def remember(db_name: str, content: str, author_id: str = "", source: str = "manual") -> int:
    return save_memory(db_name, content, author_id=author_id, source=source)


def recent_memories(db_name: str, limit: int = 10) -> list[dict]:
    return list_memories(db_name, limit=limit)
