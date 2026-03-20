# Claude Harness — Implementation Plan

**Approach**: Bottom-up (sandbox → tools → agent loop → API)

## Project Structure

```
claude-harness/
├── .planning/
│   ├── spec.md
│   └── plan.md
├── sandbox/
│   └── Dockerfile          # Pre-built sandbox image
├── src/
│   ├── __init__.py
│   ├── config.py           # Settings (env vars, model config)
│   ├── sandbox.py          # SandboxManager (Docker container lifecycle)
│   ├── tools.py            # Tool definitions + dispatch
│   ├── agent.py            # Agent loop (while loop + Anthropic SDK)
│   ├── sessions.py         # Session state management (in-memory)
│   ├── api.py              # FastAPI app + routes
│   └── main.py             # Entrypoint
├── tests/
│   ├── test_sandbox.py
│   ├── test_tools.py
│   ├── test_agent.py
│   └── test_api.py
├── pyproject.toml
├── README.md
└── .env.example
```

~12 source files. Minimal and navigable.

---

## Wave 0: Project Scaffolding

**Goal**: Runnable project skeleton with dependencies installed.

| Task | File(s) | Details |
|------|---------|---------|
| 0.1 | `pyproject.toml` | Project metadata, dependencies: `anthropic`, `fastapi`, `uvicorn`, `docker`, `sse-starlette`, `python-multipart` |
| 0.2 | `.env.example`, `src/config.py` | Pydantic Settings: `ANTHROPIC_API_KEY`, `MODEL` (default `claude-sonnet-4-6`), `SANDBOX_IMAGE`, `SANDBOX_MEMORY`, `SANDBOX_CPUS`, `SANDBOX_TIMEOUT` |
| 0.3 | `src/main.py` | `uvicorn.run()` entrypoint |
| 0.4 | `.gitignore` | Python, .env, __pycache__, .venv |

**Verify**: `uv run python -c "from src.config import settings; print(settings.model)"` works.

---

## Wave 1: Docker Sandbox Manager

**Goal**: Create, execute commands in, copy files to/from, and destroy Docker containers.

| Task | File(s) | Details |
|------|---------|---------|
| 1.1 | `sandbox/Dockerfile` | Based on `python:3.11-slim`. Install: `ripgrep`, `jq`, `nodejs`, `npm`, `curl`, `git`. Set `/workspace` as WORKDIR. Create `/workspace/uploads/` dir. |
| 1.2 | `src/sandbox.py` | `SandboxManager` class with methods: |
|      |         | `create() -> container_id` — `docker run -d --network=none --memory=2g --cpus=1` detached with `tail -f /dev/null` to keep alive |
|      |         | `exec(container_id, command) -> {stdout, stderr, return_code}` — `docker exec` with timeout |
|      |         | `copy_to(container_id, local_path, container_path)` — `docker cp` file into container |
|      |         | `copy_from(container_id, container_path) -> bytes` — `docker cp` file out of container |
|      |         | `list_files(container_id, path) -> list[str]` — `exec("ls -la {path}")` |
|      |         | `destroy(container_id)` — `docker rm -f` |
| 1.3 | `tests/test_sandbox.py` | Integration tests: create container, exec `echo hello`, copy file in/out, destroy. Requires Docker running. |

**Verify**: `uv run pytest tests/test_sandbox.py -v` passes with Docker running.

---

## Wave 2: Tool Definitions

**Goal**: Four tools that execute inside the sandbox, with Anthropic tool-use schema format.

| Task | File(s) | Details |
|------|---------|---------|
| 2.1 | `src/tools.py` | Define tool schemas (Anthropic format) + execution functions: |
|      |         | `read_file(path: str)` → `sandbox.exec(cat {path})` → returns file content |
|      |         | `write_file(path: str, content: str)` → `sandbox.exec(cat << 'HEREDOC' > {path})` → returns success/error |
|      |         | `bash_execute(command: str)` → `sandbox.exec(command)` → returns stdout/stderr/return_code |
|      |         | `grep_search(pattern: str, path: str)` → `sandbox.exec(rg {pattern} {path})` → returns matches |
| 2.2 | `src/tools.py` | `TOOL_SCHEMAS: list[dict]` — the JSON tool definitions to pass to Anthropic API |
| 2.3 | `src/tools.py` | `dispatch_tool(name, args, sandbox_manager, container_id) -> str` — routes tool call to correct function |
| 2.4 | `tests/test_tools.py` | Test each tool against a real sandbox container: write a file, read it back, grep it, bash `ls`. |

