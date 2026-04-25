import os
import asyncio
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from core import chat_controller

app = AsyncApp(token=os.environ.get("SLACK_BOT_TOKEN"))

# channel_id -> db_name
_channel_db: dict[str, str] = {}


def _db(channel_id: str) -> str:
    import json
    from pathlib import Path
    cfg = Path(__file__).parent.parent / "config" / "app.json"
    try:
        with open(cfg, encoding="utf-8") as f:
            default = json.load(f).get("default_db", "general")
    except Exception:
        default = "general"
    return _channel_db.get(channel_id, default)


def _session(channel_id: str) -> str:
    return f"slack-{channel_id}"


# ── メンション処理 ────────────────────────────────────────────────────────────

@app.event("app_mention")
async def handle_mention(event, say):
    text: str = event.get("text", "")
    # <@BOT_ID> を除去
    import re
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if not text:
        return
    channel = event["channel"]
    reply = await chat_controller.process(text, _session(channel), _db(channel))
    await say(text=reply, thread_ts=event.get("ts"))


# ── スラッシュコマンド ────────────────────────────────────────────────────────

@app.command("/pai")
async def cmd_pai(ack, body, say):
    await ack()
    text = body.get("text", "").strip()
    channel = body["channel_id"]
    if not text:
        await say("使い方: `/pai <メッセージ>`")
        return
    reply = await chat_controller.process(text, _session(channel), _db(channel))
    await say(reply)


@app.command("/pai-db")
async def cmd_db(ack, body, say):
    await ack()
    name = body.get("text", "").strip()
    channel = body["channel_id"]

    if not name or name == "list":
        dbs = chat_controller.available_dbs()
        current = _db(channel)
        lines = [f"{'→ ' if d == current else '　'}`{d}`" for d in dbs]
        await say("*利用可能なDB:*\n" + "\n".join(lines))
        return

    if name not in chat_controller.available_dbs():
        await say(f"`{name}` というDBは存在しません。`/pai-db list` で確認してください。")
        return

    _channel_db[channel] = name
    await say(f"このチャンネルのDBを `{name}` に切り替えました。")


@app.command("/pai-status")
async def cmd_status(ack, body, say):
    await ack()
    import json
    from pathlib import Path
    channel = body["channel_id"]
    db_name = _db(channel)
    llm_path = Path(__file__).parent.parent / "config" / "llm.json"
    with open(llm_path, encoding="utf-8") as f:
        llm = json.load(f)
    await say(
        f"*PAI-Chatbot Status*\n"
        f"• DB: `{db_name}`\n"
        f"• Provider: `{llm['provider']}`\n"
        f"• Model: `{llm['model']}`\n"
        f"• Endpoint: `{llm['base_url']}`"
    )


async def run():
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    await handler.start_async()
