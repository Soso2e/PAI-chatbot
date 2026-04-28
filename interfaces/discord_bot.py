import asyncio
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
_background_tasks: set[asyncio.Task] = set()


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


def _llm_error_message(error: Exception | str) -> str:
    text = str(error)
    if "timed out" in text.lower():
        return "LLM API がタイムアウトしました。モデル応答が遅いため、`config/llm.json` の `read_timeout` または `timeout` を延ばしてください。"
    return f"LLM API 呼び出しに失敗しました: {text}"


def _track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _memory_capture_result_message(capture_result: dict) -> str:
    saved = capture_result["saved"]
    error = capture_result["error"]

    lines: list[str] = []
    if saved:
        lines.append("長期記憶に保存しました:")
        lines.extend(f"- #{item['id']} {item['content']}" for item in saved)
    if error:
        prefix = "メモリ抽出は一部失敗しました。" if saved else "メモリ抽出は失敗しました。"
        lines.append(prefix)
        lines.append(_llm_error_message(error))
    return "\n".join(lines)


async def _notify_memory_capture_result(
    capture_task: asyncio.Task,
    send_message,
) -> None:
    try:
        capture_result = await capture_task
    except Exception as exc:
        await send_message(f"メモリ抽出は失敗しました。\n{_llm_error_message(exc)}")
        return

    message = _memory_capture_result_message(capture_result)
    if message:
        await send_message(message)


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
        rows.append(f"[{item.author.id}|{author}]: {content}")
    rows.reverse()
    return rows


async def _capture_channel_memories(
    channel: discord.abc.Messageable,
    guild_id: int | None,
    author_id: str,
    source: str = "discord_capture",
    limit: int = 40,
) -> dict:
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
        "このコマンドを使用するには`サーバーの管理`権限が必要です。",
        ephemeral=True,
    )


def _status_embed(guild_id: int | None) -> discord.Embed:
    db_name = _db(guild_id)
    llm = _load_llm_config()
    embed = discord.Embed(title="PAI-Chatbot ステータス", color=0x5865F2)
    embed.add_field(name="DB", value=f"`{db_name}`", inline=True)
    embed.add_field(name="LLMプロバイダー", value=f"`{llm['provider']}`", inline=True)
    embed.add_field(name="モデル", value=f"`{llm['model']}`", inline=True)
    embed.add_field(name="エンドポイント", value=f"`{llm['base_url']}`", inline=False)
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


db_group = app_commands.Group(name="db", description="このDiscordサーバーのメモリDBを管理する")
memory_group = app_commands.Group(name="memory", description="このDiscordサーバーの長期記憶を管理する")


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
            capture_task = None
            if _should_capture_memory(text):
                capture_task = asyncio.create_task(
                    _capture_channel_memories(
                        message.channel,
                        message.guild.id if message.guild else None,
                        str(message.author.id),
                        source="discord_auto",
                    )
                )
            async with message.channel.typing():
                try:
                    reply = await chat_controller.process(
                        text,
                        _session(message.channel.id),
                        _db(message.guild.id if message.guild else None),
                    )
                except RuntimeError as exc:
                    if capture_task is not None:
                        capture_task.cancel()
                    await message.reply(_llm_error_message(exc))
                    return
            await message.reply(reply)
            if capture_task is not None:
                _track_background_task(
                    asyncio.create_task(
                        _notify_memory_capture_result(
                            capture_task,
                            lambda content: message.channel.send(content),
                        )
                    )
                )
            return

    await bot.process_commands(message)


@bot.command(name="chat")
async def cmd_chat(ctx: commands.Context, *, text: str):
    capture_task = None
    if _should_capture_memory(text):
        capture_task = asyncio.create_task(
            _capture_channel_memories(
                ctx.channel,
                ctx.guild.id if ctx.guild else None,
                str(ctx.author.id),
                source="discord_auto",
            )
        )
    async with ctx.typing():
        try:
            reply = await chat_controller.process(
                text,
                _session(ctx.channel.id),
                _db(ctx.guild.id if ctx.guild else None),
            )
        except RuntimeError as exc:
            if capture_task is not None:
                capture_task.cancel()
            await ctx.reply(_llm_error_message(exc))
            return
    await ctx.reply(reply)
    if capture_task is not None:
        _track_background_task(
            asyncio.create_task(
                _notify_memory_capture_result(
                    capture_task,
                    lambda content: ctx.send(content),
                )
            )
        )


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    await ctx.send(embed=_status_embed(ctx.guild.id if ctx.guild else None))


