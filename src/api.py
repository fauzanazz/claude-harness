import json
import logging
import os
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .agent import AgentLoop
from .sandbox import SandboxManager
from .sessions import SessionManager

logger = logging.getLogger(__name__)

app = FastAPI(title="Claude Harness")

# Global state
sandbox_manager = SandboxManager()
session_manager = SessionManager(sandbox_manager)


class MessageRequest(BaseModel):
    content: str


class SessionResponse(BaseModel):
    id: str
    created_at: str


# Task 4.2: Session endpoints

@app.post("/sessions")
async def create_session():
    session = session_manager.create()
    return {"id": session.id, "created_at": session.created_at.isoformat()}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        session_manager.delete(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


# Task 4.3: Message endpoint with SSE streaming

@app.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: MessageRequest):
    try:
        session = session_manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    session.messages.append({"role": "user", "content": request.content})
    agent = AgentLoop(sandbox_manager, session.container_id)

    async def event_generator():
        async for event in agent.run(session.messages):
            event_type = event.pop("type")
            yield {"event": event_type, "data": json.dumps(event)}
        yield {"event": "done", "data": json.dumps({"status": "complete"})}

    return EventSourceResponse(event_generator())


# Task 4.4: File endpoints

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
