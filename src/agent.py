import anthropic
import logging
from typing import AsyncGenerator

from .config import settings
from .sandbox import SandboxManager
from .tools import TOOL_SCHEMAS, dispatch_tool

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(self, sandbox: SandboxManager, container_id: str, model: str | None = None):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.sandbox = sandbox
        self.container_id = container_id
        self.model = model or settings.model

    async def run(self, messages: list) -> AsyncGenerator[dict, None]:
        while True:
            # 1. Call Claude with streaming
            with self.client.messages.stream(
                model=self.model,
                max_tokens=4096,
                tools=TOOL_SCHEMAS,
                messages=messages,
            ) as stream:
                # Collect text deltas and yield them
                response_text = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            response_text += event.delta.text
                            yield {"type": "text_delta", "text": event.delta.text}

                # Get the final message
                response = stream.get_final_message()

            # 2. Log token usage
            logger.info(
                "Token usage: input=%d, output=%d",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            yield {
                "type": "usage",
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

            # 3. Check stop reason
            if response.stop_reason == "end_turn":
                # Append assistant message to history
                messages.append({"role": "assistant", "content": response.content})
                break

            if response.stop_reason == "tool_use":
                # Append assistant message with tool_use blocks
                messages.append({"role": "assistant", "content": response.content})

                # Process each tool call
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        yield {"type": "tool_call", "name": block.name, "args": block.input}

                        # Dispatch the tool
                        try:
                            result = dispatch_tool(
                                block.name, block.input, self.sandbox, self.container_id
                            )
                        except Exception as e:
                            result = f"Error: {e}"

                        logger.info("Tool %s completed", block.name)
                        yield {"type": "tool_result", "name": block.name, "result": result}

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Append tool results and loop
                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason, break
                messages.append({"role": "assistant", "content": response.content})
                break
