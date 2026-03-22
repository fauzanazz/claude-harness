#!/usr/bin/env python3
"""Bake a Firecracker snapshot from a running microVM.

Boot a microVM with the rootfs, wait for the guest agent to respond to ping,
then create a full snapshot (memory + vmstate + disk). The resulting snapshot
can be restored in ~1-5ms via mmap(MAP_PRIVATE).

Prerequisites:
    - Firecracker binary on PATH (or FIRECRACKER_BIN env var)
    - Kernel image at firecracker/kernel/vmlinux (or KERNEL_PATH env var)
    - Rootfs at firecracker/rootfs/rootfs.ext4 (or ROOTFS_PATH env var)

Usage:
    python bake-snapshot.py                         # defaults
    FIRECRACKER_BIN=/usr/bin/firecracker python bake-snapshot.py
"""

import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent

FIRECRACKER_BIN = os.environ.get("FIRECRACKER_BIN", "firecracker")
KERNEL_PATH = os.environ.get(
    "KERNEL_PATH", str(SCRIPT_DIR.parent / "kernel" / "vmlinux")
)
ROOTFS_PATH = os.environ.get(
    "ROOTFS_PATH", str(SCRIPT_DIR.parent / "rootfs" / "rootfs.ext4")
)
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", str(SCRIPT_DIR / "base"))

VCPU_COUNT = int(os.environ.get("VCPU_COUNT", "1"))
MEM_SIZE_MIB = int(os.environ.get("MEM_SIZE_MIB", "256"))
VSOCK_CID = 3  # Guest CID for the base VM during baking
VSOCK_PORT = 5000  # Must match guest-agent
PING_TIMEOUT = 30  # seconds to wait for agent
PING_INTERVAL = 0.5  # seconds between ping attempts


def create_socket_path() -> str:
    """Create a temp path for the Firecracker API socket."""
    tmpdir = tempfile.mkdtemp(prefix="fc-bake-")
    return os.path.join(tmpdir, "firecracker.sock")


async def fc_api(
    client: httpx.AsyncClient, method: str, path: str, **kwargs
) -> httpx.Response:
    """Make a request to the Firecracker API."""
    resp = await client.request(method, f"http://localhost{path}", **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"Firecracker API error: {resp.status_code} {resp.text}")
    return resp


async def configure_vm(client: httpx.AsyncClient, rootfs_cow: str):
    """Configure the microVM before boot."""
    # Machine config
    await fc_api(
        client,
        "PUT",
        "/machine-config",
        json={
            "vcpu_count": VCPU_COUNT,
            "mem_size_mib": MEM_SIZE_MIB,
        },
    )

    # Boot source
    await fc_api(
        client,
        "PUT",
        "/boot-source",
        json={
            "kernel_image_path": KERNEL_PATH,
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/init.sh",
        },
    )

    # Root drive (use CoW copy so original rootfs stays clean)
    await fc_api(
        client,
        "PUT",
        "/drives/rootfs",
        json={
            "drive_id": "rootfs",
            "path_on_host": rootfs_cow,
            "is_root_device": True,
            "is_read_only": False,
        },
    )

    # Vsock device
    await fc_api(
        client,
        "PUT",
        "/vsock",
        json={
            "guest_cid": VSOCK_CID,
            "uds_path": "/tmp/fc-vsock.sock",
        },
    )


async def ping_agent(vsock_uds_path: str) -> bool:
    """Send a ping to the guest agent over vsock UDS path.

    Firecracker exposes vsock connections via a Unix domain socket.
    To connect to guest port 5000, connect to {uds_path}_{port}.
    """
    connect_path = f"{vsock_uds_path}_{VSOCK_PORT}"

    try:
        reader, writer = await asyncio.open_unix_connection(connect_path)

        # Send ping request using length-prefixed JSON protocol
        req = json.dumps({"id": 1, "method": "ping"}).encode()
        writer.write(struct.pack(">I", len(req)))
        writer.write(req)
        await writer.drain()

        # Read response
        len_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=2.0)
        msg_len = struct.unpack(">I", len_bytes)[0]
        data = await asyncio.wait_for(reader.readexactly(msg_len), timeout=2.0)
        resp = json.loads(data)

        writer.close()
        await writer.wait_closed()

        return resp.get("result", {}).get("status") == "ok"
    except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError, OSError):
        return False


