"""Firecracker microVM backend using snapshot restore + vsock guest agent."""

import asyncio
import base64
import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
import httpx

from .config import settings

logger = logging.getLogger(__name__)

VSOCK_PORT = 5000  # Must match guest-agent


# --- Vsock Client (length-prefixed JSON protocol) ---


class VsockClient:
    """Communicates with the guest agent over Firecracker's vsock UDS.

    Firecracker vsock protocol: connect to UDS, send "CONNECT {port}\\n",
    receive "OK ...\\n", then bidirectional stream to guest.
    """

    def __init__(self, vsock_uds_path: str):
        self._uds_path = vsock_uds_path
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _vsock_connect(self):
        """Establish a vsock connection to the guest agent."""
        reader, writer = await asyncio.open_unix_connection(self._uds_path)
        writer.write(f"CONNECT {VSOCK_PORT}\n".encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not response.startswith(b"OK"):
            writer.close()
            await writer.wait_closed()
            raise ConnectionError(f"vsock CONNECT failed: {response.decode().strip()}")
        return reader, writer

    async def _request(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return the response."""
        reader, writer = await self._vsock_connect()
        try:
            req = {"id": self._next_id(), "method": method}
            if params:
                req["params"] = params

            payload = json.dumps(req).encode()
            writer.write(struct.pack(">I", len(payload)))
            writer.write(payload)
            await writer.drain()

            len_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=60.0)
            msg_len = struct.unpack(">I", len_bytes)[0]
            data = await asyncio.wait_for(reader.readexactly(msg_len), timeout=60.0)
            resp = json.loads(data)

            if resp.get("error"):
                raise RuntimeError(f"guest-agent error: {resp['error']}")
            return resp.get("result", {})
        finally:
            writer.close()
            await writer.wait_closed()

    async def ping(self) -> bool:
        try:
            result = await self._request("ping")
            return result.get("status") == "ok"
        except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError, OSError, ConnectionError):
            return False

    async def exec(self, command: str, timeout: int = 30) -> dict:
        return await self._request("exec", {"command": command, "timeout": timeout})

    async def read_file(self, path: str) -> bytes:
        result = await self._request("read_file", {"path": path})
        return base64.b64decode(result["content"])

    async def write_file(self, path: str, data: bytes) -> int:
        content = base64.b64encode(data).decode()
        result = await self._request("write_file", {"path": path, "content": content})
        return result["bytes_written"]

    async def list_files(self, path: str = "/workspace") -> list[dict]:
        result = await self._request("list_files", {"path": path})
        return result["entries"]


# --- VM State Tracking ---


@dataclass
class VMInstance:
    vm_id: str
    fc_process: subprocess.Popen
    socket_path: str
    vsock_uds_path: str
    rootfs_path: str
    work_dir: str  # temp directory for this VM's files
    client: VsockClient = field(init=False)

    def __post_init__(self):
        self.client = VsockClient(self.vsock_uds_path)


# --- Firecracker Backend ---


class FirecrackerBackend:
    """SandboxBackend implementation using Firecracker snapshot restore."""

    def __init__(self):
        self._vms: dict[str, VMInstance] = {}
        self._snapshot_dir = settings.firecracker_snapshot_path
        self._kernel_path = settings.firecracker_kernel_path
        self._rootfs_path = settings.firecracker_rootfs_path

    def create(self) -> str:
        """Restore a microVM from snapshot. Returns VM ID."""
        vm_id = uuid.uuid4().hex[:12]
        work_dir = tempfile.mkdtemp(prefix=f"fc-{vm_id}-")

        socket_path = os.path.join(work_dir, "api.sock")
        vsock_uds_path = os.path.join(work_dir, "vsock.sock")

        # CoW copy of rootfs (reflink if filesystem supports it)
        rootfs_cow = os.path.join(work_dir, "rootfs.ext4")
        shutil.copy2(self._rootfs_path, rootfs_cow)

        # Start Firecracker process
        fc_proc = subprocess.Popen(
            [settings.firecracker_bin, "--api-sock", socket_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Wait for API socket
            self._wait_for_socket(socket_path)

            # Restore from snapshot
            self._restore_snapshot(socket_path, vsock_uds_path, rootfs_cow)

            vm = VMInstance(
                vm_id=vm_id,
                fc_process=fc_proc,
                socket_path=socket_path,
                vsock_uds_path=vsock_uds_path,
                rootfs_path=rootfs_cow,
                work_dir=work_dir,
            )
            self._vms[vm_id] = vm

            logger.info("Firecracker VM %s created (pid=%d)", vm_id, fc_proc.pid)
            return vm_id

        except Exception:
            fc_proc.terminate()
            fc_proc.wait(timeout=5)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def _wait_for_socket(self, socket_path: str, timeout: float = 5.0):
        """Block until the Firecracker API socket appears."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(socket_path):
                return
            time.sleep(0.05)
        raise RuntimeError(f"Firecracker socket did not appear at {socket_path}")

    def _restore_snapshot(
        self, socket_path: str, vsock_uds_path: str, rootfs_path: str
    ):
        """Load snapshot via Firecracker REST API (synchronous)."""
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(transport=transport, timeout=30) as client:
            # Pre-configure host-side resources before snapshot load

            # Drive (point to our CoW copy)
            client.put(
                "http://localhost/drives/rootfs",
                json={
                    "drive_id": "rootfs",
                    "path_on_host": rootfs_path,
                    "is_root_device": True,
                    "is_read_only": False,
                },
            ).raise_for_status()

            # Vsock (unique UDS path for this VM)
            client.put(
                "http://localhost/vsock",
                json={
                    "guest_cid": 3,
                    "uds_path": vsock_uds_path,
                },
            ).raise_for_status()

            # Load snapshot with MAP_PRIVATE for CoW memory sharing
            snap_path = os.path.join(self._snapshot_dir, "vmstate.bin")
            mem_path = os.path.join(self._snapshot_dir, "memory.bin")

            client.put(
                "http://localhost/snapshot/load",
                json={
                    "snapshot_path": snap_path,
                    "mem_backend": {
                        "backend_path": mem_path,
                        "backend_type": "File",
                    },
                    "enable_diff_snapshots": False,
                },
            ).raise_for_status()

            # Resume
            client.patch(
                "http://localhost/vm",
                json={"state": "Resumed"},
            ).raise_for_status()

    def exec(self, vm_id: str, command: str, timeout: int = 30) -> dict:
        vm = self._get_vm(vm_id)
        return self._run_async(vm.client.exec(command, timeout))

    def copy_to(self, vm_id: str, local_path: str, remote_path: str):
        vm = self._get_vm(vm_id)
        with open(local_path, "rb") as f:
            data = f.read()
        self._run_async(vm.client.write_file(remote_path, data))

    def copy_from(self, vm_id: str, remote_path: str) -> bytes:
        vm = self._get_vm(vm_id)
        return self._run_async(vm.client.read_file(remote_path))

    def list_files(self, vm_id: str, path: str = "/workspace") -> list[str]:
        vm = self._get_vm(vm_id)
        entries = self._run_async(vm.client.list_files(path))
        return [e["name"] for e in entries]

    def destroy(self, vm_id: str):
        vm = self._vms.pop(vm_id, None)
        if vm is None:
            return
        try:
            vm.fc_process.terminate()
            vm.fc_process.wait(timeout=5)
        except Exception:
            vm.fc_process.kill()
        shutil.rmtree(vm.work_dir, ignore_errors=True)
        logger.info("Firecracker VM %s destroyed", vm_id)

    def is_running(self, vm_id: str) -> bool:
        vm = self._vms.get(vm_id)
        if vm is None:
            return False
        return vm.fc_process.poll() is None

    @property
    def recyclable(self) -> bool:
        return False

    def _get_vm(self, vm_id: str) -> VMInstance:
        vm = self._vms.get(vm_id)
        if vm is None:
            raise RuntimeError(f"VM {vm_id} not found")
        return vm

    def _run_async(self, coro):
        """Run an async coroutine from sync context, handling nested event loops."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Inside a running loop — offload to a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
