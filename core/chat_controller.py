import json
import re
from pathlib import Path

from core.context_builder import build_messages, list_available_dbs
from core.db_registry import bind_guild_db, register_db, verify_db_password
from core.llm_client import LLMClient
from core.memory_manager import (
    clear_history,
    delete_memory,
    init_db,
    list_all_memories,
    list_memories,
    save_memory,
    save_message,
    update_memory,
)

_llm = LLMClient()
_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_MEMORY_JSON_RE = re.compile(r"\[[\s\S]*\]")
_OBJECT_JSON_RE = re.compile(r"\{[\s\S]*\}")
_HISTORY_LINE_RE = re.compile(r"^\[(?P<user_id>\d+)\|(?P<name>[^\]]+)\]:\s*(?P<content>.+)$")
_SELF_NAME_PATTERNS = [
    re.compile(r"(?:ぼく|僕|おれ|俺|わたし|私)[はって]?\s*(?P<alias>[^\s。、「」]+?)\s*(?:っていう|って言う|です|だよ|だ|といいます|と言います)"),
    re.compile(r"(?P<alias>[^\s。、「」]+?)\s*(?:って呼んで|ってよんで|と呼んで|でいいよ)"),
]


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


def _memory_organization_messages(db_name: str, memories: list[dict]) -> list[dict]:
    items = [
        {
            "id": item["id"],
            "content": item["content"],
            "author_id": item.get("author_id", ""),
            "source": item.get("source", ""),
        }
        for item in memories
    ]
    prompt = (
        "You organize a long-term memory database.\n"
        "Return JSON only.\n"
        "Output schema:\n"
        "{\n"
        '  "operations": [\n'
        "    {\n"
        '      "action": "keep|add|update|delete",\n'
        '      "id": 1,\n'
        '      "content": "normalized memory text",\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        f"- Database name: {db_name}\n"
        "- Consolidate duplicates and near-duplicates.\n"
        "- Normalize wording into short standalone Japanese sentences.\n"
        "- Keep durable facts, preferences, constraints, recurring workflows, project context, and promises.\n"
        "- Delete stale, trivial, contradictory, or redundant memories when a better canonical memory exists.\n"
        "- Use update when an existing item should be rewritten.\n"
        "- Use add only when an important canonical memory is missing from the list.\n"
        "- Never invent facts not supported by the provided memory list.\n"
        "- If nothing should change, return {\"operations\": []}.\n"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps({"memories": items}, ensure_ascii=False, indent=2),
        },
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


def _parse_memory_organization_ops(raw_text: str) -> list[dict]:
    text = raw_text.strip()
    data = None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _OBJECT_JSON_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None

    if not isinstance(data, dict):
        return []

    operations = data.get("operations")
    if not isinstance(operations, list):
        return []

    parsed: list[dict] = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        if action not in {"keep", "add", "update", "delete"}:
            continue
        parsed.append(
            {
                "action": action,
                "id": item.get("id"),
                "content": str(item.get("content", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
            }
        )
    return parsed


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
) -> list[dict]:
    cleaned_lines = [line.strip() for line in history_lines if line and line.strip()]
    if not cleaned_lines:
        return []

    rule_based_candidates = _extract_rule_based_memories(cleaned_lines)
    history_text = "\n".join(cleaned_lines[-120:])
    raw = await _llm.chat(_memory_extraction_messages(history_text))
    candidates = rule_based_candidates + _parse_memory_candidates(raw)
    if not candidates:
        return []

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
    if saved:
        await auto_organize_memories(db_name)
    return saved


def _organization_enabled(db_name: str) -> bool:
    cfg_path = _db_dir(db_name) / "config.json"
    if not cfg_path.exists():
        return True
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return True

    policy = cfg.get("memory_policy", {})
    return bool(policy.get("auto_organize", True))


async def organize_memories(
    db_name: str,
    *,
    min_entries: int = 5,
    source: str = "memory_organizer",
) -> dict:
    init_db(db_name)
    memories = list_all_memories(db_name)
    if len(memories) < min_entries:
        return {"applied": False, "reason": "not_enough_memories", "stats": {"total": len(memories)}}

    raw = await _llm.chat(_memory_organization_messages(db_name, memories))
    operations = _parse_memory_organization_ops(raw)
    if not operations:
        return {"applied": False, "reason": "no_operations", "stats": {"total": len(memories)}}

    by_id = {item["id"]: item for item in list_all_memories(db_name)}
    existing_texts = {
        _normalize_memory_text(item["content"]).lower()
        for item in by_id.values()
    }
    stats = {"kept": 0, "added": 0, "updated": 0, "deleted": 0, "skipped": 0}

    for op in operations:
        action = op["action"]
        memory_id = op["id"]
        normalized = _normalize_memory_text(op["content"])

        if action == "keep":
            stats["kept"] += 1
            continue

        if action == "add":
            if not normalized:
                stats["skipped"] += 1
                continue
            key = normalized.lower()
            if key in existing_texts:
                stats["skipped"] += 1
                continue
            new_id = save_memory(db_name, normalized, source=source)
            by_id[new_id] = {"id": new_id, "content": normalized}
            existing_texts.add(key)
            stats["added"] += 1
            continue

        if not isinstance(memory_id, int) or memory_id not in by_id:
            stats["skipped"] += 1
            continue

        if action == "delete":
            deleted = delete_memory(db_name, memory_id)
            if deleted:
                old_key = _normalize_memory_text(by_id[memory_id]["content"]).lower()
                existing_texts.discard(old_key)
                del by_id[memory_id]
                stats["deleted"] += 1
            else:
                stats["skipped"] += 1
            continue

        if action == "update":
            if not normalized:
                stats["skipped"] += 1
                continue
            current_key = _normalize_memory_text(by_id[memory_id]["content"]).lower()
            next_key = normalized.lower()
            if next_key != current_key and next_key in existing_texts:
                deleted = delete_memory(db_name, memory_id)
                if deleted:
                    existing_texts.discard(current_key)
                    del by_id[memory_id]
                    stats["deleted"] += 1
                else:
                    stats["skipped"] += 1
                continue
            updated = update_memory(db_name, memory_id, normalized, source=source)
            if updated:
                existing_texts.discard(current_key)
                existing_texts.add(next_key)
                by_id[memory_id]["content"] = normalized
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

    changed = stats["added"] > 0 or stats["updated"] > 0 or stats["deleted"] > 0
    return {
        "applied": changed,
        "reason": "updated" if changed else "no_effective_changes",
        "stats": stats,
        "total_after": len(list_all_memories(db_name)),
    }


async def auto_organize_memories(
    db_name: str,
    *,
    min_entries: int = 5,
    source: str = "memory_auto_organizer",
) -> dict:
    if not _organization_enabled(db_name):
        return {"applied": False, "reason": "disabled"}
    return await organize_memories(db_name, min_entries=min_entries, source=source)


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


async def remember_and_organize(
    db_name: str,
    content: str,
    author_id: str = "",
    source: str = "manual",
) -> dict:
    memory_id = save_memory(db_name, content, author_id=author_id, source=source)
    organization = await auto_organize_memories(db_name)
    return {"id": memory_id, "organization": organization}


def recent_memories(db_name: str, limit: int = 10) -> list[dict]:
    return list_memories(db_name, limit=limit)