async def wait_for_agent(vsock_uds_path: str):
    """Wait for the guest agent to respond to ping."""
    print(f"Waiting for guest agent (timeout: {PING_TIMEOUT}s)...")
    start = time.monotonic()

    while time.monotonic() - start < PING_TIMEOUT:
        if await ping_agent(vsock_uds_path):
            elapsed = time.monotonic() - start
            print(f"Guest agent ready in {elapsed:.1f}s")
            return
        await asyncio.sleep(PING_INTERVAL)

    raise TimeoutError(f"Guest agent did not respond within {PING_TIMEOUT}s")


async def create_snapshot(client: httpx.AsyncClient, snapshot_dir: str):
    """Pause the VM and create a full snapshot."""
    # Pause first
    await fc_api(client, "PATCH", "/vm", json={"state": "Paused"})

    os.makedirs(snapshot_dir, exist_ok=True)
    mem_path = os.path.join(snapshot_dir, "memory.bin")
    snap_path = os.path.join(snapshot_dir, "vmstate.bin")

    await fc_api(
        client,
        "PUT",
        "/snapshot/create",
        json={
            "snapshot_type": "Full",
            "snapshot_path": snap_path,
            "mem_file_path": mem_path,
        },
    )

    print(f"Snapshot created at {snapshot_dir}/")
    print(f"  vmstate: {os.path.getsize(snap_path) / 1024:.0f} KB")
    print(f"  memory:  {os.path.getsize(mem_path) / (1024*1024):.0f} MB")


async def bake():
    """Main bake workflow: boot VM, wait for agent, snapshot."""
    # Validate inputs
    if not os.path.isfile(KERNEL_PATH):
        print(f"Error: kernel not found at {KERNEL_PATH}")
        print("Download a Firecracker-compatible kernel (vmlinux) and place it there.")
        sys.exit(1)

    if not os.path.isfile(ROOTFS_PATH):
        print(f"Error: rootfs not found at {ROOTFS_PATH}")
        print("Build it first: cd firecracker/rootfs && ./build-rootfs.sh")
        sys.exit(1)

    socket_path = create_socket_path()
    socket_dir = os.path.dirname(socket_path)

    # Create a CoW copy of rootfs for the bake VM
    rootfs_cow = os.path.join(socket_dir, "rootfs-cow.ext4")
    shutil.copy2(ROOTFS_PATH, rootfs_cow)

    vsock_uds_path = "/tmp/fc-vsock.sock"

    # Start Firecracker process
    print(f"Starting Firecracker (socket: {socket_path})")
    fc_proc = subprocess.Popen(
        [FIRECRACKER_BIN, "--api-sock", socket_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for socket to appear
        for _ in range(50):
            if os.path.exists(socket_path):
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Firecracker socket did not appear")

        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        async with httpx.AsyncClient(transport=transport, timeout=30) as client:
            # Configure and boot
            await configure_vm(client, rootfs_cow)

            print("Booting microVM...")
            await fc_api(client, "PUT", "/actions", json={"action_type": "InstanceStart"})

            # Wait for guest agent
            await wait_for_agent(vsock_uds_path)

            # Create snapshot
            await create_snapshot(client, SNAPSHOT_DIR)

            print("Bake complete!")

    finally:
        fc_proc.terminate()
        fc_proc.wait(timeout=5)
        # Clean up temp files
        if os.path.exists(rootfs_cow):
            os.unlink(rootfs_cow)
        if os.path.exists(socket_dir):
            shutil.rmtree(socket_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(bake())
