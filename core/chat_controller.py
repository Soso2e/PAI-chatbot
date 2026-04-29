import json
import re
from pathlib import Path

from core.context_builder import build_messages, list_available_dbs
from core.db_registry import bind_guild_db, register_db, verify_db_password
from core.llm_client import LLMClient
from core.memory_manager import (
    clear_history,
    get_all_memories,
    init_db,
    list_memories,
    replace_all_memories,
    save_memory,
    save_message,
    vacuum_db,
)

_llm = LLMClient()
_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_MEMORY_JSON_RE = re.compile(r"\[[\s\S]*\]")
_HISTORY_LINE_RE = re.compile(r"^\[(?P<user_id>\d+)\|(?P<name>[^\]]+)\]:\s*(?P<content>.+)$")
_SELF_NAME_PATTERNS = [
    re.compile(r"(?:ぼく|僕|おれ|俺|わたし|私)[はって]?\s*(?P<alias>[^\s。、「」]+?)\s*(?:っていう|って言う|です|だよ|だ|といいます|と言います)"),
    re.compile(r"(?P<alias>[^\s。、「」]+?)\s*(?:って呼んで|ってよんで|と呼んで|でいいよ)"),
]
_MEMORY_CAPTURE_MAX_LINES = 40
_MEMORY_CAPTURE_MAX_CHARS = 6000


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


def _memory_extraction_messages(history_text: str) -> list[dict]:
    prompt = (
        "You extract durable long-term memories from chat logs.\n"
        "Return JSON only.\n"
        "Output format: [{\"content\": \"...\"}, ...]\n"
        "Rules:\n"
        "- Keep only information worth remembering later.\n"
        "- Prefer user preferences, profile facts, ongoing projects, decisions, promises, recurring workflows, and constraints.\n"
        "- Ignore small talk, one-off jokes, and temporary chatter.\n"
        "- Write each memory as a short standalone sentence in Japanese.\n"
        "- At most 5 items.\n"
        "- Do not duplicate near-identical items.\n"
        "- If nothing is worth saving, return []"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": history_text},
    ]


def _parse_memory_candidates(raw_text: str) -> list[str]:
    text = raw_text.strip()
    candidates: list[str] = []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _MEMORY_JSON_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
        else:
            data = None

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                value = str(item.get("content", "")).strip()
            else:
                value = ""
            if value:
                candidates.append(value)

    if candidates:
        return candidates[:5]

    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*0123456789. ").strip()
        if cleaned:
            candidates.append(cleaned)
    return candidates[:5]


def _normalize_memory_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_rule_based_memories(history_lines: list[str]) -> list[str]:
    memories: list[str] = []

    for line in history_lines:
        match = _HISTORY_LINE_RE.match(line.strip())
        if not match:
            continue

        user_id = match.group("user_id")
        content = match.group("content").strip()
        for pattern in _SELF_NAME_PATTERNS:
            alias_match = pattern.search(content)
            if not alias_match:
                continue
            alias = _normalize_memory_text(alias_match.group("alias"))
            alias = alias.strip("。、「」\"'")
            if len(alias) > 24:
                continue
            if alias:
                memories.append(f"{user_id}: {alias}")
                break

    return memories[:5]


def _prepare_history_for_memory_extraction(history_lines: list[str]) -> str:
    trimmed_lines = [line.strip() for line in history_lines if line and line.strip()]
    if not trimmed_lines:
        return ""

    trimmed_lines = trimmed_lines[-_MEMORY_CAPTURE_MAX_LINES:]
    if len(trimmed_lines) == 1:
        return trimmed_lines[0][-_MEMORY_CAPTURE_MAX_CHARS:]

    result: list[str] = []
    total = 0
    for line in reversed(trimmed_lines):
        line_len = len(line) + 1
        if result and total + line_len > _MEMORY_CAPTURE_MAX_CHARS:
            break
        if not result and line_len > _MEMORY_CAPTURE_MAX_CHARS:
            result.append(line[-_MEMORY_CAPTURE_MAX_CHARS:])
            break
        result.append(line)
        total += line_len
    result.reverse()
    return "\n".join(result)


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


