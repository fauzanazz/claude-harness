# Claude Harness v2 — Implementation Plan

**Spec**: `.planning/spec-v2.md`
**Approach**: Sequential waves, each building on the previous.
**SDK**: Keep Claude Agent SDK throughout. Direct Anthropic SDK not needed.

---

## Updated Project Structure

```
claude-harness/
├── src/
│   ├── __init__.py
│   ├── config.py           # + new env vars (pool, compaction, permissions)
│   ├── sandbox.py           # unchanged
│   ├── pool.py              # NEW — ContainerPool (warm pool + claim/release)
│   ├── tools.py             # + permission check in dispatch_tool()
│   ├── agent.py             # + compaction hook, permission hook
│   ├── sessions.py          # + permissions config, pool integration, compaction state
│   ├── permissions.py       # NEW — PermissionManager (allow/deny + approval flow)
│   ├── compaction.py        # NEW — compact_messages() with hybrid strategy
│   ├── api.py               # + reconnect, permission endpoint, improved SSE events
│   └── main.py              # + pool startup/shutdown lifecycle
├── tests/
│   ├── test_sandbox.py      # unchanged
│   ├── test_tools.py        # unchanged
│   ├── test_pool.py         # NEW
│   ├── test_permissions.py  # NEW
│   ├── test_compaction.py   # NEW
│   ├── test_agent.py        # + permission/compaction tests
│   └── test_api.py          # + reconnect, permission endpoint tests
```

3 new files, ~15 total. Stays minimal.

---

## Wave 5: Streaming Improvements

**Goal**: Clean up SSE event structure for a better client experience. Foundation for permission events in Wave 7.

| Task | File(s) | Details |
|------|---------|---------|
| 5.1 | `src/agent.py` | Refine event types yielded by `AgentLoop.run()`: ensure `text_delta`, `tool_call` (with name + args), `tool_result` (with name + result), and `usage` events are all emitted distinctly. Currently the Agent SDK callback structure may not emit tool events — add them in the MCP tool wrappers. |
| 5.2 | `src/api.py` | Update `event_generator()` to emit structured SSE events matching the spec: `text_delta`, `tool_call`, `tool_result`, `usage`, `done`. |
| 5.3 | `tests/test_agent.py` | Add test: verify agent yields `tool_call` and `tool_result` events (not just `text_delta`). |

**Verify**: `uv run pytest tests/test_agent.py tests/test_api.py -v -m integration`

---

## Wave 6: Container Pool + Reconnect

**Goal**: Warm container pool for fast session creation + reconnect to existing containers.

| Task | File(s) | Details |
|------|---------|---------|
| 6.1 | `src/config.py` | Add `pool_min_size: int = 1`, `pool_max_size: int = 5` |
| 6.2 | `src/pool.py` | `ContainerPool` class: |
|      |         | `__init__(sandbox: SandboxManager, min_size, max_size)` |
|      |         | `start()` — pre-create `min_size` containers, store in `_available: asyncio.Queue` |
|      |         | `claim() -> str` — pop a container from the queue. If empty, create one on-demand (up to max). |
|      |         | `release(container_id)` — return container to pool (reset: `rm -rf /workspace/*`, recreate `/workspace/uploads/`). Or destroy if pool is at max. |
|      |         | `shutdown()` — destroy all pooled containers |
|      |         | Background task: `_replenish()` — if `_available.qsize() < min_size`, create containers to fill up. Runs on a 5s interval. |
|      |         | Track `_active: set[str]` — containers currently claimed by sessions |
| 6.3 | `src/sessions.py` | Modify `SessionManager`: |
|      |         | Accept `ContainerPool` instead of `SandboxManager` |
|      |         | `create(container_id: str | None = None)` — if `container_id` given and it's in `pool._active` for another session, raise 409. If given and running but not active, adopt it. Otherwise claim from pool. |
|      |         | `delete()` — release container back to pool (not destroy) |
|      |         | `get()` response includes `container_id` |
| 6.4 | `src/api.py` | Update `POST /sessions` to accept optional `{"container_id": "..."}` body. Return `container_id` in response. Update module-level state to use pool. |
| 6.5 | `src/main.py` | Add FastAPI lifespan: `pool.start()` on startup, `pool.shutdown()` on shutdown. |
| 6.6 | `tests/test_pool.py` | Test: claim/release cycle, pool replenishment, reconnect to released container. |
| 6.7 | `tests/test_api.py` | Test: create session returns `container_id`. Create with `container_id` reconnects. 409 on double-bind. |

**Verify**: `uv run pytest tests/test_pool.py tests/test_api.py -v -m integration`

---

## Wave 7: Permission System

**Goal**: Static allow/deny + human-in-the-loop approval with SSE + POST callback.

