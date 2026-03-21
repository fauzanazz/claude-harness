# Claude Harness

A sandboxed agent execution platform that wraps the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents/claude-agent-sdk) so all tool execution happens inside Docker containers — never on the host. A FastAPI server exposes sessions over HTTP/SSE, and a container pool keeps warm containers ready.

## Architecture

```
HTTP request → FastAPI → Session → AgentLoop → Claude Agent SDK → MCP tools → SandboxManager → Docker
```

- **Sandbox isolation** — Containers run with `network_mode="none"` (no network access) and configurable resource limits. All Claude built-in tools are blocked; only four sandboxed tools are exposed: `read_file`, `write_file`, `bash_execute`, `grep_search`.
- **Container pool** — Warm containers are pre-created and recycled after use (workspace cleaned between sessions).
- **Permission system** — Three-tier model (`allow`, `deny`, `needs_approval`) with async approval flow via API.
- **Context compaction** — When conversation history exceeds a token threshold, older messages are summarized using a cheaper model and replaced with a context summary.
- **SSE streaming** — Responses stream as typed events: `text_delta`, `tool_call`, `tool_result`, `permission_request`, `compaction`, `usage`, `done`.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker

## Quick Start

```bash
# Install dependencies
uv sync --group dev

# Build the sandbox Docker image
docker build -t claude-harness-sandbox sandbox/

# Run the server
uv run uvicorn src.api:app --reload --port 8001
```

The API is now available at `http://localhost:8001`.

## Usage

### Create a session

```bash
curl -X POST http://localhost:8001/sessions \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Send a message (SSE stream)

```bash
curl -N -X POST http://localhost:8001/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Write a Python script that prints hello world"}'
```

### Configure permissions

```bash
curl -X POST http://localhost:8001/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "permissions": {
      "allowed_tools": ["read_file", "grep_search"],
      "denied_tools": ["bash_execute"],
      "require_approval": ["write_file"]
    }
  }'
```

### Resolve a permission request

```bash
curl -X POST http://localhost:8001/sessions/{session_id}/permissions/{request_id} \
  -H "Content-Type: application/json" \
  -d '{"decision": "approve"}'
```

### File operations

```bash
# List files in a session's container
curl http://localhost:8001/sessions/{session_id}/files?path=/workspace

# Upload a file
curl -X POST http://localhost:8001/sessions/{session_id}/files \
  -F "file=@myfile.txt"

# Download a file
curl http://localhost:8001/sessions/{session_id}/files/workspace/output.txt
```

### Delete a session

```bash
curl -X DELETE http://localhost:8001/sessions/{session_id}
```

### Interactive TUI

A Textual-based TUI is included for manual testing:

```bash
uv run python tui.py
```

## Configuration

All configuration is via environment variables (or `.env` file). See `.env.example` for defaults.

| Variable | Default | Description |
|---|---|---|
| `MODEL` | `claude-sonnet-4-6` | Claude model for the agent |
| `SANDBOX_IMAGE` | `claude-harness-sandbox` | Docker image for sandbox containers |
| `SANDBOX_MEMORY` | `2g` | Memory limit per container |
| `SANDBOX_CPUS` | `1` | CPU limit per container |
| `SANDBOX_TIMEOUT` | `3600` | Container timeout in seconds |
| `POOL_MIN_SIZE` | `1` | Minimum warm containers in pool |
| `POOL_MAX_SIZE` | `5` | Maximum containers in pool |
| `MAX_CONTEXT_TOKENS` | `100000` | Token threshold for context compaction |
| `COMPACTION_MODEL` | `claude-haiku-4-5-20251001` | Model used for compaction summaries |
| `PERMISSION_TIMEOUT` | `60` | Seconds to wait for permission approval |

No API key environment variable is needed — authentication uses the local Claude CLI.

## Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Unit tests only (no Docker or API key needed)
uv run pytest tests/ -v -m "not integration"

# Single test
uv run pytest tests/test_permissions.py::test_denied_tools -v
```

Integration tests require Docker running and a valid Anthropic API key available to the Claude CLI.

## Project Structure

```
src/
  api.py          # FastAPI routes and SSE streaming
  agent.py        # Claude Agent SDK wrapper with MCP tool server
  sandbox.py      # Docker container management
  pool.py         # Warm container pool with recycling
  permissions.py  # Three-tier permission model
  compaction.py   # Context window compaction
  sessions.py     # Session state management
  tools.py        # Sandbox tool implementations
  config.py       # Pydantic Settings configuration
sandbox/
  Dockerfile      # Sandbox container image (Python, ripgrep, jq, git, Node.js)
tests/            # Unit and integration tests
tui.py            # Interactive TUI for manual testing
```

## License

Private repository.
