import json
import logging

import anthropic

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Summarize the following conversation history into a concise context summary.
Focus on: key decisions made, files created/modified, errors encountered, and important facts.
Be specific — include file names, variable names, and concrete details.
Keep it under 500 words.

Conversation to summarize:
{conversation}

{existing_summary}"""


def estimate_tokens(messages: list) -> int:
    return len(json.dumps(messages)) // 4


async def compact_messages(
    messages: list,
    max_tokens: int,
    existing_summary: str = "",
    compaction_model: str = "claude-haiku-4-5-20251001",
) -> tuple[list, str, bool]:
    """Compact messages if they exceed the token budget.

    Returns (compacted_messages, new_summary, did_compact).
    """
    current_tokens = estimate_tokens(messages)
    if current_tokens <= max_tokens:
        return messages, existing_summary, False

    # Find split point: remove oldest messages to get under 70% of budget
    target = int(max_tokens * 0.7)
    split_idx = 0
    running_tokens = current_tokens
    for i, msg in enumerate(messages):
        msg_tokens = estimate_tokens([msg])
        running_tokens -= msg_tokens
        if running_tokens <= target:
            split_idx = i + 1
            break
    else:
        split_idx = len(messages) - 1  # Keep at least the last message

    if split_idx == 0:
        return messages, existing_summary, False

    old_messages = messages[:split_idx]
    remaining_messages = messages[split_idx:]

    # Summarize old messages using a cheap model
    conversation_text = "\n".join(
        f"{m['role']}: {m['content'][:500]}" for m in old_messages
    )
    existing_part = f"\nPrevious summary to incorporate:\n{existing_summary}" if existing_summary else ""

    try:
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=compaction_model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": SUMMARY_PROMPT.format(
                    conversation=conversation_text,
                    existing_summary=existing_part,
                ),
            }],
        )
        new_summary = response.content[0].text
    except Exception:
        logger.exception("Compaction summary failed, falling back to truncation")
        new_summary = existing_summary or "(earlier conversation context was truncated)"

    # Prepend summary as a context message
    summary_message = {
        "role": "user",
        "content": f"[Context from earlier in this conversation]: {new_summary}",
    }
    compacted = [summary_message] + remaining_messages

    logger.info(
        "Compacted %d messages (%d est. tokens) -> %d messages (%d est. tokens)",
        len(messages), current_tokens, len(compacted), estimate_tokens(compacted),
    )
    return compacted, new_summary, True
