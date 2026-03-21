# AGENTS.md

Instructions for AI agents working on this codebase.

## Stack

FastAPI, Python >=3.11, Claude Agent SDK, Docker SDK, Pydantic v2, uv

## Commands

```bash
uv sync --group dev                              # Install dependencies
uv run uvicorn src.api:app --reload --port 8001   # Dev server (hot reload)
uv run pytest tests/ -v                           # Run all tests
uv run pytest tests/ -v -m "not integration"      # Unit tests only (no Docker/API key)
uv run pytest tests/test_permissions.py::test_denied_tools -v  # Single test
docker build -t claude-harness-sandbox sandbox/   # Build sandbox image
uv run python tui.py                              # Interactive TUI for manual testing
```

> **Package manager:** Use `uv` only. Never use pip, poetry, or conda.

## Architecture

```
HTTP Request → api.py → SessionManager → AgentLoop → Claude Agent SDK → MCP tools → SandboxManager → Docker
```

### Module Responsibilities

| Module | Role |
|--------|------|
| `api.py` | FastAPI app, SSE streaming, HTTP endpoints |
| `agent.py` | Wraps Claude Agent SDK, defines MCP sandbox tools, blocks built-in tools |
| `sessions.py` | Session lifecycle, holds messages + permissions per session |
| `pool.py` | Warm container pool with async replenishment |
| `sandbox.py` | Docker SDK wrapper (create, exec, copy, destroy) |
| `tools.py` | Pure functions that run commands inside containers |
| `permissions.py` | Three-tier permission model (allow/deny/needs_approval) |
| `compaction.py` | Context window management via summarization |
| `config.py` | Pydantic BaseSettings, all config from env vars |

### Security Boundary

All Claude tool execution is sandboxed in Docker containers with `network_mode="none"`. The `DISALLOWED_BUILTIN_TOOLS` list in `agent.py` blocks all SDK built-in tools (Bash, Read, Write, etc.) so Claude can only use `mcp__sandbox__*` tools. The SDK's `permission_mode="bypassPermissions"` is intentional — our `PermissionManager` handles authorization.

### SSE Event Types

The `/sessions/{id}/messages` endpoint streams these events:

| Event | Payload |
|-------|---------|
| `text_delta` | `{"text": "..."}` |
| `tool_call` | `{"id", "name", "args"}` |
| `tool_result` | `{"tool_use_id", "content", "is_error"}` |
| `permission_request` | `{"request_id", "tool", "args"}` |
| `usage` | `{"input_tokens", "output_tokens"}` |
| `compaction` | `{"summary_length"}` |
| `done` | `{"status": "complete"}` |

## Testing

- **Unit tests:** permissions, compaction, tools dispatch, pool logic — no external deps
- **Integration tests:** marked `@pytest.mark.integration`, require Docker + Anthropic API key
- **Async mode:** `asyncio_mode = "auto"` in pyproject.toml
- All sandbox tools go through `dispatch_tool()` in `tools.py`

## Configuration

All via environment variables (see `.env.example`). No API key env var needed — uses local Claude CLI auth. Key settings: `MODEL`, `POOL_MIN_SIZE`, `POOL_MAX_SIZE`, `MAX_CONTEXT_TOKENS`, `PERMISSION_TIMEOUT`.

## Anti-Patterns

| Don't | Do Instead |
|-------|------------|
| Add SDK built-in tools without updating `DISALLOWED_BUILTIN_TOOLS` | Block new built-ins to maintain sandbox isolation |
| Emit `text_delta` from both `AssistantMessage` and `ResultMessage` | Only emit text from `AssistantMessage` to avoid duplicates |
| Run network calls on the main Textual thread | Use `@work(thread=True)` for all HTTP calls in the TUI |
| Raise `HTTPException` in services/agent | Raise domain exceptions, convert in `api.py` |
| Hardcode config values | Use `config.py` Pydantic Settings + env vars |
