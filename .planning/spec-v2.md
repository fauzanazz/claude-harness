# Claude Harness v2 — Spec

Extends the base harness (spec.md) with four new capabilities.

## Feature 1: Container Reuse

### 1a. Warm Container Pool
- Maintain a pool of pre-created, idle Docker containers ready for immediate assignment
- Pool size configurable via env vars: `POOL_MIN_SIZE` (default 1), `POOL_MAX_SIZE` (default 5)
- On session creation: claim a container from the pool instead of cold-starting one
- Background task replenishes the pool when it drops below `POOL_MIN_SIZE`
- Idle containers in the pool are recycled after `SANDBOX_TIMEOUT` (existing setting, default 1hr)

### 1b. Session Reconnect
- `POST /sessions` accepts optional `container_id` in the request body
- If provided and the container is still running, create a new session attached to that existing container
- The old session's message history is NOT carried over (fresh conversation, same filesystem state)
- `GET /sessions/{id}` response includes `container_id` so clients can store it for reconnection
- Reconnecting to a container that's already bound to an active session returns 409 Conflict

### Success Criteria
- Session creation latency drops from ~2-5s (cold start) to <500ms (warm pool hit)
- Client can create session, note the container_id, delete the session, create a new session with that container_id, and find their files still there

---

## Feature 2: Context Compaction

### Strategy: Hybrid (sliding window + summary)
- Track token count of the conversation history after each turn
- When total tokens exceed `MAX_CONTEXT_TOKENS` (env var, default 100,000):
  1. Take the oldest messages that exceed the budget
  2. Call Claude (cheap/fast model) to summarize them into a single condensed summary
  3. Replace those messages with a system-level summary message
  4. Keep the N most recent messages verbatim
- Summary is stored in the session and prepended as a system message on subsequent API calls
- Token counting uses `anthropic.count_tokens()` or a tiktoken estimate

### Success Criteria
- Conversations can exceed 100k tokens of raw history without API errors
- Summarized context preserves key facts (file names created, decisions made, errors encountered)
- Compaction is transparent to the client (no API change, just works)

---

## Feature 3: Permission System

### 3a. Static Allow/Deny List
- Sessions accept an optional `permissions` config on creation:
  ```json
  {
    "allowed_tools": ["read_file", "grep_search"],
    "denied_tools": ["bash_execute"],
    "require_approval": ["write_file"]
  }
  ```
- If not provided, defaults to all tools allowed, none requiring approval (backward compatible)
- Denied tools return an error to Claude immediately ("Tool X is not permitted in this session")
- Checked at dispatch time in `dispatch_tool()`

### 3b. Human-in-the-Loop Approval
- Tools listed in `require_approval` pause execution before running
- Server emits an SSE event:
  ```
  event: permission_request
  data: {"request_id": "uuid", "tool": "write_file", "args": {"path": "/workspace/foo.py", "content": "..."}}
  ```
- Client POSTs approval/denial:
  ```
  POST /sessions/{id}/permissions/{request_id}
  {"decision": "approve"}  // or "deny"
  ```
- Agent loop blocks (via asyncio.Event) until the client responds or a timeout (60s default, configurable via `PERMISSION_TIMEOUT`) expires
- Timeout = deny (safe default)
- Denied tool calls return an error to Claude: "User denied permission to execute write_file"

### Success Criteria
- Session with `denied_tools: ["bash_execute"]` prevents Claude from executing bash commands
- Session with `require_approval: ["write_file"]` pauses and waits for client approval before writing
- Default behavior (no permissions config) is unchanged from current behavior

---

## Feature 4: Streaming Responses

### Strategy: Keep Claude Agent SDK, event-level streaming
- Stay on Claude Agent SDK (Python SDK has token-streaming bugs; revisit when fixed)
- Stream at event granularity: whole text blocks + tool call/result events (not token-by-token)
- Improve current SSE event structure for better client experience
- SSE event types:
  - `text_delta` — `{"text": "complete text block"}`
  - `tool_call` — `{"name": "bash_execute", "args": {"command": "ls"}}`
  - `tool_result` — `{"name": "bash_execute", "result": "..."}`
  - `permission_request` — (from Feature 3)
  - `usage` — `{"input_tokens": N, "output_tokens": N}`
  - `done` — `{"status": "complete"}`
- Future: upgrade to token-level streaming when Agent SDK Python bugs are fixed

### Success Criteria
- Client sees text blocks and tool events as they happen via SSE (verifiable with curl -N)
- Tool calls are visible as they happen (call, result)
- Token usage is reported accurately per turn
- No regression from current behavior

---

## Out of Scope (unchanged)

- Frontend/UI
- Authentication/multi-user
- Persistent storage (beyond container lifecycle)
- Firecracker/microVM
- Deployment pipeline
- MCP server integration

## Constraints (unchanged + additions)

- Runtime: Python 3.11+, `uv` for package management
- Framework: FastAPI (async)
- LLM: Claude Agent SDK (keep current; upgrade to token streaming when Python SDK bugs are fixed)
- Sandbox: Docker
- No new infra: no databases, no Redis — in-memory state only
- Keep it minimal: target ~15 files total
- New env vars: `POOL_MIN_SIZE`, `POOL_MAX_SIZE`, `MAX_CONTEXT_TOKENS`, `PERMISSION_TIMEOUT`
