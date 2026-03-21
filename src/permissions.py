import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class PendingApproval:
    request_id: str
    tool_name: str
    args: dict
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str = ""  # "approve" or "deny"


class PermissionManager:
    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
        require_approval: list[str] | None = None,
        timeout: int = 60,
    ):
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.denied_tools = set(denied_tools) if denied_tools else set()
        self.require_approval = set(require_approval) if require_approval else set()
        self.timeout = timeout
        self._pending: dict[str, PendingApproval] = {}

    def check(self, tool_name: str) -> str:
        if tool_name in self.denied_tools:
            return "deny"
        if tool_name in self.require_approval:
            return "needs_approval"
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return "deny"
        return "allow"

    def request_approval(self, tool_name: str, args: dict) -> PendingApproval:
        request_id = str(uuid.uuid4())
        pending = PendingApproval(request_id=request_id, tool_name=tool_name, args=args)
        self._pending[request_id] = pending
        return pending

    def resolve(self, request_id: str, decision: str):
        if request_id not in self._pending:
            raise KeyError(f"No pending approval with id {request_id}")
        pending = self._pending[request_id]
        pending.decision = decision
        pending.event.set()

    async def wait_for_decision(self, pending: PendingApproval) -> str:
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            pending.decision = "deny"
        finally:
            self._pending.pop(pending.request_id, None)
        return pending.decision
