#!/usr/bin/env python3
"""Interactive CLI for testing the Claude Harness API."""

import argparse
import json
import sys

import httpx

DEFAULT_BASE = "http://localhost:8000"


def get_client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=10)


def cmd_create(client: httpx.Client, args):
    payload = {}
    if args.container_id:
        payload["container_id"] = args.container_id
    resp = client.post("/sessions", json=payload if payload else None)
    resp.raise_for_status()
    data = resp.json()
    print(f"Session:   {data['id']}")
    print(f"Container: {data['container_id']}")
    print(f"Created:   {data['created_at']}")


def cmd_delete(client: httpx.Client, args):
    resp = client.delete(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    print(f"Deleted session {args.session_id}")


def cmd_chat(client: httpx.Client, args):
    print(f"Chatting with session {args.session_id}")
    print(f"Streaming from {client.base_url}...")
    print("-" * 60)

    with httpx.Client(base_url=str(client.base_url), timeout=300) as stream_client:
        with stream_client.stream(
            "POST",
            f"/sessions/{args.session_id}/messages",
            json={"content": args.message},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())

                    if event_type == "text_delta":
                        print(data.get("text", ""), end="", flush=True)
                    elif event_type == "tool_call":
                        print(f"\n[tool_call] {data['name']}({json.dumps(data.get('args', {}), indent=2)})")
                    elif event_type == "tool_result":
                        content = data.get("content", "")
                        if isinstance(content, list):
                            content = "\n".join(
                                c.get("text", "") for c in content if isinstance(c, dict)
                            )
                        preview = content[:200] + "..." if len(str(content)) > 200 else content
                        is_error = data.get("is_error", False)
                        label = "tool_error" if is_error else "tool_result"
                        print(f"[{label}] {preview}")
                    elif event_type == "permission_request":
                        print(f"\n[permission] Tool '{data['tool']}' needs approval (id: {data['request_id']})")
                    elif event_type == "usage":
                        print(f"\n[usage] in={data.get('input_tokens', 0)} out={data.get('output_tokens', 0)}")
                    elif event_type == "compaction":
                        print(f"[compaction] summary_length={data.get('summary_length', 0)}")
                    elif event_type == "done":
                        pass

    print()


def cmd_files(client: httpx.Client, args):
    path = args.path or "/workspace"
    resp = client.get(f"/sessions/{args.session_id}/files", params={"path": path})
    resp.raise_for_status()
    files = resp.json()["files"]
    if not files:
        print(f"No files in {path}")
        return
    for f in files:
        print(f)


def cmd_upload(client: httpx.Client, args):
    with open(args.local_path, "rb") as f:
        resp = client.post(
            f"/sessions/{args.session_id}/files",
            files={"file": (args.local_path.split("/")[-1], f)},
        )
    resp.raise_for_status()
    print(f"Uploaded to {resp.json()['path']}")


def cmd_download(client: httpx.Client, args):
    remote = args.remote_path.lstrip("/")
    resp = client.get(f"/sessions/{args.session_id}/files/{remote}")
    resp.raise_for_status()
    if args.output:
        with open(args.output, "wb") as f:
            f.write(resp.content)
        print(f"Saved to {args.output}")
    else:
        sys.stdout.buffer.write(resp.content)
        sys.stdout.buffer.write(b"\n")


def cmd_approve(client: httpx.Client, args):
    decision = "approve" if not args.deny else "deny"
    resp = client.post(
        f"/sessions/{args.session_id}/permissions/{args.request_id}",
        json={"decision": decision},
    )
    resp.raise_for_status()
    print(f"Permission {args.request_id}: {decision}d")


def cmd_interactive(client: httpx.Client, args):
    print("Creating session...")
    payload = {}
    if args.container_id:
        payload["container_id"] = args.container_id
    resp = client.post("/sessions", json=payload if payload else None)
    resp.raise_for_status()
    session = resp.json()
    session_id = session["id"]
    print(f"Session:   {session_id}")
    print(f"Container: {session['container_id']}")
    print("=" * 60)
    print("Type a message to chat. Commands:")
    print("  /files [path]    - list files in sandbox")
    print("  /upload <path>   - upload a local file")
    print("  /quit            - delete session and exit")
    print("=" * 60)

    try:
        while True:
            try:
                user_input = input("\nyou> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            if user_input == "/quit":
                break

            if user_input.startswith("/files"):
                parts = user_input.split(maxsplit=1)
                path = parts[1] if len(parts) > 1 else "/workspace"
                resp = client.get(f"/sessions/{session_id}/files", params={"path": path})
                resp.raise_for_status()
                for f in resp.json()["files"]:
                    print(f"  {f}")
                continue

            if user_input.startswith("/upload "):
                local_path = user_input[len("/upload "):].strip()
                with open(local_path, "rb") as f:
                    resp = client.post(
                        f"/sessions/{session_id}/files",
                        files={"file": (local_path.split("/")[-1], f)},
                    )
                resp.raise_for_status()
                print(f"  Uploaded to {resp.json()['path']}")
                continue

            # Chat
            print()
            chat_args = argparse.Namespace(session_id=session_id, message=user_input)
            cmd_chat(client, chat_args)

    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"\nDeleting session {session_id}...")
    client.delete(f"/sessions/{session_id}")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Claude Harness CLI")
    parser.add_argument("--url", default=DEFAULT_BASE, help=f"Base URL (default: {DEFAULT_BASE})")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p = sub.add_parser("create", help="Create a new session")
    p.add_argument("--container-id", help="Reconnect to existing container")

    # delete
    p = sub.add_parser("delete", help="Delete a session")
    p.add_argument("session_id")

    # chat
    p = sub.add_parser("chat", help="Send a message and stream the response")
    p.add_argument("session_id")
    p.add_argument("message")

    # files
    p = sub.add_parser("files", help="List files in sandbox")
    p.add_argument("session_id")
    p.add_argument("--path", default=None)

    # upload
    p = sub.add_parser("upload", help="Upload a file to sandbox")
    p.add_argument("session_id")
    p.add_argument("local_path")

    # download
    p = sub.add_parser("download", help="Download a file from sandbox")
    p.add_argument("session_id")
    p.add_argument("remote_path")
    p.add_argument("-o", "--output", help="Save to local file instead of stdout")

    # approve/deny permission
    p = sub.add_parser("approve", help="Approve or deny a permission request")
    p.add_argument("session_id")
    p.add_argument("request_id")
    p.add_argument("--deny", action="store_true")

    # interactive
    p = sub.add_parser("interactive", aliases=["i"], help="Interactive chat session")
    p.add_argument("--container-id", help="Reconnect to existing container")

    args = parser.parse_args()
    client = get_client(args.url)

    commands = {
        "create": cmd_create,
        "delete": cmd_delete,
        "chat": cmd_chat,
        "files": cmd_files,
        "upload": cmd_upload,
        "download": cmd_download,
        "approve": cmd_approve,
        "interactive": cmd_interactive,
        "i": cmd_interactive,
    }
    commands[args.command](client, args)


if __name__ == "__main__":
    main()