| Task | File(s) | Details |
|------|---------|---------|
| 7.1 | `src/config.py` | Add `permission_timeout: int = 60` |
| 7.2 | `src/permissions.py` | `PermissionManager` class: |
|      |         | `__init__(allowed: list[str] | None, denied: list[str] | None, require_approval: list[str] | None, timeout: int)` |
|      |         | `check(tool_name) -> "allow" | "deny" | "needs_approval"` — static check |
|      |         | `request_approval(request_id, tool_name, args) -> asyncio.Event` — stores pending request, returns event to await |
|      |         | `resolve(request_id, decision: "approve" | "deny")` — sets the event, stores decision |
|      |         | `get_decision(request_id) -> "approve" | "deny"` — retrieve stored decision |
|      |         | Default (no config): all tools allowed, no approval needed |
| 7.3 | `src/sessions.py` | Add `permissions: PermissionManager` to `Session` dataclass. `SessionManager.create()` accepts optional permissions config dict. |
| 7.4 | `src/agent.py` | In the MCP tool wrappers (before calling `dispatch_tool`): |
|      |         | 1. Call `session.permissions.check(tool_name)` |
|      |         | 2. If "deny": return error message to Claude |
|      |         | 3. If "needs_approval": yield `permission_request` event, await the asyncio.Event (with timeout), check decision |
|      |         | 4. If "allow" or approved: proceed with dispatch |
|      |         | This requires `AgentLoop` to receive a reference to the session's `PermissionManager` and a way to yield events back to the SSE stream while awaiting approval. Use an `asyncio.Queue` for bidirectional communication. |
| 7.5 | `src/api.py` | Add `POST /sessions/{id}/permissions/{request_id}` endpoint: |
|      |         | Body: `{"decision": "approve"}` or `{"decision": "deny"}` |
|      |         | Calls `session.permissions.resolve(request_id, decision)` |
|      |         | Returns 404 if request_id not found, 200 on success |
| 7.6 | `tests/test_permissions.py` | Unit tests: check() logic for allow/deny/needs_approval. resolve() sets event. Timeout defaults to deny. |
| 7.7 | `tests/test_api.py` | Integration test: create session with `require_approval: ["bash_execute"]`. Send message that triggers bash. Verify `permission_request` SSE event is emitted. POST approve. Verify tool executes. |

**Verify**: `uv run pytest tests/test_permissions.py tests/test_api.py -v -m integration`

### Architecture Note: Agent ↔ API Bidirectional Communication

The permission flow requires the agent loop to:
1. **Yield** a `permission_request` event out to the SSE stream
2. **Block** until the API layer resolves the approval

Current design: `AgentLoop.run()` is an `AsyncGenerator[dict, None]`. For bidirectional flow, introduce an `asyncio.Queue` pair:
- `event_queue: asyncio.Queue` — agent pushes events, API consumes for SSE
- The `PermissionManager` holds the `asyncio.Event` objects — agent awaits them directly

The `event_generator()` in `api.py` reads from the event queue. When the agent pushes a `permission_request`, it also awaits the corresponding `asyncio.Event` from the `PermissionManager`. The POST endpoint resolves the event, unblocking the agent.

---

## Wave 8: Context Compaction

**Goal**: Hybrid compaction — summarize old messages + keep recent ones when approaching token limit.

| Task | File(s) | Details |
|------|---------|---------|
| 8.1 | `src/config.py` | Add `max_context_tokens: int = 100_000`, `compaction_model: str = "claude-haiku-4-5-20251001"` (cheap model for summaries) |
| 8.2 | `src/compaction.py` | `compact_messages(messages, max_tokens, summary_so_far) -> (compacted_messages, new_summary)`: |
|      |         | 1. Estimate token count of `messages` (use `len(json.dumps(m)) / 4` as rough estimate, or `anthropic.count_tokens()` if available) |
|      |         | 2. If under `max_tokens`: return messages unchanged |
|      |         | 3. Split: find the oldest N messages that bring total under 70% of max_tokens when removed |
|      |         | 4. Call Claude (compaction_model) to summarize those N messages + existing summary into a new condensed summary |
|      |         | 5. Return: `[{"role": "user", "content": "[Context summary]: {new_summary}"}] + remaining_messages`, new_summary |
| 8.3 | `src/sessions.py` | Add `summary: str = ""` to `Session` dataclass. Stores the running compaction summary. |
| 8.4 | `src/agent.py` | Before each API call in the agent loop: call `compact_messages()` on session messages. Update session summary if compaction occurred. Yield a `compaction` event so the client knows it happened. |
| 8.5 | `tests/test_compaction.py` | Unit test: create a message list that exceeds token budget. Verify compaction produces a shorter list with a summary. Mock the Claude call for the summary. |
| 8.6 | `tests/test_compaction.py` | Unit test: messages under budget are returned unchanged. |

**Verify**: `uv run pytest tests/test_compaction.py -v`

---

## Execution Order

```
Wave 5 (streaming) ──→ Wave 6 (pool) ──→ Wave 7 (permissions) ──→ Wave 8 (compaction)
```

Sequential. Each wave gets its own commit.

## Dependencies Added

None — all features use existing deps (`claude-agent-sdk`, `fastapi`, `docker`, `sse-starlette`).
The compaction feature needs an Anthropic client for the summary call, but that's available through the Agent SDK or by adding `anthropic` as a direct dependency.

## Risk Notes

1. **Wave 7 (permissions)** is the most complex — bidirectional async flow between agent loop and API layer. The `asyncio.Queue` + `asyncio.Event` pattern needs careful testing to avoid deadlocks.
2. **Wave 8 (compaction)** token counting is approximate. The rough `len/4` estimate may trigger compaction too early or too late. Can be refined later.
3. **Wave 6 (pool)** container cleanup on release needs to be thorough — `rm -rf /workspace/*` must not leave state that leaks between sessions.
