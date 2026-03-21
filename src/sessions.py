from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from .config import settings
from .permissions import PermissionManager
from .pool import ContainerPool


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    container_id: str = ""
    messages: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    permissions: PermissionManager = field(default_factory=PermissionManager)
    summary: str = ""


class SessionManager:
    def __init__(self, pool: ContainerPool):
        self.pool = pool
        self.sessions: dict[str, Session] = {}

    async def create(
        self,
        container_id: str | None = None,
        permissions: dict | None = None,
    ) -> Session:
        perm_manager = PermissionManager(
            allowed_tools=permissions.get("allowed_tools") if permissions else None,
            denied_tools=permissions.get("denied_tools") if permissions else None,
            require_approval=permissions.get("require_approval") if permissions else None,
            timeout=settings.permission_timeout,
        )
        session = Session(permissions=perm_manager)

        if container_id:
            bound_session = self.pool.is_container_active(container_id)
            if bound_session is not None:
                raise ValueError(f"Container already bound to session {bound_session}")
            if not self.pool.is_container_running(container_id):
                raise ValueError(f"Container {container_id} is not running")
            self.pool.adopt(container_id, session.id)
            session.container_id = container_id
        else:
            session.container_id = await self.pool.claim(session.id)

        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise KeyError(f"Session {session_id} not found")
        return self.sessions[session_id]

    async def delete(self, session_id: str):
        session = self.get(session_id)
        await self.pool.release(session.container_id)
        del self.sessions[session_id]
