#!/usr/bin/env python3
"""Interactive TUI for testing the Claude Harness."""

from __future__ import annotations

import json

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

BASE_URL = "http://localhost:8001"


class SessionPanel(Static):
    """Displays current session info."""

    def compose(self) -> ComposeResult:
        yield Label("No active session", id="session-status")
        yield Label("", id="session-details")


class FilePanel(RichLog):
    """Shows sandbox file listing."""
    pass


class ChatLog(RichLog):
    """Main chat display area."""
    pass


class HarnessTUI(App):
    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 4;
        grid-columns: 1fr 3fr;
        grid-rows: auto auto 1fr auto;
    }
    Header { column-span: 2; }
    Footer { column-span: 2; }
    SessionPanel {
        height: auto;
        border: solid $accent;
        padding: 1;
    }
    #help-bar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    FilePanel {
        border: solid $accent;
        min-height: 8;
    }
    ChatLog {
        border: solid $primary;
    }
    #input-row {
        column-span: 2;
        height: auto;
    }
    #prompt-input {
        width: 1fr;
    }
    """

    TITLE = "Claude Harness"
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+d", "delete_session", "Delete Session"),
        Binding("ctrl+f", "list_files", "Files"),
        Binding("ctrl+q", "quit_app", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.session_id: str | None = None
        self.container_id: str | None = None
        self.http = httpx.Client(base_url=BASE_URL, timeout=10)
        self._streaming = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionPanel(id="session-panel")
        yield Label(
            "[b]ctrl+n[/] new session  [b]ctrl+d[/] delete  [b]ctrl+f[/] files  [b]ctrl+q[/] quit",
            id="help-bar",
        )
        yield FilePanel(id="file-panel", highlight=True, markup=True)
        yield ChatLog(id="chat-log", highlight=True, markup=True, auto_scroll=True)
        yield Input(placeholder="Type a message... (creates session automatically)", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()
        self._log_system("Welcome! Type a message to start chatting (session created automatically).")
        self._log_system("Use ctrl+n / ctrl+d / ctrl+f / ctrl+q for session management.")
        self._check_server()

    # --- Helpers ---

    @work(thread=True)
    def _check_server(self) -> None:
        try:
            resp = self.http.get("/docs")
            self.call_from_thread(self._log_system, "Server connected.")
        except httpx.ConnectError:
            self.call_from_thread(
                self._log_system,
                "[red]Cannot reach server at " + BASE_URL + "[/red]\n"
                "[dim]Start it with: uv run uvicorn src.api:app --reload[/dim]",
            )
        except Exception:
            pass

    def _log_system(self, msg: str) -> None:
        self.query_one("#chat-log", ChatLog).write(f"[dim]{msg}[/dim]")

    def _log_user(self, msg: str) -> None:
        self.query_one("#chat-log", ChatLog).write(f"\n[bold cyan]you>[/bold cyan] {msg}")

    def _log_tool(self, label: str, content: str, style: str = "yellow") -> None:
        self.query_one("#chat-log", ChatLog).write(f"[{style}][{label}][/{style}] {content}")

    def _update_session_display(self) -> None:
        status = self.query_one("#session-status", Label)
        details = self.query_one("#session-details", Label)
        if self.session_id:
            status.update("[bold green]Active[/bold green]")
            details.update(
                f"[dim]Session:[/dim]   {self.session_id[:12]}...\n"
                f"[dim]Container:[/dim] {(self.container_id or 'unknown')[:12]}..."
            )
        else:
            status.update("[dim]No active session[/dim]")
            details.update("")

    # --- Session management ---

    def _create_session_sync(self) -> bool:
        """Create session synchronously. Call from worker thread only."""
        try:
            resp = self.http.post("/sessions")
            resp.raise_for_status()
            data = resp.json()
            self.session_id = data["id"]
            self.container_id = data["container_id"]
            self.call_from_thread(self._update_session_display)
            self.call_from_thread(
                self._log_system,
                f"Session created: {self.session_id[:12]}... (container: {self.container_id[:12]}...)",
            )
            return True
        except httpx.ConnectError:
            self.call_from_thread(
                self._log_system,
                "[red]Cannot reach server. Is it running?[/red]\n"
                "[dim]Start with: uv run uvicorn src.api:app --reload[/dim]",
            )
            return False
        except httpx.HTTPStatusError as e:
            self.call_from_thread(
                self._log_system,
                f"[red]Session creation failed (HTTP {e.response.status_code}): {e.response.text}[/red]",
            )
            return False
        except Exception as e:
            self.call_from_thread(self._log_system, f"[red]Failed to create session: {e}[/red]")
            return False

    @work(thread=True)
    def _create_session(self) -> None:
        self._create_session_sync()

    def action_new_session(self) -> None:
        if self.session_id:
            self.action_delete_session()
        self._create_session()

    def action_delete_session(self) -> None:
        if not self.session_id:
            self._log_system("No active session to delete.")
            return
        sid = self.session_id
        self.session_id = None
        self.container_id = None
        self._update_session_display()
        self._delete_session_bg(sid)

    @work(thread=True)
    def _delete_session_bg(self, sid: str) -> None:
        try:
            self.http.delete(f"/sessions/{sid}")
            self.call_from_thread(self._log_system, f"Deleted session {sid[:12]}...")
        except Exception as e:
            self.call_from_thread(self._log_system, f"[red]Delete failed: {e}[/red]")

    def action_list_files(self) -> None:
        if not self.session_id:
            self._log_system("No active session.")
            return
        self._fetch_files()

    @work(thread=True)
    def _fetch_files(self) -> None:
        if not self.session_id:
            return
        try:
            resp = self.http.get(f"/sessions/{self.session_id}/files", params={"path": "/workspace"})
            resp.raise_for_status()
            files = resp.json()["files"]
            panel = self.query_one("#file-panel", FilePanel)
            self.call_from_thread(panel.clear)
            if files:
                for f in files:
                    self.call_from_thread(panel.write, f"  {f}")
            else:
                self.call_from_thread(panel.write, "  (empty)")
        except Exception as e:
            self.call_from_thread(self._log_system, f"[red]File list failed: {e}[/red]")

    def action_quit_app(self) -> None:
        if self.session_id:
            try:
                self.http.delete(f"/sessions/{self.session_id}")
            except Exception:
                pass
        self.exit()

    # --- Chat ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.value = ""

        if self._streaming:
            self._log_system("[dim]Wait for the current response to finish...[/dim]")
            return

        self._log_user(msg)
        self._send_message(msg)

    @work(thread=True)
    def _send_message(self, message: str) -> None:
        # Auto-create session if needed
        if not self.session_id:
            if not self._create_session_sync():
                return

        self._streaming = True
        self.call_from_thread(
            self.query_one("#prompt-input", Input).__setattr__, "placeholder", "Waiting for response..."
        )

        try:
            with httpx.Client(base_url=BASE_URL, timeout=300) as client:
                with client.stream(
                    "POST",
                    f"/sessions/{self.session_id}/messages",
                    json={"content": message},
                ) as resp:
                    resp.raise_for_status()
                    event_type = ""
                    assistant_text: list[str] = []

                    for line in resp.iter_lines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            try:
                                data = json.loads(line[len("data:"):].strip())
                            except json.JSONDecodeError:
                                continue
                            self._handle_sse(event_type, data, assistant_text)

                    # Show collected assistant text
                    if assistant_text:
                        full = "".join(assistant_text)
                        self.call_from_thread(
                            self.query_one("#chat-log", ChatLog).write,
                            f"\n[bold green]assistant>[/bold green] {full}",
                        )

        except httpx.ConnectError:
            self.call_from_thread(
                self._log_system,
                "[red]Lost connection to server.[/red]",
            )
        except httpx.HTTPStatusError as e:
            self.call_from_thread(
                self._log_system,
                f"[red]HTTP {e.response.status_code}: {e.response.text[:200]}[/red]",
            )
        except Exception as e:
            self.call_from_thread(self._log_system, f"[red]Error: {e}[/red]")
        finally:
            self._streaming = False
            self.call_from_thread(
                self.query_one("#prompt-input", Input).__setattr__, "placeholder", "Type a message..."
            )
            self.call_from_thread(self._fetch_files)

    def _handle_sse(self, event_type: str, data: dict, text_parts: list[str]) -> None:
        if event_type == "text_delta":
            text_parts.append(data.get("text", ""))

        elif event_type == "tool_call":
            name = data.get("name", "?")
            args = data.get("args", {})
            args_str = json.dumps(args, indent=2) if args else ""
            if len(args_str) > 300:
                args_str = args_str[:300] + "..."
            self.call_from_thread(self._log_tool, f"tool: {name}", args_str)

        elif event_type == "tool_result":
            content = data.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            content = str(content)
            preview = content[:300] + "..." if len(content) > 300 else content
            is_error = data.get("is_error", False)
            style = "red" if is_error else "green"
            label = "error" if is_error else "result"
            self.call_from_thread(self._log_tool, label, preview, style)

        elif event_type == "permission_request":
            tool = data.get("tool", "?")
            req_id = data.get("request_id", "?")
            self.call_from_thread(
                self._log_system,
                f"[bold red]Permission needed for '{tool}' (id: {req_id})[/bold red]\n"
                f"[dim]From another terminal: uv run python cli.py approve {self.session_id} {req_id}[/dim]",
            )

        elif event_type == "usage":
            inp = data.get("input_tokens", 0)
            out = data.get("output_tokens", 0)
            self.call_from_thread(self._log_system, f"[dim]tokens: {inp} in / {out} out[/dim]")

        elif event_type == "compaction":
            self.call_from_thread(self._log_system, "[dim]Context compacted[/dim]")


if __name__ == "__main__":
    app = HarnessTUI()
    app.run()
