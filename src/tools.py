import base64

from .sandbox import SandboxManager


def read_file(sandbox: SandboxManager, container_id: str, path: str) -> str:
    result = sandbox.exec(container_id, f"cat {path}")
    if result["return_code"] != 0:
        error = result["stderr"].strip() or f"cat exited with code {result['return_code']}"
        return f"Error reading {path}: {error}"
    return result["stdout"]


def write_file(sandbox: SandboxManager, container_id: str, path: str, content: str) -> str:
    encoded = base64.b64encode(content.encode()).decode()
    result = sandbox.exec(container_id, f"echo {encoded} | base64 -d > {path}")
    if result["return_code"] != 0:
        error = result["stderr"].strip() or f"write exited with code {result['return_code']}"
        return f"Error writing {path}: {error}"
    return f"Successfully wrote to {path}"


def bash_execute(sandbox: SandboxManager, container_id: str, command: str) -> str:
    result = sandbox.exec(container_id, command)
    parts = []
    if result["stdout"]:
        parts.append(f"stdout:\n{result['stdout']}")
    if result["stderr"]:
        parts.append(f"stderr:\n{result['stderr']}")
    parts.append(f"return_code: {result['return_code']}")
    return "\n".join(parts)


def grep_search(sandbox: SandboxManager, container_id: str, pattern: str, path: str = "/workspace") -> str:
    result = sandbox.exec(container_id, f"rg {pattern} {path}")
    if result["return_code"] != 0:
        if result["stdout"].strip() == "" and result["stderr"].strip() == "":
            return "no matches"
        if result["stderr"].strip():
            return f"Error: {result['stderr'].strip()}"
        return "no matches"
    output = result["stdout"].strip()
    return output if output else "no matches"


TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file at the given path, creating or overwriting it",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "bash_execute",
        "description": "Execute a bash command and return its stdout, stderr, and return code",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a pattern in files using ripgrep",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The pattern to search for"},
                "path": {
                    "type": "string",
                    "description": "Path to search in (file or directory). Defaults to /workspace",
                },
            },
            "required": ["pattern"],
        },
    },
]

_TOOL_MAP = {
    "read_file": read_file,
    "write_file": write_file,
    "bash_execute": bash_execute,
    "grep_search": grep_search,
}


def dispatch_tool(name: str, args: dict, sandbox: SandboxManager, container_id: str) -> str:
    if name not in _TOOL_MAP:
        raise ValueError(f"Unknown tool: {name!r}")
    return _TOOL_MAP[name](sandbox, container_id, **args)