**Verify**: `uv run pytest tests/test_tools.py -v` passes.

---

## Wave 3: Agent Loop

**Goal**: The core while loop that talks to Claude, dispatches tools, and streams responses.

| Task | File(s) | Details |
|------|---------|---------|
| 3.1 | `src/agent.py` | `AgentLoop` class: |
|      |         | `__init__(client, sandbox_manager, container_id, model)` |
|      |         | `run(messages) -> AsyncGenerator` — the core loop: |
|      |         |   1. Call `client.messages.create(stream=True, tools=TOOL_SCHEMAS, messages=messages)` |
|      |         |   2. Yield text deltas as they stream |
|      |         |   3. When response completes, check `stop_reason` |
|      |         |   4. If `stop_reason == "tool_use"`: dispatch each tool call, append results, loop back to step 1 |
|      |         |   5. If `stop_reason == "end_turn"`: done |
|      |         |   6. Yield tool call events (name, args, result) for observability |
| 3.2 | `src/agent.py` | Token usage logging: log input/output tokens after each API call |
| 3.3 | `tests/test_agent.py` | Test with a real API call: "What is 2+2? Use bash to calculate it." Verify it calls bash_execute and returns 4. |

**Verify**: `ANTHROPIC_API_KEY=... uv run pytest tests/test_agent.py -v` passes.

---

## Wave 4: FastAPI API Layer

**Goal**: HTTP endpoints for sessions, messages (SSE streaming), and file upload/download.

| Task | File(s) | Details |
|------|---------|---------|
| 4.1 | `src/sessions.py` | `SessionManager` class: |
|      |         | `sessions: dict[str, Session]` — in-memory store |
|      |         | `Session` dataclass: `id`, `container_id`, `messages: list`, `created_at` |
|      |         | `create() -> Session` — creates sandbox container + session |
|      |         | `get(session_id) -> Session` |
|      |         | `delete(session_id)` — destroys container + removes from store |
| 4.2 | `src/api.py` | Session endpoints: |
|      |         | `POST /sessions` → create session, return `{id, created_at}` |
|      |         | `DELETE /sessions/{id}` → destroy session |
| 4.3 | `src/api.py` | Message endpoint: |
|      |         | `POST /sessions/{id}/messages` → body: `{content: str}` |
|      |         | Appends user message to session history |
|      |         | Runs agent loop, streams SSE events: |
|      |         |   `event: text_delta` / `data: {text: "..."}` |
|      |         |   `event: tool_call` / `data: {name, args}` |
|      |         |   `event: tool_result` / `data: {name, result}` |
|      |         |   `event: done` / `data: {usage: {input_tokens, output_tokens}}` |
|      |         | Appends assistant message to session history |
| 4.4 | `src/api.py` | File endpoints: |
|      |         | `GET /sessions/{id}/files?path=/workspace` → list files |
|      |         | `POST /sessions/{id}/files` → multipart upload, copies into container `/workspace/uploads/` |
|      |         | `GET /sessions/{id}/files/{path:path}` → download file from container |
| 4.5 | `tests/test_api.py` | E2E test: create session → send message "create a file hello.txt with 'world'" → verify file exists → download it → delete session |

**Verify**: `uv run pytest tests/test_api.py -v` passes. Manual test with `curl`.

---

## Execution Order

```
Wave 0 (scaffolding) ──→ Wave 1 (sandbox) ──→ Wave 2 (tools) ──→ Wave 3 (agent) ──→ Wave 4 (API)
```

All sequential — each wave depends on the previous.

## Manual Smoke Test (after Wave 4)

```bash
# Terminal 1: Start the server
cd /Users/enjat/Github/claude-harness
uv run python -m src.main

# Terminal 2: Test it
# Create session
curl -X POST http://localhost:8000/sessions | jq

# Send message (streaming)
curl -N -X POST http://localhost:8000/sessions/{id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Create a Python file that prints fibonacci numbers up to 100, then run it"}'

# List files
curl http://localhost:8000/sessions/{id}/files | jq

# Download generated file
curl http://localhost:8000/sessions/{id}/files/workspace/fibonacci.py

# Cleanup
curl -X DELETE http://localhost:8000/sessions/{id}
```