@bot.tree.command(name="chat", description="現在のチャンネルでボットとチャットする")
@app_commands.describe(text="ボットに回答させたいメッセージ")
async def slash_chat(interaction: discord.Interaction, text: str):
    if interaction.channel is None:
        await interaction.response.send_message("このコマンドはチャンネル内でのみ使用できます。", ephemeral=True)
        return

    capture_task = None
    if _should_capture_memory(text):
        capture_task = asyncio.create_task(
            _capture_channel_memories(
                interaction.channel,
                interaction.guild.id if interaction.guild else None,
                str(interaction.user.id),
                source="discord_auto",
            )
        )
    await interaction.response.defer(thinking=True)
    try:
        reply = await chat_controller.process(
            text,
            _session(interaction.channel.id),
            _db(interaction.guild.id if interaction.guild else None),
        )
    except RuntimeError as exc:
        if capture_task is not None:
            capture_task.cancel()
        await interaction.followup.send(_llm_error_message(exc), ephemeral=True)
        return
    await interaction.followup.send(reply)
    if capture_task is not None:
        _track_background_task(
            asyncio.create_task(
                _notify_memory_capture_result(
                    capture_task,
                    lambda content: interaction.followup.send(content),
                )
            )
        )


@bot.tree.command(name="status", description="現在のDBとモデル設定を表示する")
async def slash_status(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=_status_embed(interaction.guild.id if interaction.guild else None),
        ephemeral=True,
    )


@db_group.command(name="list", description="このボットで使用できるDBの一覧を表示する")
async def db_list(interaction: discord.Interaction):
    current = _db(interaction.guild.id if interaction.guild else None)
    dbs = chat_controller.available_dbs()
    lines = [f"{'*' if d == current else '-'} `{d}`" for d in dbs]
    content = "**利用可能なDB**\n" + "\n".join(lines) if lines else "まだDBがありません。"
    await interaction.response.send_message(content, ephemeral=True)


@db_group.command(name="current", description="このDiscordサーバーが使用しているDBを表示する")
async def db_current(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"このサーバーは `{_db(interaction.guild.id if interaction.guild else None)}` を使用しています。",
        ephemeral=True,
    )


@db_group.command(name="create", description="新しいメモリDBを作成してこのDiscordサーバーに紐付ける")
@app_commands.describe(
    db_name="作成するDB名。英数字、'_'、'-' が使用可能",
    password="後でDBを切り替える際に必要なパスワード",
)
async def db_create(interaction: discord.Interaction, db_name: str, password: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
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
        f"DB `{db_name}` を作成してこのサーバーに紐付けました。",
        ephemeral=True,
    )


@db_group.command(name="use", description="このDiscordサーバーを既存のメモリDBに切り替える")
@app_commands.describe(
    db_name="このサーバーで使用するDB名",
    password="対象DBのパスワード",
)
async def db_use(interaction: discord.Interaction, db_name: str, password: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
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
        f"このサーバーは `{db_name}` を使用するようになりました。",
        ephemeral=True,
    )


class _RefreshConfirmView(discord.ui.View):
    def __init__(self, db_name: str, author_id: str):
        super().__init__(timeout=30)
        self._db_name = db_name
        self._author_id = author_id

    @discord.ui.button(label="実行する", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="長期記憶を再構成中...", view=None)
        result = await chat_controller.consolidate_memories(self._db_name, author_id=self._author_id)
        if result["after"] == 0:
            await interaction.edit_original_response(content="長期記憶が見つからなかったため、何もしませんでした。")
            self.stop()
            return
        lines = [f"`#{e['id']}` {e['content']}" for e in result["entries"]]
        summary = (
            f"DB `{self._db_name}` の長期記憶を再構成しました。\n"
            f"{result['before']} 件 → {result['after']} 件\n\n"
            + "\n".join(lines)
        )
        await interaction.edit_original_response(content=summary)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="キャンセルしました。", view=None)
        self.stop()


