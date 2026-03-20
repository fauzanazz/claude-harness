from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from .sandbox import SandboxManager


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    container_id: str = ""
    messages: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionManager:
    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox
        self.sessions: dict[str, Session] = {}

    def create(self) -> Session:
        container_id = self.sandbox.create()
        session = Session(container_id=container_id)
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise KeyError(f"Session {session_id} not found")
        return self.sessions[session_id]

    def delete(self, session_id: str):
        session = self.get(session_id)
        self.sandbox.destroy(session.container_id)
        del self.sessions[session_id]
