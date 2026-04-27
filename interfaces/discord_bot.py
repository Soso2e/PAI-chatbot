import json
from pathlib import Path

import discord
from discord.ext import commands

from core import chat_controller
from core.db_registry import get_guild_db

_PREFIX = "!"
APP_CONFIG_PATH = Path(__file__).parent.parent / "config" / "app.json"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=_PREFIX, intents=intents)


def _session(channel_id: int) -> str:
    return f"discord-{channel_id}"


def _default_db() -> str:
    try:
        with open(APP_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("default_db", "general")
    except Exception:
        return "general"


def _db(guild_id: int | None) -> str:
    if guild_id is None:
        return _default_db()
    return get_guild_db(guild_id) or _default_db()


@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user} (id={bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user.mentioned_in(message) and not message.mention_everyone:
        text = message.content
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        text = text.strip()
        if text:
            async with message.channel.typing():
                reply = await chat_controller.process(
                    text,
                    _session(message.channel.id),
                    _db(message.guild.id if message.guild else None),
                )
            await message.reply(reply)
            return

    await bot.process_commands(message)


@bot.command(name="chat")
async def cmd_chat(ctx: commands.Context, *, text: str):
    async with ctx.typing():
        reply = await chat_controller.process(
            text,
            _session(ctx.channel.id),
            _db(ctx.guild.id if ctx.guild else None),
        )
    await ctx.reply(reply)


@bot.command(name="db")
async def cmd_db(ctx: commands.Context, action: str = "", name: str = "", password: str = ""):
    current = _db(ctx.guild.id if ctx.guild else None)

    if action in ("", "list"):
        dbs = chat_controller.available_dbs()
        lines = [f"{'*' if d == current else '-'} `{d}`" for d in dbs]
        await ctx.send("**Available DBs**\n" + "\n".join(lines))
        return

    if action == "current":
        await ctx.send(f"This server is using `{current}`.")
        return

    if ctx.guild is None:
        await ctx.send("This command can only be used inside a Discord server.")
        return

    if action == "create":
        if not name or not password:
            await ctx.send("Usage: `!db create <db_name> <password>`")
            return
        try:
            chat_controller.create_db(name, password, ctx.guild.id)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        await ctx.send(f"Created DB `{name}` and bound it to this server.")
        return

    if action == "use":
        if not name or not password:
            await ctx.send("Usage: `!db use <db_name> <password>`")
            return
        try:
            chat_controller.switch_guild_db(ctx.guild.id, name, password)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        await ctx.send(f"This server now uses `{name}`.")
        return

    await ctx.send("Usage: `!db list`, `!db current`, `!db create <db_name> <password>`, `!db use <db_name> <password>`")


@bot.command(name="memory")
async def cmd_memory(ctx: commands.Context, sub: str = "", *, text: str = ""):
    db_name = _db(ctx.guild.id if ctx.guild else None)

    if sub == "clear":
        n = chat_controller.clear_session(db_name, _session(ctx.channel.id))
        await ctx.send(f"Cleared chat history for this channel. Deleted {n} messages.")
        return

    if sub == "save":
        if not text.strip():
            await ctx.send("Usage: `!memory save <text>`")
            return
        memory_id = chat_controller.remember(
            db_name,
            text.strip(),
            author_id=str(ctx.author.id),
            source="discord_manual",
        )
        await ctx.send(f"Saved memory #{memory_id} to `{db_name}`.")
        return

    if sub == "list":
        items = chat_controller.recent_memories(db_name, limit=5)
        if not items:
            await ctx.send("No saved memories yet.")
            return
        lines = [f"`#{item['id']}` {item['content']}" for item in items]
        await ctx.send("**Recent memories**\n" + "\n".join(lines))
        return

    await ctx.send("Usage: `!memory clear`, `!memory save <text>`, `!memory list`")


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    db_name = _db(ctx.guild.id if ctx.guild else None)
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
