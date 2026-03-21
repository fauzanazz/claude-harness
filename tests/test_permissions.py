import asyncio

import pytest

from src.permissions import PermissionManager


def test_default_allows_all():
    pm = PermissionManager()
    assert pm.check("read_file") == "allow"
    assert pm.check("bash_execute") == "allow"
    assert pm.check("anything") == "allow"


def test_denied_tools():
    pm = PermissionManager(denied_tools=["bash_execute"])
    assert pm.check("bash_execute") == "deny"
    assert pm.check("read_file") == "allow"


def test_allowed_tools_whitelist():
    pm = PermissionManager(allowed_tools=["read_file", "grep_search"])
    assert pm.check("read_file") == "allow"
    assert pm.check("grep_search") == "allow"
    assert pm.check("bash_execute") == "deny"
    assert pm.check("write_file") == "deny"


def test_require_approval():
    pm = PermissionManager(require_approval=["write_file"])
    assert pm.check("write_file") == "needs_approval"
    assert pm.check("read_file") == "allow"


def test_denied_overrides_approval():
    pm = PermissionManager(denied_tools=["bash_execute"], require_approval=["bash_execute"])
    assert pm.check("bash_execute") == "deny"


def test_request_and_resolve():
    pm = PermissionManager(require_approval=["write_file"])
    pending = pm.request_approval("write_file", {"path": "/workspace/foo.txt"})
    assert pending.request_id
    assert not pending.event.is_set()

    pm.resolve(pending.request_id, "approve")
    assert pending.event.is_set()
    assert pending.decision == "approve"


def test_resolve_unknown_raises():
    pm = PermissionManager()
    with pytest.raises(KeyError):
        pm.resolve("nonexistent", "approve")


@pytest.mark.asyncio
async def test_wait_for_decision_approve():
    pm = PermissionManager(require_approval=["write_file"], timeout=5)
    pending = pm.request_approval("write_file", {"path": "/workspace/foo.txt"})

    async def resolve_later():
        await asyncio.sleep(0.1)
        pm.resolve(pending.request_id, "approve")

    asyncio.create_task(resolve_later())
    decision = await pm.wait_for_decision(pending)
    assert decision == "approve"


@pytest.mark.asyncio
async def test_wait_for_decision_timeout():
    pm = PermissionManager(require_approval=["write_file"], timeout=0.2)
    pending = pm.request_approval("write_file", {"path": "/workspace/foo.txt"})
    decision = await pm.wait_for_decision(pending)
    assert decision == "deny"  # timeout defaults to deny
