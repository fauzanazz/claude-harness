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
    # Create session
    resp = await client.post("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    session_id = data["id"]

    # Delete session
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
    # Create session
    resp = await client.post("/sessions")
    session_id = resp.json()["id"]

    try:
        # List files
        resp = await client.get(f"/sessions/{session_id}/files")
        assert resp.status_code == 200
        assert "files" in resp.json()
    finally:
        await client.delete(f"/sessions/{session_id}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_and_download_file(client):
    # Create session
    resp = await client.post("/sessions")
    session_id = resp.json()["id"]

    try:
        # Upload file
        resp = await client.post(
            f"/sessions/{session_id}/files",
            files={"file": ("test.txt", b"hello from upload", "text/plain")},
        )
        assert resp.status_code == 200
        upload_path = resp.json()["path"]
        assert "test.txt" in upload_path

        # Download file - strip leading /
        download_path = upload_path.lstrip("/")
        resp = await client.get(f"/sessions/{session_id}/files/{download_path}")
        assert resp.status_code == 200
        assert b"hello from upload" in resp.content
    finally:
        await client.delete(f"/sessions/{session_id}")
