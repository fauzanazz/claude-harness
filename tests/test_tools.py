import pytest

from src.sandbox import SandboxManager
from src.tools import bash_execute, dispatch_tool, grep_search, read_file, write_file


@pytest.fixture(scope="module")
def sandbox_container():
    sandbox = SandboxManager()
    container_id = sandbox.create()
    yield sandbox, container_id
    sandbox.destroy(container_id)


@pytest.mark.integration
def test_write_and_read_file(sandbox_container):
    sandbox, cid = sandbox_container
    write_result = write_file(sandbox, cid, "/workspace/hello.txt", "world")
    assert "Successfully wrote" in write_result

    read_result = read_file(sandbox, cid, "/workspace/hello.txt")
    assert "world" in read_result


@pytest.mark.integration
def test_bash_execute(sandbox_container):
    sandbox, cid = sandbox_container
    result = bash_execute(sandbox, cid, "echo 42")
    assert "42" in result
    assert "return_code: 0" in result


@pytest.mark.integration
def test_grep_search(sandbox_container):
    sandbox, cid = sandbox_container
    write_file(sandbox, cid, "/workspace/grep_target.txt", "the quick brown fox")
    result = grep_search(sandbox, cid, "quick", "/workspace/grep_target.txt")
    assert "quick" in result


@pytest.mark.integration
def test_grep_no_matches(sandbox_container):
    sandbox, cid = sandbox_container
    write_file(sandbox, cid, "/workspace/grep_target.txt", "the quick brown fox")
    result = grep_search(sandbox, cid, "zzznomatchzzz", "/workspace/grep_target.txt")
    assert result == "no matches"


@pytest.mark.integration
def test_dispatch_tool(sandbox_container):
    sandbox, cid = sandbox_container
    result = dispatch_tool("bash_execute", {"command": "echo dispatch_works"}, sandbox, cid)
    assert "dispatch_works" in result


@pytest.mark.integration
def test_dispatch_unknown_tool(sandbox_container):
    sandbox, cid = sandbox_container
    with pytest.raises(ValueError):
        dispatch_tool("nonexistent", {}, sandbox, cid)
