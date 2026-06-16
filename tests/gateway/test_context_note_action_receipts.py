from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _Adapter:
    def __init__(self):
        self.send = AsyncMock()


def _make_runner(adapter=None) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="fake")},
    )
    runner.adapters = {Platform.DISCORD: adapter} if adapter else {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    runner._decide_image_input_mode = lambda: "text"
    runner._thread_metadata_for_source = lambda source, reply_anchor=None: {"thread_id": source.thread_id}
    runner._reply_anchor_for_event = lambda event: event.message_id
    return runner


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="1501971993405292796",
        thread_id="1514268323544698901",
        chat_type="group",
        user_id="emo",
        user_name="emo",
    )


@pytest.mark.asyncio
async def test_document_context_action_note_sends_visible_receipt_and_prompt_accounting():
    adapter = _Adapter()
    runner = _make_runner(adapter)
    source = _source()
    event = MessageEvent(
        text="make sure these skills are installed for us to use",
        message_type=MessageType.DOCUMENT,
        source=source,
        message_id="msg-1",
        media_urls=["/tmp/123_456_context-note.md"],
        media_types=["text/markdown"],
    )

    prepared = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    adapter.send.assert_awaited_once()
    chat_id, receipt = adapter.send.await_args.args[:2]
    assert chat_id == source.chat_id
    assert "Action item noted" in receipt
    assert "make sure these skills are installed" in receipt
    assert adapter.send.await_args.kwargs["metadata"]["thread_id"] == source.thread_id

    assert "The user sent a text document" in prepared
    assert "Action item from the user's accompanying note" in prepared
    assert "make sure these skills are installed for us to use" in prepared
    assert "explicitly account for this action item" in prepared


@pytest.mark.asyncio
async def test_document_context_non_action_note_does_not_send_receipt():
    adapter = _Adapter()
    runner = _make_runner(adapter)
    source = _source()
    event = MessageEvent(
        text="FYI, source material for later",
        message_type=MessageType.DOCUMENT,
        source=source,
        message_id="msg-2",
        media_urls=["/tmp/123_456_reference.md"],
        media_types=["text/markdown"],
    )

    prepared = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    adapter.send.assert_not_awaited()
    assert "The user sent a text document" in prepared
    assert "Action item from the user's accompanying note" not in prepared
