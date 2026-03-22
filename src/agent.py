import asyncio
import collections
import logging
import threading
from typing import AsyncGenerator

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from .permissions import PermissionManager
from .backend import SandboxBackend
from .tools import dispatch_tool

logger = logging.getLogger(__name__)

DISALLOWED_BUILTIN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "MultiEdit",
    "Glob", "Grep", "LS", "WebFetch", "WebSearch",
    "ToolSearch", "NotebookEdit", "NotebookRead",
    "TodoRead", "TodoWrite",
    "Agent", "AskUserQuestion",
]


class AgentLoop:
    def __init__(
        self,
        sandbox: SandboxBackend,
        container_id: str,
        model: str | None = None,
        permissions: PermissionManager | None = None,
    ):
        self.sandbox = sandbox
        self.container_id = container_id
        self.model = model
        self.permissions = permissions or PermissionManager()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        # Thread-safe buffer for tool results (MCP tools may run in a different context)
        self._tool_results: collections.deque[dict] = collections.deque()
        self._tool_results_lock = threading.Lock()

    async def _check_permission(self, tool_name: str, args: dict) -> str | None:
        decision = self.permissions.check(tool_name)
        if decision == "allow":
            return None
        if decision == "deny":
            return f"Permission denied: tool '{tool_name}' is not allowed in this session."
        # needs_approval
        pending = self.permissions.request_approval(tool_name, args)
        await self._event_queue.put({
            "type": "permission_request",
            "request_id": pending.request_id,
            "tool": tool_name,
            "args": args,
        })
        result = await self.permissions.wait_for_decision(pending)
        if result == "deny":
            return f"User denied permission to execute {tool_name}."
        return None

    def _make_mcp_server(self):
        sandbox = self.sandbox
        container_id = self.container_id
        agent = self

        async def _run_tool(name: str, args: dict) -> dict:
            error = await agent._check_permission(name, args)
            if error:
                with agent._tool_results_lock:
                    agent._tool_results.append({
                        "type": "tool_result", "content": error, "is_error": True,
                    })
                return {"content": [{"type": "text", "text": error}]}
            result = dispatch_tool(name, args, sandbox, container_id)
            with agent._tool_results_lock:
                agent._tool_results.append({
                    "type": "tool_result", "content": result, "is_error": False,
                })
            return {"content": [{"type": "text", "text": result}]}

        @tool("read_file", "Read the contents of a file at the given path in the sandbox", {"path": str})
        async def read_file(args):
            return await _run_tool("read_file", args)

        @tool("write_file", "Write content to a file at the given path in the sandbox", {"path": str, "content": str})
        async def write_file(args):
            return await _run_tool("write_file", args)

        @tool("bash_execute", "Execute a bash command in the sandbox and return stdout, stderr, and return code", {"command": str})
        async def bash_execute(args):
            return await _run_tool("bash_execute", args)

        @tool("grep_search", "Search for a pattern in files using ripgrep in the sandbox", {"pattern": str, "path": str})
        async def grep_search(args):
            return await _run_tool("grep_search", args)

        return create_sdk_mcp_server("sandbox-tools", tools=[read_file, write_file, bash_execute, grep_search])

    async def run(self, messages: list) -> AsyncGenerator[dict, None]:
        server = self._make_mcp_server()

        options = ClaudeAgentOptions(
            mcp_servers={"sandbox": server},
            permission_mode="bypassPermissions",
            disallowed_tools=DISALLOWED_BUILTIN_TOOLS,
            system_prompt=(
                "You are an AI assistant with access to a sandboxed Docker container. "
                "Use the sandbox tools (read_file, write_file, bash_execute, grep_search) "
                "to execute code and manipulate files. All tools operate inside the sandbox."
            ),
        )
        if self.model:
            options.model = self.model

        last_user_msg = messages[-1]["content"] if messages else ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(last_user_msg)
            async for message in client.receive_response():
                # Drain permission events
                while not self._event_queue.empty():
                    yield self._event_queue.get_nowait()
                # Drain tool results (thread-safe)
                with self._tool_results_lock:
                    while self._tool_results:
                        yield self._tool_results.popleft()

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield {"type": "text_delta", "text": block.text}
                        elif isinstance(block, ToolUseBlock):
                            yield {
                                "type": "tool_call",
                                "id": block.id,
                                "name": block.name,
                                "args": block.input,
                            }
                        elif isinstance(block, ToolResultBlock):
                            yield {
                                "type": "tool_result",
                                "tool_use_id": block.tool_use_id,
                                "content": block.content,
                                "is_error": block.is_error or False,
                            }
                elif isinstance(message, ResultMessage):
                    usage = message.usage or {}
                    yield {
                        "type": "usage",
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    }

            # Final drain
            while not self._event_queue.empty():
                yield self._event_queue.get_nowait()
            with self._tool_results_lock:
                while self._tool_results:
                    yield self._tool_results.popleft()
