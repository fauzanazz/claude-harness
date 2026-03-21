import asyncio
import logging

from .sandbox import SandboxManager

logger = logging.getLogger(__name__)


class ContainerPool:
    def __init__(self, sandbox: SandboxManager, min_size: int = 1, max_size: int = 5):
        self.sandbox = sandbox
        self.min_size = min_size
        self.max_size = max_size
        self._available: asyncio.Queue[str] = asyncio.Queue()
        self._active: dict[str, str | None] = {}  # container_id -> session_id or None
        self._replenish_task: asyncio.Task | None = None

    async def start(self):
        for _ in range(self.min_size):
            container_id = self.sandbox.create()
            await self._available.put(container_id)
            logger.info("Pool: pre-created container %s", container_id[:12])
        self._replenish_task = asyncio.create_task(self._replenish_loop())

    async def _replenish_loop(self):
        while True:
            await asyncio.sleep(5)
            try:
                total = self._available.qsize() + len(self._active)
                needed = self.min_size - self._available.qsize()
                while needed > 0 and total < self.max_size:
                    container_id = self.sandbox.create()
                    await self._available.put(container_id)
                    logger.info("Pool: replenished container %s", container_id[:12])
                    needed -= 1
                    total += 1
            except Exception:
                logger.exception("Pool: replenishment error")

    async def claim(self, session_id: str) -> str:
        try:
            container_id = self._available.get_nowait()
        except asyncio.QueueEmpty:
            total = self._available.qsize() + len(self._active)
            if total >= self.max_size:
                raise RuntimeError("Container pool exhausted")
            container_id = self.sandbox.create()
            logger.info("Pool: on-demand created container %s", container_id[:12])
        self._active[container_id] = session_id
        return container_id

    def adopt(self, container_id: str, session_id: str):
        if container_id in self._active:
            existing_session = self._active[container_id]
            if existing_session is not None:
                raise ValueError(f"Container already bound to session {existing_session}")
        self._active[container_id] = session_id

    async def release(self, container_id: str):
        self._active.pop(container_id, None)
        try:
            self.sandbox.exec(container_id, "rm -rf /workspace/* /workspace/.[!.]* 2>/dev/null; mkdir -p /workspace/uploads")
            await self._available.put(container_id)
            logger.info("Pool: released container %s back to pool", container_id[:12])
        except Exception:
            logger.exception("Pool: failed to reset container %s, destroying", container_id[:12])
            try:
                self.sandbox.destroy(container_id)
            except Exception:
                pass

    def is_container_active(self, container_id: str) -> str | None:
        return self._active.get(container_id)

    def is_container_running(self, container_id: str) -> bool:
        try:
            container = self.sandbox.client.containers.get(container_id)
            return container.status == "running"
        except Exception:
            return False

    async def shutdown(self):
        if self._replenish_task:
            self._replenish_task.cancel()
            try:
                await self._replenish_task
            except asyncio.CancelledError:
                pass

        # Destroy active containers
        for container_id in list(self._active):
            try:
                self.sandbox.destroy(container_id)
            except Exception:
                pass
        self._active.clear()

        # Destroy pooled containers
        while not self._available.empty():
            try:
                container_id = self._available.get_nowait()
                self.sandbox.destroy(container_id)
            except Exception:
                pass
        logger.info("Pool: shutdown complete")
