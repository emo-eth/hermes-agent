"""Tests for Discord startup missed-message backfill."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import sys

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return

    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.MessageType = SimpleNamespace(default="default", reply="reply")
    discord_mod.Object = lambda id: SimpleNamespace(id=id)
    discord_mod.ui = SimpleNamespace(View=object, button=lambda *a, **k: (lambda fn: fn), Button=object)
    discord_mod.ButtonStyle = SimpleNamespace(success=1, primary=2, secondary=2, danger=3, green=1, grey=2, blurple=2, red=3)
    discord_mod.Color = SimpleNamespace(orange=lambda: 1, green=lambda: 2, blue=lambda: 3, red=lambda: 4, purple=lambda: 5)
    discord_mod.Interaction = object
    discord_mod.Embed = MagicMock
    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules.setdefault("discord", discord_mod)
    sys.modules.setdefault("discord.ext", ext_mod)
    sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

import plugins.platforms.discord.adapter as discord_platform  # noqa: E402
from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


class FakeChannel:
    def __init__(self, channel_id=123, messages=None):
        self.id = channel_id
        self.name = "wiki-inbox"
        self.guild = SimpleNamespace(name="emo")
        self.topic = None
        self._messages = list(messages or [])

    def history(self, **kwargs):
        async def _iter():
            for message in self._messages:
                yield message
        return _iter()


class FakeThread(FakeChannel):
    def __init__(self, channel_id=456, parent=None, messages=None):
        super().__init__(channel_id=channel_id, messages=messages)
        self.parent = parent
        self.parent_id = getattr(parent, "id", None)


def make_message(message_id, *, channel=None, author_id=42, bot=False, content="ingest this"):
    return SimpleNamespace(
        id=message_id,
        content=content,
        clean_content=content,
        attachments=[],
        author=SimpleNamespace(id=author_id, bot=bot, display_name=f"user-{author_id}"),
        channel=channel,
        thread=None,
        type=discord_platform.discord.MessageType.default,
        created_at=datetime.now(timezone.utc),
        reference=None,
    )


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(discord_platform.discord, "Thread", FakeThread, raising=False)
    monkeypatch.setenv("DISCORD_MISSED_MESSAGE_BACKFILL", "true")
    monkeypatch.setenv("DISCORD_MISSED_MESSAGE_BACKFILL_CHANNELS", "123")
    config = PlatformConfig(enabled=True, token="fake-token")
    adapter = DiscordAdapter(config)
    adapter._handle_message = AsyncMock()
    adapter._client = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_channel=lambda channel_id: None,
    )
    return adapter


@pytest.mark.asyncio
async def test_backfill_dispatches_unhandled_free_response_message(adapter):
    channel = FakeChannel(channel_id=123)
    message = make_message(1, channel=channel)
    channel._messages = [message]
    adapter._client.get_channel = lambda channel_id: channel

    await adapter._run_missed_message_backfill()

    adapter._handle_message.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_backfill_skips_message_with_substantive_thread_response(adapter):
    parent = FakeChannel(channel_id=123)
    message = make_message(1, channel=parent)
    thread = FakeThread(channel_id=456, parent=parent)
    bot_reply = make_message(2, channel=thread, author_id=999, bot=True, content="done")
    thread._messages = [bot_reply]
    message.thread = thread
    parent._messages = [message]
    adapter._client.get_channel = lambda channel_id: parent

    await adapter._run_missed_message_backfill()

    adapter._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_parent_channel_unreferenced_bot_message_does_not_mask_miss(adapter):
    parent = FakeChannel(channel_id=123)
    message = make_message(1, channel=parent)
    unrelated_bot_reply = make_message(2, channel=parent, author_id=999, bot=True, content="handled something else")
    parent._messages = [message, unrelated_bot_reply]
    adapter._client.get_channel = lambda channel_id: parent

    await adapter._run_missed_message_backfill()

    adapter._handle_message.assert_awaited_once_with(message)
