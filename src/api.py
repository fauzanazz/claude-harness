import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .agent import AgentLoop
from .compaction import compact_messages
from .config import settings
from .pool import ContainerPool
from .sandbox import SandboxManager
from .sessions import SessionManager

logger = logging.getLogger(__name__)

# Global state
sandbox_manager = SandboxManager()
pool = ContainerPool(sandbox_manager, min_size=settings.pool_min_size, max_size=settings.pool_max_size)
session_manager = SessionManager(pool)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.start()
    yield
    await pool.shutdown()


app = FastAPI(title="Claude Harness", lifespan=lifespan)


class PermissionsConfig(BaseModel):
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    require_approval: list[str] | None = None


class CreateSessionRequest(BaseModel):
    container_id: str | None = None
    permissions: PermissionsConfig | None = None


class MessageRequest(BaseModel):
    content: str


class PermissionDecision(BaseModel):
    decision: str  # "approve" or "deny"


# Session endpoints

@app.post("/sessions")
async def create_session(request: CreateSessionRequest | None = None):
    try:
        container_id = request.container_id if request else None
        permissions = request.permissions.model_dump() if request and request.permissions else None
        session = await session_manager.create(container_id=container_id, permissions=permissions)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "id": session.id,
        "container_id": session.container_id,
        "created_at": session.created_at.isoformat(),
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        await session_manager.delete(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


# Message endpoint with SSE streaming

@app.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: MessageRequest):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    session.messages.append({"role": "user", "content": request.content})

    # Compact context if approaching token limit
    compacted, new_summary, did_compact = await compact_messages(
        session.messages,
        max_tokens=settings.max_context_tokens,
        existing_summary=session.summary,
        compaction_model=settings.compaction_model,
    )
    if did_compact:
        session.messages = compacted
        session.summary = new_summary

    agent = AgentLoop(sandbox_manager, session.container_id, permissions=session.permissions)

    async def event_generator():
        if did_compact:
            yield {"event": "compaction", "data": json.dumps({"summary_length": len(new_summary)})}
        full_text = []
        async for event in agent.run(session.messages):
            event_type = event.pop("type")
            if event_type == "text_delta":
                full_text.append(event["text"])
            yield {"event": event_type, "data": json.dumps(event)}
        session.messages.append({"role": "assistant", "content": "".join(full_text)})
        yield {"event": "done", "data": json.dumps({"status": "complete"})}

    return EventSourceResponse(event_generator())


# Permission endpoint

@app.post("/sessions/{session_id}/permissions/{request_id}")
async def resolve_permission(session_id: str, request_id: str, request: PermissionDecision):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    if request.decision not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="Decision must be 'approve' or 'deny'")
    try:
        session.permissions.resolve(request_id, request.decision)
    except KeyError:
        raise HTTPException(status_code=404, detail="Permission request not found")
    return {"status": "resolved", "decision": request.decision}


# File endpoints

@app.get("/sessions/{session_id}/files")
async def list_files(session_id: str, path: str = "/workspace"):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    files = sandbox_manager.list_files(session.container_id, path)
    return {"files": files}


@app.post("/sessions/{session_id}/files")
async def upload_file(session_id: str, file: UploadFile):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        container_path = f"/workspace/uploads/{file.filename}"
        sandbox_manager.copy_to(session.container_id, tmp_path, container_path)
    finally:
        os.unlink(tmp_path)
    return {"path": container_path}


@app.get("/sessions/{session_id}/files/{file_path:path}")
async def download_file(session_id: str, file_path: str):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        data = sandbox_manager.copy_from(session.container_id, f"/{file_path}")
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=data, media_type="application/octet-stream")
