import logging
from typing import AsyncGenerator

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from .sandbox import SandboxManager
from .tools import dispatch_tool

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(self, sandbox: SandboxManager, container_id: str, model: str | None = None):
        self.sandbox = sandbox
        self.container_id = container_id
        self.model = model

    def _make_mcp_server(self):
        """Create an MCP server with sandbox tools."""
        sandbox = self.sandbox
        container_id = self.container_id

        @tool("read_file", "Read the contents of a file at the given path in the sandbox", {"path": str})
        async def read_file(args):
            result = dispatch_tool("read_file", args, sandbox, container_id)
            return {"content": [{"type": "text", "text": result}]}

        @tool("write_file", "Write content to a file at the given path in the sandbox", {"path": str, "content": str})
        async def write_file(args):
            result = dispatch_tool("write_file", args, sandbox, container_id)
            return {"content": [{"type": "text", "text": result}]}

        @tool("bash_execute", "Execute a bash command in the sandbox and return stdout, stderr, and return code", {"command": str})
        async def bash_execute(args):
            result = dispatch_tool("bash_execute", args, sandbox, container_id)
            return {"content": [{"type": "text", "text": result}]}

        @tool("grep_search", "Search for a pattern in files using ripgrep in the sandbox", {"pattern": str, "path": str})
        async def grep_search(args):
            result = dispatch_tool("grep_search", args, sandbox, container_id)
            return {"content": [{"type": "text", "text": result}]}

        return create_sdk_mcp_server("sandbox-tools", tools=[read_file, write_file, bash_execute, grep_search])

    async def run(self, messages: list) -> AsyncGenerator[dict, None]:
        server = self._make_mcp_server()

        options = ClaudeAgentOptions(
            mcp_servers={"sandbox": server},
            permission_mode="bypassPermissions",
            system_prompt=(
                "You are an AI assistant with access to a sandboxed Docker container. "
                "Use the sandbox tools (read_file, write_file, bash_execute, grep_search) "
                "to execute code and manipulate files. All tools operate inside the sandbox."
            ),
        )
        if self.model:
            options.model = self.model

        # Extract the last user message as the prompt
        last_user_msg = messages[-1]["content"] if messages else ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(last_user_msg)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield {"type": "text_delta", "text": block.text}
                elif isinstance(message, ResultMessage):
                    if message.result:
                        yield {"type": "text_delta", "text": message.result}
                    yield {
                        "type": "usage",
                        "input_tokens": 0,
                        "output_tokens": 0,
                    }
