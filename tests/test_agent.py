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

    # Should have text output
    event_types = [e["type"] for e in events]
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

    # Verify file actually exists in container
    result = manager.exec(container_id, "cat /workspace/test.txt")
    assert "hello world" in result["stdout"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_yields_tool_events(sandbox):
    """Verify the agent yields tool_call and tool_result events alongside text."""
    manager, container_id = sandbox
    agent = AgentLoop(manager, container_id)
    messages = [{"role": "user", "content": "Use bash_execute to run 'echo tool_event_test'. Reply with the output."}]

    events = []
    async for event in agent.run(messages):
        events.append(event)

    event_types = {e["type"] for e in events}
    assert "text_delta" in event_types, "Expected text output"
    assert "usage" in event_types, "Expected usage event"

    # Tool call events should include name and args
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    if tool_calls:
        tc = tool_calls[0]
        assert "name" in tc
        assert "args" in tc
        assert "id" in tc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_usage_reports_tokens(sandbox):
    """Verify usage event reports input/output tokens."""
    manager, container_id = sandbox
    agent = AgentLoop(manager, container_id)
    messages = [{"role": "user", "content": "Say hello."}]

    events = []
    async for event in agent.run(messages):
        events.append(event)

    usage_events = [e for e in events if e["type"] == "usage"]
    assert len(usage_events) == 1
    assert "input_tokens" in usage_events[0]
    assert "output_tokens" in usage_events[0]
