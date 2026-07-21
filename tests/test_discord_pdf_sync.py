import datetime
import types
import unittest
from unittest.mock import AsyncMock, patch

from core import discord_pdf_sync


class FakeAttachment:
    def __init__(self, attachment_id, filename):
        self.id = attachment_id
        self.filename = filename


class FakeMessage:
    def __init__(self, attachments, *, bot=False, created_at=None):
        self.attachments = attachments
        self.author = types.SimpleNamespace(bot=bot)
        self.created_at = created_at or datetime.datetime.now(datetime.timezone.utc)


class CollectPdfAttachmentsTests(unittest.TestCase):
    def test_collects_attachment_only_pdf_and_ignores_non_pdf(self):
        messages = [
            FakeMessage([FakeAttachment(10, "guide.PDF"), FakeAttachment(11, "notes.txt")])
        ]

        result = discord_pdf_sync.collect_pdf_attachments(messages, set())

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "discord-pdf:10:guide.PDF")

    def test_skips_pdf_ingested_by_legacy_filename_source(self):
        messages = [FakeMessage([FakeAttachment(10, "guide.pdf")])]

        result = discord_pdf_sync.collect_pdf_attachments(messages, {"guide.pdf"})

        self.assertEqual(result, [])


class InstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_merges_pdf_counts_and_errors(self):
        original = AsyncMock(
            return_value={"channels_processed": 2, "total_chunks": 5, "errors": ["text error"]}
        )
        module = types.SimpleNamespace(_sync_guild_to_rag=original)

        with patch.object(
            discord_pdf_sync,
            "sync_guild_pdfs",
            AsyncMock(return_value={"pdfs_processed": 1, "pdf_chunks": 3, "errors": ["pdf error"]}),
        ):
            discord_pdf_sync.install(module)
            result = await module._sync_guild_to_rag(
                "guild",
                "general",
                mode="all",
                limit_per_channel=500,
                after=None,
            )

        self.assertEqual(result["total_chunks"], 8)
        self.assertEqual(result["pdfs_processed"], 1)
        self.assertEqual(result["pdf_chunks"], 3)
        self.assertEqual(result["errors"], ["text error", "pdf error"])

    async def test_install_is_idempotent(self):
        original = AsyncMock(return_value={"total_chunks": 0, "errors": []})
        module = types.SimpleNamespace(_sync_guild_to_rag=original)

        discord_pdf_sync.install(module)
        wrapped_once = module._sync_guild_to_rag
        discord_pdf_sync.install(module)

        self.assertIs(module._sync_guild_to_rag, wrapped_once)


if __name__ == "__main__":
    unittest.main()
