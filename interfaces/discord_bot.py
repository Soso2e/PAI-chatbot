import os
import json
import discord
from discord.ext import commands
from core import chat_controller

_PREFIX = "!"
# channel_id -> db_name
_channel_db: dict[int, str] = {}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=_PREFIX, intents=intents)


def _session(channel_id: int) -> str:
    return f"discord-{channel_id}"


def _db(channel_id: int) -> str:
    cfg_path = __file__.replace("interfaces/discord_bot.py", "config/app.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            default = json.load(f).get("default_db", "general")
    except Exception:
        default = "general"
    return _channel_db.get(channel_id, default)


# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user} (id={bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # メンション処理
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        text = message.content
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        text = text.strip()
        if text:
            async with message.channel.typing():
                reply = await chat_controller.process(
                    text, _session(message.channel.id), _db(message.channel.id)
                )
            await message.reply(reply)
            return

    await bot.process_commands(message)


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="chat")
async def cmd_chat(ctx: commands.Context, *, text: str):
    """!chat <メッセージ> で会話する"""
    async with ctx.typing():
        reply = await chat_controller.process(
            text, _session(ctx.channel.id), _db(ctx.channel.id)
        )
    await ctx.reply(reply)


@bot.command(name="db")
async def cmd_db(ctx: commands.Context, name: str = ""):
    """!db <db名> でDB切り替え / !db list で一覧表示"""
    if name == "list" or name == "":
        dbs = chat_controller.available_dbs()
        current = _db(ctx.channel.id)
        lines = [f"{'→ ' if d == current else '  '}`{d}`" for d in dbs]
        await ctx.send("**利用可能なDB:**\n" + "\n".join(lines))
        return

    if name not in chat_controller.available_dbs():
        await ctx.send(f"`{name}` というDBは存在しません。`!db list` で確認してください。")
        return

    _channel_db[ctx.channel.id] = name
    await ctx.send(f"このチャンネルのDBを `{name}` に切り替えました。")


@bot.command(name="memory")
async def cmd_memory(ctx: commands.Context, sub: str = ""):
    """!memory clear でこのチャンネルの会話履歴をクリア"""
    if sub == "clear":
        n = chat_controller.clear_session(_db(ctx.channel.id), _session(ctx.channel.id))
        await ctx.send(f"会話履歴をクリアしました（{n}件削除）。")
    else:
        await ctx.send("使い方: `!memory clear`")


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    """!status で現在のDB・LLM設定を表示"""
    import json
    from pathlib import Path
    db_name = _db(ctx.channel.id)
    llm_cfg_path = Path(__file__).parent.parent / "config" / "llm.json"
    with open(llm_cfg_path, encoding="utf-8") as f:
        llm = json.load(f)
    embed = discord.Embed(title="PAI-Chatbot Status", color=0x5865F2)
    embed.add_field(name="DB", value=f"`{db_name}`", inline=True)
    embed.add_field(name="LLM Provider", value=f"`{llm['provider']}`", inline=True)
    embed.add_field(name="Model", value=f"`{llm['model']}`", inline=True)
    embed.add_field(name="Endpoint", value=f"`{llm['base_url']}`", inline=False)
    await ctx.send(embed=embed)


def run(token: str):
    bot.run(token)
