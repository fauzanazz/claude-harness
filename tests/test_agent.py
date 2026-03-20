import pytest
from src.sandbox import SandboxManager
from src.agent import AgentLoop


@pytest.fixture
def sandbox():
    manager = SandboxManager()
    container_id = manager.create()
    yield manager, container_id
    manager.destroy(container_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_simple_math(sandbox):
    """Ask Claude to use bash to calculate 2+2, verify it returns 4."""
    manager, container_id = sandbox
    agent = AgentLoop(manager, container_id)
    messages = [{"role": "user", "content": "What is 2+2? Use bash_execute to calculate it with 'echo $((2+2))'. Reply with just the number."}]

    events = []
    async for event in agent.run(messages):
        events.append(event)

    # Should have at least one tool_call and text_delta
    event_types = [e["type"] for e in events]
    assert "tool_call" in event_types, "Expected a tool call"
    assert "text_delta" in event_types, "Expected text output"

    # The final text should contain "4"
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "4" in text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_creates_file(sandbox):
    """Ask Claude to create a file and verify it exists."""
    manager, container_id = sandbox
    agent = AgentLoop(manager, container_id)
    messages = [{"role": "user", "content": "Create a file at /workspace/test.txt containing 'hello world' using the write_file tool."}]

    events = []
    async for event in agent.run(messages):
        events.append(event)

    # Verify tool was called
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) > 0
    assert any(tc["name"] == "write_file" for tc in tool_calls)

    # Verify file actually exists in container
    result = manager.exec(container_id, "cat /workspace/test.txt")
    assert "hello world" in result["stdout"]
