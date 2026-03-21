# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Claude Harness is a sandboxed agent execution platform. It wraps the Claude Agent SDK so that all tool execution happens inside Docker containers — never on the host. A FastAPI server exposes sessions over HTTP/SSE, and a container pool keeps warm containers ready.

## Commands

```bash
# Install dependencies
uv sync --group dev

# Run server
uv run uvicorn src.api:app --reload --port 8001

# Run all tests (unit + integration)
uv run pytest tests/ -v

# Run only unit tests (no Docker/API key needed)
uv run pytest tests/ -v -m "not integration"

# Run a single test
uv run pytest tests/test_permissions.py::test_denied_tools -v

# Build the sandbox Docker image (required for integration tests)
docker build -t claude-harness-sandbox sandbox/

# Interactive TUI for manual testing
uv run python tui.py
```

## Architecture

The request flow is: **HTTP request → API → Session → AgentLoop → Claude Agent SDK → MCP tools → SandboxManager → Docker container**.

Key design decisions:

- **`agent.py`**: Wraps the Claude Agent SDK. Creates an MCP server with 4 sandbox tools (`read_file`, `write_file`, `bash_execute`, `grep_search`). All SDK built-in tools are blocked via `DISALLOWED_BUILTIN_TOOLS` to prevent host-side execution. Uses `permission_mode="bypassPermissions"` because our own `PermissionManager` handles authorization per-tool.

- **`api.py`**: FastAPI app. The `/sessions/{id}/messages` endpoint streams responses as SSE events (`text_delta`, `tool_call`, `tool_result`, `permission_request`, `usage`, `done`).

- **`pool.py`**: Maintains a warm pool of Docker containers. On release, containers are cleaned (`rm -rf /workspace/*`) and recycled. Replenishment runs on a background task.

- **`permissions.py`**: Three-tier permission model — `allow`, `deny`, or `needs_approval`. Approval requests block via `asyncio.Event` with a configurable timeout (default 60s). Resolved via the `POST /sessions/{id}/permissions/{request_id}` endpoint.

- **`compaction.py`**: When conversation history exceeds `MAX_CONTEXT_TOKENS`, older messages are summarized using a cheap model (Haiku) and replaced with a context summary message.

- **`tools.py`**: Pure functions that execute commands inside containers via `SandboxManager.exec()`. Also contains `TOOL_SCHEMAS` (currently unused — schemas are defined inline in `agent.py` via the `@tool` decorator).

- **`sandbox.py`**: Thin wrapper around the Docker SDK. Containers run with `network_mode="none"` (no network access) and resource limits from config.

## Configuration

All config is via environment variables (see `.env.example`). Loaded by `config.py` using Pydantic Settings. No API key env var needed — uses local Claude CLI authentication.

## Testing

Integration tests (marked `@pytest.mark.integration`) require Docker running and a valid Anthropic API key available to the Claude CLI. Unit tests for permissions, compaction, tools dispatch, and pool logic use no external services.