@memory_group.command(name="optimize", description="長期記憶をAIで再構成し、重複・分散した情報を整理する")
async def memory_optimize(interaction: discord.Interaction):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
        return
    if not _has_manage_guild(interaction):
        await _send_permission_error(interaction)
        return

    db_name = _db(interaction.guild.id)
    view = _RefreshConfirmView(db_name, str(interaction.user.id))
    await interaction.response.send_message(
        f"DB `{db_name}` の長期記憶をAIで再構成します。\n"
        "密度が高い記憶の分割・重複の統合が行われます。元の記憶は置き換えられます。",
        view=view,
        ephemeral=True,
    )


@memory_group.command(name="save", description="このサーバーのDBに長期記憶を保存する")
@app_commands.describe(text="ボットに覚えさせたい内容")
async def memory_save(interaction: discord.Interaction, text: str):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
        return
    if not text.strip():
        await interaction.response.send_message("保存するテキストを入力してください。", ephemeral=True)
        return

    db_name = _db(interaction.guild.id)
    memory_id = chat_controller.remember(
        db_name,
        text.strip(),
        author_id=str(interaction.user.id),
        source="discord_manual",
    )
    await interaction.response.send_message(
        f"`{db_name}` に記憶 #{memory_id} を保存しました。",
        ephemeral=True,
    )


@memory_group.command(name="list", description="このサーバーに保存された最近の長期記憶を表示する")
async def memory_list(interaction: discord.Interaction):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
        return

    db_name = _db(interaction.guild.id)
    items = chat_controller.recent_memories(db_name, limit=5)
    if not items:
        await interaction.response.send_message("まだ保存された記憶はありません。", ephemeral=True)
        return

    lines = [f"`#{item['id']}` {item['content']}" for item in items]
    await interaction.response.send_message(
        "**最近の記憶**\n" + "\n".join(lines),
        ephemeral=True,
    )


@memory_group.command(name="capture", description="このチャンネルの最近のメッセージから長期記憶を抽出して保存する")
@app_commands.describe(limit="調査する最近のメッセージ数（10〜100）")
async def memory_capture(interaction: discord.Interaction, limit: app_commands.Range[int, 10, 100] = 40):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
        return
    if interaction.channel is None:
        await interaction.response.send_message("このコマンドはチャンネル内でのみ使用できます。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    capture_result = await _capture_channel_memories(
        interaction.channel,
        interaction.guild.id,
        str(interaction.user.id),
        source="discord_manual_capture",
        limit=limit,
    )
    saved = capture_result["saved"]
    if capture_result["error"]:
        message = "メモリ抽出は失敗しました。"
        if saved:
            lines = [f"`#{item['id']}` {item['content']}" for item in saved]
            message += "\nただし、ルールベースで抽出できた内容は保存しました:\n" + "\n".join(lines)
        message += f"\n\n詳細: {_llm_error_message(capture_result['error'])}"
        await interaction.followup.send(message, ephemeral=True)
        return
    if not saved:
        await interaction.followup.send("保存候補は見つかりませんでした。", ephemeral=True)
        return

    lines = [f"`#{item['id']}` {item['content']}" for item in saved]
    await interaction.followup.send(
        "長期記憶に保存しました:\n" + "\n".join(lines),
        ephemeral=True,
    )


@memory_group.command(name="clear", description="現在のチャンネルのチャット履歴を消去する")
async def memory_clear(interaction: discord.Interaction):
    if not _require_guild(interaction):
        await interaction.response.send_message("このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True)
        return

    deleted = chat_controller.clear_session(
        _db(interaction.guild.id),
        _session(interaction.channel.id),
    )
    await interaction.response.send_message(
        f"このチャンネルのチャット履歴を消去しました。{deleted} 件のメッセージを削除しました。",
        ephemeral=True,
    )


bot.tree.add_command(db_group)
bot.tree.add_command(memory_group)


def run(token: str):
    bot.run(token)
