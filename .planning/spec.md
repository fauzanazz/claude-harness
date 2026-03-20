# Claude Harness — Spec

## 1. One-Line Summary

A learning-focused, Claude-like AI agent harness with a tool-calling loop, Docker-based code sandbox, and file upload/download API.

## 2. Target Users

- The developer (enjat) — learning exercise to deeply understand how Claude Code / Claude.ai's "computer" works by building a working replica of the core architecture.

## 3. Success Criteria

### Agent Loop
1. A `while` loop around the Anthropic Messages API with native tool use
2. Claude decides which tools to call; the harness executes them and feeds results back
3. Loop continues until `stop_reason == "end_turn"` (no more tool calls)
4. Supports multi-turn conversations (message history maintained per session)
5. Streaming responses (SSE) so the user sees tokens as they arrive

### Tool Definitions
6. `read_file` — read a file from the sandbox filesystem, returns content
7. `write_file` — create or overwrite a file in the sandbox filesystem
8. `bash_execute` — run any bash command in the sandbox, returns stdout/stderr/return_code
9. `grep_search` — search file contents by pattern in the sandbox, returns matches with line numbers
10. All tools execute **inside the Docker sandbox**, not on the host machine

### Docker Sandbox
11. Each session gets a Docker container with `--network=none` (no internet)
12. Based on a pre-built image with Python 3.11, Node.js, common CLI tools (ripgrep, jq, etc.)
13. Resource limits: `--memory=2g --cpus=1` (lighter than Claude's 5GB for learning purposes)
14. Container reuse: same container persists across turns within a session
15. Container cleanup: containers are removed when session ends or after 1 hour idle timeout
16. Files created by tools persist within the container's filesystem for the session duration

### File Upload/Download API
17. `POST /files` — multipart upload, file is copied into the active sandbox container
18. `GET /files/{id}` — download a file from the sandbox container
19. Files are placed in `/workspace/uploads/` inside the container
20. Generated files (anything Claude creates) can be listed and downloaded

### API Layer
21. FastAPI with async endpoints
22. `POST /sessions` — create a new session (spins up a Docker container)
23. `POST /sessions/{id}/messages` — send a message, get streaming response (SSE)
24. `GET /sessions/{id}/files` — list files in the sandbox
25. `POST /sessions/{id}/files` — upload a file to the sandbox
26. `GET /sessions/{id}/files/{path}` — download a file from the sandbox
27. `DELETE /sessions/{id}` — destroy session and container

### Observability
28. Log every tool call (name, args, duration, success/failure)
29. Log token usage per turn (input/output tokens)

## 4. Out of Scope

- Frontend/UI (API-only for now, test with curl/httpie)
- Authentication/multi-user (single-user, no auth)
- Persistent storage (containers are ephemeral)
- Firecracker/microVM (Docker is sufficient for learning)
- Deployment pipeline (local dev only)
- MCP server integration
- Context compaction / memory system
- Permission system (all tools auto-approved)

## 5. Constraints

- **Runtime**: Python 3.11+, `uv` for package management
- **Framework**: FastAPI (async)
- **LLM**: Anthropic SDK only (direct Claude API, no LangChain/LiteLLM)
- **Sandbox**: Docker (must be installed on host), `docker` Python SDK
- **No new infra**: no databases, no Redis, no message queues — in-memory session state only
- **Model**: Claude Sonnet 4.6 default (configurable via env var)
- **Keep it minimal**: ~10-15 files, no over-engineering, this is a learning project