async def capture_memories_from_history(
    db_name: str,
    history_lines: list[str],
    author_id: str = "",
    source: str = "discord_capture",
) -> dict:
    cleaned_lines = [line.strip() for line in history_lines if line and line.strip()]
    if not cleaned_lines:
        return {"saved": [], "error": ""}

    rule_based_candidates = _extract_rule_based_memories(cleaned_lines)
    history_text = _prepare_history_for_memory_extraction(cleaned_lines)
    raw = "[]"
    llm_error = ""
    try:
        raw = await _llm.chat(_memory_extraction_messages(history_text))
    except RuntimeError as exc:
        llm_error = str(exc)
        print(f"[MemoryCapture] LLM extraction skipped due to error: {exc}")
    candidates = rule_based_candidates + _parse_memory_candidates(raw)
    if not candidates:
        return {"saved": [], "error": llm_error}

    existing = {
        _normalize_memory_text(item["content"]).lower()
        for item in list_memories(db_name, limit=100)
    }

    saved: list[dict] = []
    for candidate in candidates:
        normalized = _normalize_memory_text(candidate)
        key = normalized.lower()
        if not normalized or key in existing:
            continue
        memory_id = save_memory(
            db_name,
            normalized,
            author_id=author_id,
            source=source,
        )
        existing.add(key)
        saved.append(
            {
                "id": memory_id,
                "content": normalized,
                "source": source,
            }
        )
    return {"saved": saved, "error": llm_error}


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


def optimize_db(db_name: str) -> bool:
    return vacuum_db(db_name)


_CONSOLIDATE_MEMORIES_PROMPT = (
    "You reorganize a list of long-term memory entries for a chatbot.\n"
    "Given the current memory list, return a reorganized version that:\n"
    "- Splits overly dense entries (containing multiple facts) into atomic, single-fact entries\n"
    "- Merges near-duplicate or highly similar entries into one\n"
    "- Removes redundant information\n"
    "- Keeps each entry as a short, standalone sentence in Japanese\n"
    "- Preserves all distinct facts — do not lose information\n"
    "Output format: [\"memory 1\", \"memory 2\", ...]\n"
    "Return JSON array only, no explanation."
)


async def consolidate_memories(db_name: str, author_id: str = "") -> dict:
    all_memories = get_all_memories(db_name)
    if not all_memories:
        return {"before": 0, "after": 0, "entries": []}

    lines = [f"{i + 1}. {m['content']}" for i, m in enumerate(all_memories)]
    messages = [
        {"role": "system", "content": _CONSOLIDATE_MEMORIES_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]

    raw = await _llm.chat(messages)
    candidates = _parse_memory_candidates(raw)
    normalized = [_normalize_memory_text(c) for c in candidates if c.strip()]
    if not normalized:
        return {"before": len(all_memories), "after": 0, "entries": []}

    new_ids = replace_all_memories(db_name, normalized, author_id=author_id, source="db_refresh")
    entries = [{"id": new_ids[i], "content": normalized[i]} for i in range(len(normalized))]
    return {"before": len(all_memories), "after": len(normalized), "entries": entries}


# ---------------------------------------------------------------------------
# RAG management helpers
# ---------------------------------------------------------------------------

_VALID_RAG_BACKENDS = ("chroma", "json")


def _load_db_config(db_name: str) -> dict:
    path = _db_dir(db_name) / "config.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_db_config(db_name: str, cfg: dict) -> None:
    path = _db_dir(db_name) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def rag_enable(db_name: str) -> None:
    cfg = _load_db_config(db_name)
    cfg.setdefault("rag", {})["enabled"] = True
    _save_db_config(db_name, cfg)


def rag_disable(db_name: str) -> None:
    cfg = _load_db_config(db_name)
    cfg.setdefault("rag", {})["enabled"] = False
    _save_db_config(db_name, cfg)


def rag_set_backend(db_name: str, backend: str) -> None:
    if backend not in _VALID_RAG_BACKENDS:
        raise ValueError(f"backend は {_VALID_RAG_BACKENDS} のいずれかを指定してください")
    cfg = _load_db_config(db_name)
    cfg.setdefault("rag", {})["vector_backend"] = backend
    _save_db_config(db_name, cfg)


def rag_get_status(db_name: str) -> dict:
    from core.rag_manager import collection_stats
    return collection_stats(db_name)
