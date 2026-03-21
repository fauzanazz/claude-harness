from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.compaction import compact_messages, estimate_tokens


def test_estimate_tokens():
    messages = [{"role": "user", "content": "hello world"}]
    tokens = estimate_tokens(messages)
    assert tokens > 0
    assert isinstance(tokens, int)


@pytest.mark.asyncio
async def test_under_budget_no_compaction():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result, summary, did_compact = await compact_messages(
        messages, max_tokens=100_000, existing_summary=""
    )
    assert result == messages
    assert summary == ""
    assert did_compact is False


@pytest.mark.asyncio
async def test_over_budget_triggers_compaction():
    # Create messages that exceed a small token budget
    messages = [
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "y" * 1000},
        {"role": "user", "content": "z" * 1000},
        {"role": "assistant", "content": "w" * 1000},
        {"role": "user", "content": "latest question"},
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Summary of earlier conversation about x, y, z, and w.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("src.compaction.anthropic.AsyncAnthropic", return_value=mock_client):
        result, summary, did_compact = await compact_messages(
            messages, max_tokens=500, existing_summary=""
        )

    assert did_compact is True
    assert len(result) < len(messages)
    assert result[0]["content"].startswith("[Context from earlier")
    assert "Summary of earlier conversation" in summary


@pytest.mark.asyncio
async def test_compaction_preserves_recent_messages():
    messages = [
        {"role": "user", "content": "old " * 500},
        {"role": "assistant", "content": "old response " * 500},
        {"role": "user", "content": "recent question"},
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Earlier discussion summary.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("src.compaction.anthropic.AsyncAnthropic", return_value=mock_client):
        result, summary, did_compact = await compact_messages(
            messages, max_tokens=200, existing_summary=""
        )

    assert did_compact is True
    # The most recent message should be preserved
    assert any("recent question" in m["content"] for m in result)


@pytest.mark.asyncio
async def test_compaction_fallback_on_api_error():
    messages = [
        {"role": "user", "content": "x" * 2000},
        {"role": "user", "content": "latest"},
    ]

    with patch("src.compaction.anthropic.AsyncAnthropic", side_effect=Exception("API error")):
        result, summary, did_compact = await compact_messages(
            messages, max_tokens=200, existing_summary="old summary"
        )

    assert did_compact is True
    assert summary == "old summary"  # Falls back to existing summary


@pytest.mark.asyncio
async def test_incorporates_existing_summary():
    messages = [
        {"role": "user", "content": "x" * 2000},
        {"role": "user", "content": "latest"},
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Combined summary with prior context.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("src.compaction.anthropic.AsyncAnthropic", return_value=mock_client):
        result, summary, did_compact = await compact_messages(
            messages, max_tokens=200, existing_summary="Prior context about files."
        )

    assert did_compact is True
    # Verify the prompt included the existing summary
    call_args = mock_client.messages.create.call_args
    prompt_content = call_args.kwargs["messages"][0]["content"]
    assert "Prior context about files" in prompt_content
