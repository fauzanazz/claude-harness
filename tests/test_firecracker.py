"""Unit tests for FirecrackerBackend and VsockClient.

Tests the vsock protocol, client serialization, and backend logic
without requiring Firecracker or a running VM.
"""

import asyncio
import json
import struct
import tempfile
import os
from unittest.mock import MagicMock, patch

import pytest

from src.firecracker import FirecrackerBackend, VsockClient, VMInstance


# --- VsockClient protocol tests ---


async def mock_vsock_server(socket_path: str, responses: list[dict]):
    """Start a Unix socket server that speaks the guest-agent protocol."""
    response_iter = iter(responses)

    async def handle_client(reader, writer):
        try:
            while True:
                len_bytes = await reader.readexactly(4)
                msg_len = struct.unpack(">I", len_bytes)[0]
                data = await reader.readexactly(msg_len)
                req = json.loads(data)

                resp = next(response_iter, {"id": req["id"], "error": "no more responses"})
                resp["id"] = req["id"]
                payload = json.dumps(resp).encode()
                writer.write(struct.pack(">I", len(payload)))
                writer.write(payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, StopIteration):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, socket_path)
    return server


@pytest.fixture
def vsock_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.mark.asyncio
async def test_vsock_client_ping(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    server = await mock_vsock_server(
        connect_path, [{"result": {"status": "ok"}}]
    )
    async with server:
        client = VsockClient(uds_base)
        result = await client.ping()
        assert result is True
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_exec(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    server = await mock_vsock_server(
        connect_path,
        [{"result": {"stdout": "hello\n", "stderr": "", "return_code": 0}}],
    )
    async with server:
        client = VsockClient(uds_base)
        result = await client.exec("echo hello")
        assert result["stdout"] == "hello\n"
        assert result["return_code"] == 0
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_read_file(vsock_dir):
    import base64

    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    content = base64.b64encode(b"file contents").decode()
    server = await mock_vsock_server(
        connect_path,
        [{"result": {"content": content, "size": 13}}],
    )
    async with server:
        client = VsockClient(uds_base)
        data = await client.read_file("/workspace/test.txt")
        assert data == b"file contents"
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_write_file(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    server = await mock_vsock_server(
        connect_path, [{"result": {"bytes_written": 5}}]
    )
    async with server:
        client = VsockClient(uds_base)
        written = await client.write_file("/workspace/out.txt", b"hello")
        assert written == 5
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_list_files(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    server = await mock_vsock_server(
        connect_path,
        [
            {
                "result": {
                    "entries": [
                        {"name": "file.txt", "is_dir": False},
                        {"name": "subdir", "is_dir": True},
                    ]
                }
            }
        ],
    )
    async with server:
        client = VsockClient(uds_base)
        entries = await client.list_files("/workspace")
        assert len(entries) == 2
        assert entries[0]["name"] == "file.txt"
        assert entries[1]["is_dir"] is True
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_error_response(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    connect_path = f"{uds_base}_5000"

    server = await mock_vsock_server(
        connect_path, [{"error": "file not found"}]
    )
    async with server:
        client = VsockClient(uds_base)
        with pytest.raises(RuntimeError, match="file not found"):
            await client.read_file("/nonexistent")
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_vsock_client_ping_connection_refused(vsock_dir):
    uds_base = os.path.join(vsock_dir, "vsock.sock")
    client = VsockClient(uds_base)
    # No server running — should return False, not raise
    result = await client.ping()
    assert result is False


# --- FirecrackerBackend unit tests (no real Firecracker) ---


def test_backend_recyclable_is_false():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        backend._vms = {}
        assert backend.recyclable is False


def test_backend_is_running_unknown_vm():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        backend._vms = {}
        assert backend.is_running("nonexistent") is False


def test_backend_destroy_unknown_vm():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        backend._vms = {}
        # Should not raise
        backend.destroy("nonexistent")


def test_backend_get_vm_raises():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        backend._vms = {}
        with pytest.raises(RuntimeError, match="not found"):
            backend._get_vm("missing")


def test_backend_is_running_with_live_process():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        vm = MagicMock(spec=VMInstance)
        vm.fc_process = mock_proc
        backend._vms = {"vm1": vm}
        assert backend.is_running("vm1") is True


def test_backend_is_running_with_dead_process():
    with patch.object(FirecrackerBackend, "__init__", lambda self: None):
        backend = FirecrackerBackend()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited
        vm = MagicMock(spec=VMInstance)
        vm.fc_process = mock_proc
        backend._vms = {"vm1": vm}
        assert backend.is_running("vm1") is False
