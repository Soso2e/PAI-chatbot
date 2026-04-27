import json
from pathlib import Path
from core.memory_manager import find_relevant_memories, get_history

DB_BASE = Path(__file__).parent.parent / "databases"


def _load_db_config(db_name: str) -> dict:
    path = DB_BASE / db_name / "config.json"
    if not path.exists():
        return {"system_prompt": "You are a helpful assistant.", "memory_policy": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_messages(db_name: str, session_id: str, user_input: str) -> list[dict]:
    cfg = _load_db_config(db_name)
    system_prompt = cfg.get("system_prompt", "You are a helpful assistant.")
    max_ctx = cfg.get("memory_policy", {}).get("max_context_messages", 20)

    history = get_history(db_name, session_id, limit=max_ctx)
    memories = find_relevant_memories(db_name, user_input, limit=5)

    messages = [{"role": "system", "content": system_prompt}]
    if memories:
        memory_lines = "\n".join(f"- {memory['content']}" for memory in memories)
        messages.append(
            {
                "role": "system",
                "content": "Relevant long-term memories:\n" + memory_lines,
            }
        )
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})
    return messages


def list_available_dbs() -> list[str]:
    if not DB_BASE.exists():
        return []
    return [d.name for d in DB_BASE.iterdir() if (d / "config.json").exists()]
