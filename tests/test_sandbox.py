import tempfile
import os

import pytest

from src.sandbox import SandboxManager


@pytest.fixture
def sandbox():
    manager = SandboxManager()
    container_id = manager.create()
    yield manager, container_id
    manager.destroy(container_id)


@pytest.mark.integration
def test_create_and_destroy():
    manager = SandboxManager()
    container_id = manager.create()
    assert isinstance(container_id, str)
    assert len(container_id) > 0
    manager.destroy(container_id)


@pytest.mark.integration
def test_exec_echo(sandbox):
    manager, container_id = sandbox
    result = manager.exec(container_id, "echo hello")
    assert result["stdout"] == "hello\n"
    assert result["return_code"] == 0


@pytest.mark.integration
def test_exec_stderr(sandbox):
    manager, container_id = sandbox
    result = manager.exec(container_id, "sh -c 'echo error_output >&2'")
    assert "error_output" in result["stderr"]


@pytest.mark.integration
def test_copy_to_and_from(sandbox):
    manager, container_id = sandbox
    content = b"hello from harness\n"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        container_path = "/workspace/transferred.txt"
        manager.copy_to(container_id, tmp_path, container_path)
        returned = manager.copy_from(container_id, container_path)
        assert returned == content
    finally:
        os.unlink(tmp_path)


@pytest.mark.integration
def test_list_files(sandbox):
    manager, container_id = sandbox
    manager.exec(container_id, "touch /workspace/test.txt")
    lines = manager.list_files(container_id, "/workspace")
    filenames = " ".join(lines)
    assert "test.txt" in filenames
