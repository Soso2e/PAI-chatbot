from core.llm_client import LLMClient
from core.memory_manager import init_db, save_message, clear_history
from core.context_builder import build_messages, list_available_dbs

_llm = LLMClient()


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
