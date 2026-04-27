import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from core import chat_controller
from core.db_registry import get_guild_db

_PREFIX = "!"
APP_CONFIG_PATH = Path(__file__).parent.parent / "config" / "app.json"
LLM_CONFIG_PATH = Path(__file__).parent.parent / "config" / "llm.json"
_MEMORY_TRIGGER_PHRASES = (
    "覚えておいて",
    "覚えといて",
    "remember this",
    "remember that",
    "save this to memory",
)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=_PREFIX, intents=intents)
_slash_synced = False


def _load_app_config() -> dict:
    try:
        with open(APP_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_llm_config() -> dict:
    with open(LLM_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _session(channel_id: int) -> str:
    return f"discord-{channel_id}"


def _default_db() -> str:
    return _load_app_config().get("default_db", "general")


def _db(guild_id: int | None) -> str:
    if guild_id is None:
        return _default_db()
    return get_guild_db(guild_id) or _default_db()


def _should_capture_memory(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in text or phrase in lowered for phrase in _MEMORY_TRIGGER_PHRASES)


async def _build_history_lines(
    channel: discord.abc.Messageable,
    limit: int = 40,
) -> list[str]:
    if not hasattr(channel, "history"):
        return []

    rows: list[str] = []
    async for item in channel.history(limit=limit, oldest_first=False):
        author = getattr(item.author, "display_name", None) or getattr(item.author, "name", "unknown")
        content = (item.content or "").strip()
        if not content:
            continue
        rows.append(f"{author}: {content}")
    rows.reverse()
    return rows


async def _capture_channel_memories(
    channel: discord.abc.Messageable,
    guild_id: int | None,
    author_id: str,
    source: str = "discord_capture",
    limit: int = 40,
) -> list[dict]:
    history_lines = await _build_history_lines(channel, limit=limit)
    return await chat_controller.capture_memories_from_history(
        _db(guild_id),
        history_lines,
        author_id=author_id,
        source=source,
    )


def _require_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.channel is not None


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


async def _send_permission_error(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "You need the `Manage Server` permission to use this command.",
        ephemeral=True,
    )


def _status_embed(guild_id: int | None) -> discord.Embed:
    db_name = _db(guild_id)
    llm = _load_llm_config()
    embed = discord.Embed(title="PAI-Chatbot Status", color=0x5865F2)
    embed.add_field(name="DB", value=f"`{db_name}`", inline=True)
    embed.add_field(name="LLM Provider", value=f"`{llm['provider']}`", inline=True)
    embed.add_field(name="Model", value=f"`{llm['model']}`", inline=True)
    embed.add_field(name="Endpoint", value=f"`{llm['base_url']}`", inline=False)
    return embed


async def _sync_slash_commands() -> None:
    global _slash_synced
    if _slash_synced:
        return

    cfg = _load_app_config().get("discord", {})
    guild_id = cfg.get("guild_id") or cfg.get("dev_guild_id")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[Discord] Synced {len(synced)} slash commands to guild {guild_id}")
    else:
        synced = await bot.tree.sync()
        print(f"[Discord] Synced {len(synced)} global slash commands")

    _slash_synced = True


db_group = app_commands.Group(name="db", description="Manage the memory database for this Discord server")
memory_group = app_commands.Group(name="memory", description="Manage long-term memories for this Discord server")


@bot.event
async def on_ready():
    await _sync_slash_commands()
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
                saved = []
                if _should_capture_memory(text):
                    saved = await _capture_channel_memories(
                        message.channel,
                        message.guild.id if message.guild else None,
                        str(message.author.id),
                        source="discord_auto",
                    )
                    if saved:
                        reply += "\n\n長期記憶に保存しました:\n" + "\n".join(
                            f"- #{item['id']} {item['content']}" for item in saved
                        )
                    else:
                        reply += "\n\n確認しましたが、長期記憶として残す内容は見つかりませんでした。"
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
        saved = []
        if _should_capture_memory(text):
            saved = await _capture_channel_memories(
                ctx.channel,
                ctx.guild.id if ctx.guild else None,
                str(ctx.author.id),
                source="discord_auto",
            )
            if saved:
                reply += "\n\n長期記憶に保存しました:\n" + "\n".join(
                    f"- #{item['id']} {item['content']}" for item in saved
                )
            else:
                reply += "\n\n確認しましたが、長期記憶として残す内容は見つかりませんでした。"
    await ctx.reply(reply)


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    await ctx.send(embed=_status_embed(ctx.guild.id if ctx.guild else None))


@bot.tree.command(name="chat", description="Chat with the bot in the current channel")
@app_commands.describe(text="The message you want the bot to answer")
async def slash_chat(interaction: discord.Interaction, text: str):
    if interaction.channel is None:
        await interaction.response.send_message("This command can only be used in a channel.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    reply = await chat_controller.process(
        text,
        _session(interaction.channel.id),
        _db(interaction.guild.id if interaction.guild else None),
    )
    if _should_capture_memory(text):
        saved = await _capture_channel_memories(
            interaction.channel,
            interaction.guild.id if interaction.guild else None,
            str(interaction.user.id),
            source="discord_auto",
        )
        if saved:
            reply += "\n\n長期記憶に保存しました:\n" + "\n".join(
                f"- #{item['id']} {item['content']}" for item in saved
            )
        else:
            reply += "\n\n確認しましたが、長期記憶として残す内容は見つかりませんでした。"
    await interaction.followup.send(reply)


@bot.tree.command(name="status", description="Show the current DB and model settings")
async def slash_status(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=_status_embed(interaction.guild.id if interaction.guild else None),
        ephemeral=True,
    )


@db_group.command(name="list", description="Show the databases that can be used by this bot")
async def db_list(interaction: discord.Interaction):
    current = _db(interaction.guild.id if interaction.guild else None)
    dbs = chat_controller.available_dbs()
    lines = [f"{'*' if d == current else '-'} `{d}`" for d in dbs]
    content = "**Available DBs**\n" + "\n".join(lines) if lines else "No DBs are available yet."
    await interaction.response.send_message(content, ephemeral=True)


@db_group.command(name="current", description="Show which DB this Discord server is using")
async def db_current(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"This server is using `{_db(interaction.guild.id if interaction.guild else None)}`.",
        ephemeral=True,
    )


@db_group.command(name="create", description="Create a new memory DB and bind it to this Discord server")
@app_commands.describe(
    db_name="Name of the DB to create. Use letters, numbers, '_' or '-'",
    password="Password required later when switching this server to the DB",
)
async def db_create(interaction: discord.Interaction, db_name: str, password: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return
    if not _has_manage_guild(interaction):
        await _send_permission_error(interaction)
        return

    try:
        chat_controller.create_db(db_name, password, interaction.guild.id)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"Created DB `{db_name}` and bound it to this server.",
        ephemeral=True,
    )


@db_group.command(name="use", description="Switch this Discord server to an existing memory DB")
@app_commands.describe(
    db_name="Name of the DB to use for this server",
    password="Password for the target DB",
)
async def db_use(interaction: discord.Interaction, db_name: str, password: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return
    if not _has_manage_guild(interaction):
        await _send_permission_error(interaction)
        return

    try:
        chat_controller.switch_guild_db(interaction.guild.id, db_name, password)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"This server now uses `{db_name}`.",
        ephemeral=True,
    )


@memory_group.command(name="save", description="Save a long-term memory into this server's DB")
@app_commands.describe(text="The memory content you want the bot to remember")
async def memory_save(interaction: discord.Interaction, text: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return
    if not text.strip():
        await interaction.response.send_message("Please enter some text to save.", ephemeral=True)
        return

    db_name = _db(interaction.guild.id)
    memory_id = chat_controller.remember(
        db_name,
        text.strip(),
        author_id=str(interaction.user.id),
        source="discord_manual",
    )
    await interaction.response.send_message(
        f"Saved memory #{memory_id} to `{db_name}`.",
        ephemeral=True,
    )


@memory_group.command(name="list", description="Show recent long-term memories saved for this server")
async def memory_list(interaction: discord.Interaction):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return

    db_name = _db(interaction.guild.id)
    items = chat_controller.recent_memories(db_name, limit=5)
    if not items:
        await interaction.response.send_message("No saved memories yet.", ephemeral=True)
        return

    lines = [f"`#{item['id']}` {item['content']}" for item in items]
    await interaction.response.send_message(
        "**Recent memories**\n" + "\n".join(lines),
        ephemeral=True,
    )


@memory_group.command(name="capture", description="Capture durable memories from recent messages in this channel")
@app_commands.describe(limit="How many recent messages to inspect (10-100)")
async def memory_capture(interaction: discord.Interaction, limit: app_commands.Range[int, 10, 100] = 40):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return
    if interaction.channel is None:
        await interaction.response.send_message("This command can only be used in a channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    saved = await _capture_channel_memories(
        interaction.channel,
        interaction.guild.id,
        str(interaction.user.id),
        source="discord_manual_capture",
        limit=limit,
    )
    if not saved:
        await interaction.followup.send("保存候補は見つかりませんでした。", ephemeral=True)
        return

    lines = [f"`#{item['id']}` {item['content']}" for item in saved]
    await interaction.followup.send(
        "長期記憶に保存しました:\n" + "\n".join(lines),
        ephemeral=True,
    )


@memory_group.command(name="clear", description="Clear the chat history for the current channel")
async def memory_clear(interaction: discord.Interaction):
    if not _require_guild(interaction):
        await interaction.response.send_message("This command can only be used inside a Discord server.", ephemeral=True)
        return

    deleted = chat_controller.clear_session(
        _db(interaction.guild.id),
        _session(interaction.channel.id),
    )
    await interaction.response.send_message(
        f"Cleared chat history for this channel. Deleted {deleted} messages.",
        ephemeral=True,
    )


bot.tree.add_command(db_group)
bot.tree.add_command(memory_group)


def run(token: str):
    bot.run(token)
