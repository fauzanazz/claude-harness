import asyncio

import pytest

from src.sandbox import SandboxManager
from src.pool import ContainerPool


@pytest.fixture
async def pool():
    sandbox = SandboxManager()
    p = ContainerPool(sandbox, min_size=1, max_size=3)
    await p.start()
    yield p
    await p.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_returns_container(pool):
    container_id = await pool.claim("test-session")
    assert isinstance(container_id, str)
    assert len(container_id) > 0
    assert pool.is_container_active(container_id) == "test-session"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_and_reclaim(pool):
    cid = await pool.claim("s1")
    # Write a file to verify it gets cleaned up
    pool.sandbox.exec(cid, "echo 'dirty' > /workspace/dirty.txt")
    await pool.release(cid)

    # Container should no longer be active
    assert pool.is_container_active(cid) is None

    # Reclaim — should get a clean container
    cid2 = await pool.claim("s2")
    result = pool.sandbox.exec(cid2, "ls /workspace/dirty.txt 2>&1")
    assert result["return_code"] != 0, "Workspace should have been cleaned on release"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_adopt_existing_container(pool):
    cid = await pool.claim("s1")
    pool.sandbox.exec(cid, "echo 'keep me' > /workspace/data.txt")
    await pool.release(cid)

    # Adopt the released container (it's in the pool now, not active)
    # First, drain it from the available queue
    drained = pool._available.get_nowait()
    assert drained == cid
    pool.adopt(cid, "s2")

    # File should still be there (release cleans, but let's verify adopt works)
    assert pool.is_container_active(cid) == "s2"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_adopt_bound_container_raises(pool):
    cid = await pool.claim("s1")
    with pytest.raises(ValueError, match="already bound"):
        pool.adopt(cid, "s2")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pool_exhaustion(pool):
    """Pool with max_size=3 should raise after 3 claims."""
    ids = []
    for i in range(3):
        ids.append(await pool.claim(f"s{i}"))
    with pytest.raises(RuntimeError, match="exhausted"):
        await pool.claim("s3")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_is_container_running(pool):
    cid = await pool.claim("s1")
    assert pool.is_container_running(cid) is True
    pool.sandbox.destroy(cid)
    pool._active.pop(cid, None)
    assert pool.is_container_running(cid) is False
