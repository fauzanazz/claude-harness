import pytest
from httpx import AsyncClient, ASGITransport
from src.api import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_and_delete_session(client):
    resp = await client.post("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "container_id" in data
    session_id = data["id"]

    resp = await client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_not_found(client):
    resp = await client.delete("/sessions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_files(client):
    resp = await client.post("/sessions")
    session_id = resp.json()["id"]

    try:
        resp = await client.get(f"/sessions/{session_id}/files")
        assert resp.status_code == 200
        assert "files" in resp.json()
    finally:
        await client.delete(f"/sessions/{session_id}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_and_download_file(client):
    resp = await client.post("/sessions")
    session_id = resp.json()["id"]

    try:
        resp = await client.post(
            f"/sessions/{session_id}/files",
            files={"file": ("test.txt", b"hello from upload", "text/plain")},
        )
        assert resp.status_code == 200
        upload_path = resp.json()["path"]
        assert "test.txt" in upload_path

        download_path = upload_path.lstrip("/")
        resp = await client.get(f"/sessions/{session_id}/files/{download_path}")
        assert resp.status_code == 200
        assert b"hello from upload" in resp.content
    finally:
        await client.delete(f"/sessions/{session_id}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconnect_to_container(client):
    """Create a session, note container_id, delete session, reconnect."""
    resp = await client.post("/sessions")
    data = resp.json()
    session_id = data["id"]
    container_id = data["container_id"]

    # Delete session (container goes back to pool)
    await client.delete(f"/sessions/{session_id}")

    # Reconnect using the container_id
    resp = await client.post("/sessions", json={"container_id": container_id})
    assert resp.status_code == 200
    data2 = resp.json()
    assert data2["container_id"] == container_id
    assert data2["id"] != session_id  # New session ID

    # Cleanup
    await client.delete(f"/sessions/{data2['id']}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconnect_conflict(client):
    """Reconnecting to a container bound to an active session returns 409."""
    resp = await client.post("/sessions")
    data = resp.json()
    session_id = data["id"]
    container_id = data["container_id"]

    try:
        resp = await client.post("/sessions", json={"container_id": container_id})
        assert resp.status_code == 409
    finally:
        await client.delete(f"/sessions/{session_id}")
