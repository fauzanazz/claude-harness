#!/usr/bin/env python3
"""Interactive TUI for testing the Claude Harness."""

from __future__ import annotations

import json

import httpx
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Footer,
    Input,
    Label,
    RichLog,
    Static,
)

BASE_URL = "http://localhost:8001"

# -- Custom theme ----------------------------------------------------------

HARNESS_THEME = Theme(
    name="harness-dark",
    primary="#7AA2F7",
    secondary="#9ECE6A",
    accent="#BB9AF7",
    foreground="#C0CAF5",
    background="#1A1B26",
    surface="#24283B",
    panel="#2F3549",
    warning="#E0AF68",
    error="#F7768E",
    success="#9ECE6A",
    dark=True,
    variables={
        "footer-key-foreground": "#7AA2F7",
        "input-selection-background": "#7aa2f740",
    },
)

# -- Colors (reused in panels) ---------------------------------------------

CLR_PRIMARY = "#7AA2F7"
CLR_GREEN = "#9ECE6A"
CLR_AMBER = "#E0AF68"
CLR_RED = "#F7768E"

# -- Widgets ----------------------------------------------------------------


class TopBar(Horizontal):
    """Compact session info bar."""

    def compose(self) -> ComposeResult:
        yield Static("\u25cf", id="status-dot")
        yield Static("No session", id="session-info")
        yield Static("", id="container-info")
        yield Horizontal(id="file-chips")


class ChatLog(RichLog):
    """Main chat display area."""
    pass


class StreamingIndicator(Static):
    """Shows streaming status."""

    def __init__(self) -> None:
        super().__init__("\u23f3 Generating\u2026", id="streaming-indicator")


# -- App --------------------------------------------------------------------


class HarnessTUI(App):
    CSS = """
    Screen {
        background: $background;
    }

    /* -- top bar --------------------------------------------------------- */

    #top-bar {
        dock: top;
        height: 3;
        background: $surface;
        border-bottom: solid $panel;
        padding: 0 2;
    }

    #status-dot {
        width: 3;
        color: $error;
        padding: 1 0;
    }

    #status-dot.connected {
        color: $success;
    }

    #session-info {
        width: auto;
        padding: 1 2;
        color: $foreground;
    }

    #container-info {
        width: auto;
        padding: 1 2;
        color: $foreground;
    }

    #file-chips {
        padding: 1 1;
        width: 1fr;
        height: auto;
    }

    .file-chip {
        background: $panel;
        color: $foreground;
        padding: 0 1;
        margin: 0 1;
        width: auto;
        text-opacity: 80%;
    }

    /* -- chat area ------------------------------------------------------- */

    #chat-log {
        border: none;
        padding: 1 2;
        scrollbar-color: $panel;
        scrollbar-color-hover: $primary;
        scrollbar-color-active: $accent;
    }

    /* -- input area ------------------------------------------------------ */

    #input-area {
        dock: bottom;
        height: auto;
        padding: 0 2 1 2;
        background: $surface;
        border-top: solid $panel;
    }

    #streaming-indicator {
        height: 1;
        display: none;
        color: $primary;
        padding: 0 1;
        text-opacity: 70%;
    }

    #streaming-indicator.active {
        display: block;
    }

    #prompt-input {
        border: round $panel;
        padding: 0 1;
        background: $background;
    }

    #prompt-input:focus {
        border: round $primary;
    }

    /* -- footer ---------------------------------------------------------- */

    Footer {
        background: $surface;
    }
    """

    TITLE = "Claude Harness"
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+d", "delete_session", "Delete Session"),
        Binding("ctrl+f", "list_files", "Files"),
        Binding("ctrl+q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.session_id: str | None = None
        self.container_id: str | None = None
        self.http = httpx.Client(base_url=BASE_URL, timeout=10)
        self._streaming = False

    def compose(self) -> ComposeResult:
        yield TopBar(id="top-bar")
        yield ChatLog(id="chat-log", highlight=True, markup=True, auto_scroll=True)
        with Vertical(id="input-area"):
            yield StreamingIndicator()
            yield Input(placeholder="Send a message\u2026", id="prompt-input")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(HARNESS_THEME)
        self.theme = "harness-dark"
        self.query_one("#prompt-input", Input).focus()

        welcome = Panel(
            Text.from_markup(
                "[bold]Welcome to Claude Harness[/bold]\n\n"
                "Type a message to start chatting.\n"
                "A sandbox session is created automatically.\n\n"
                "[dim]ctrl+n[/dim] new session   "
                "[dim]ctrl+d[/dim] delete   "
                "[dim]ctrl+f[/dim] files   "
                "[dim]ctrl+q[/dim] quit"
            ),
            border_style=CLR_PRIMARY,
            padding=(1, 2),
            expand=True,
        )
        self.query_one("#chat-log", ChatLog).write(welcome)
        self._check_server()

    # -- Logging helpers ----------------------------------------------------

    def _log_system(self, msg: str) -> None:
        text = Text.from_markup(f"  {msg}")
        text.stylize("dim")
        self.query_one("#chat-log", ChatLog).write(text)

    def _log_user(self, msg: str) -> None:
        panel = Panel(
            Text(msg),
            border_style="cyan",
            title="[bold cyan]You[/bold cyan]",
            title_align="left",
            padding=(0, 1),
            expand=True,
        )
        self.query_one("#chat-log", ChatLog).write(panel)

    def _log_assistant(self, msg: str) -> None:
        self.query_one("#chat-log", ChatLog).write(Markdown(msg))

    def _log_tool_call(self, name: str, args_str: str) -> None:
        if args_str.strip():
            content = Syntax(args_str, "json", theme="monokai", word_wrap=True)
        else:
            content = Text("(no arguments)", style="dim")
        panel = Panel(
            content,
            border_style=CLR_AMBER,
            title=f"[bold {CLR_AMBER}]{name}[/bold {CLR_AMBER}]",
            title_align="left",
            subtitle="[dim]tool call[/dim]",
            subtitle_align="right",
            padding=(0, 1),
            expand=True,
        )
        self.query_one("#chat-log", ChatLog).write(panel)

    def _log_tool_result(self, content: str, is_error: bool = False) -> None:
        border_color = CLR_RED if is_error else CLR_GREEN
        icon = "\u2717" if is_error else "\u2713"
        label = "error" if is_error else "result"

        renderable = self._make_result_renderable(content)
        panel = Panel(
            renderable,
            border_style=border_color,
            title=f"[bold {border_color}]{icon} {label}[/bold {border_color}]",
            title_align="left",
            padding=(0, 1),
            expand=True,
        )
        self.query_one("#chat-log", ChatLog).write(panel)

    def _log_permission(self, tool: str, req_id: str) -> None:
        msg = Text.from_markup(
            f"[bold]Tool:[/bold] {tool}\n"
            f"[bold]Request ID:[/bold] {req_id}\n\n"
            f"[dim]Approve from another terminal:[/dim]\n"
            f"uv run python cli.py approve {self.session_id} {req_id}"
        )
        panel = Panel(
            msg,
            border_style=CLR_RED,
            title=f"[bold {CLR_RED}]Permission Required[/bold {CLR_RED}]",
            title_align="left",
            padding=(0, 1),
            expand=True,
        )
        self.query_one("#chat-log", ChatLog).write(panel)

    def _log_usage(self, inp: int, out: int) -> None:
        text = Text(f"  tokens: {inp:,} in / {out:,} out", style="dim italic")
        self.query_one("#chat-log", ChatLog).write(text)

    @staticmethod
    def _make_result_renderable(content: str):
        """Return a Rich renderable, using Syntax highlighting when appropriate."""
        stripped = content.strip()
        if not stripped:
            return Text("(empty)", style="dim")
        # bash_execute output (stdout:/stderr:)
        if stripped.startswith("stdout:") or stripped.startswith("stderr:"):
            return Syntax(stripped, "bash", theme="monokai", word_wrap=True)
        # Python code
        if any(kw in stripped for kw in ("def ", "class ", "import ", "from ")):
            return Syntax(stripped, "python", theme="monokai", word_wrap=True)
        # JSON
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
                return Syntax(stripped, "json", theme="monokai", word_wrap=True)
            except json.JSONDecodeError:
                pass
        return Text(stripped)

    # -- Top bar state ------------------------------------------------------

    def _update_session_display(self) -> None:
        dot = self.query_one("#status-dot", Static)
        info = self.query_one("#session-info", Static)
        ctr = self.query_one("#container-info", Static)
        if self.session_id:
            dot.update("\u25cf")
            dot.add_class("connected")
            info.update(f"[bold]Session:[/bold] {self.session_id[:8]}")
            ctr.update(f"[bold]Container:[/bold] {(self.container_id or '?')[:8]}")
        else:
            dot.update("\u25cf")
            dot.remove_class("connected")
            info.update("[dim]No session[/dim]")
            ctr.update("")

    def _render_file_chips(self, files: list[str]) -> None:
        chips = self.query_one("#file-chips", Horizontal)
        chips.remove_children()
        for f in files[:10]:
            name = f.split("/")[-1] if "/" in f else f
            chips.mount(Label(f" {name} ", classes="file-chip"))

    # -- Server check -------------------------------------------------------

    @work(thread=True)
    def _check_server(self) -> None:
        try:
            self.http.get("/docs")
            self.call_from_thread(self._log_system, "Server connected.")
        except httpx.ConnectError:
            self.call_from_thread(
                self._log_system,
                f"[red]Cannot reach server at {BASE_URL}[/red]\n"
                "  [dim]Start it with: uv run uvicorn src.api:app --reload --port 8001[/dim]",
            )
        except Exception:
            pass

    # -- Session management -------------------------------------------------

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
                f"Session created: {self.session_id[:12]}...",
            )
            return True
        except httpx.ConnectError:
            self.call_from_thread(
                self._log_system,
                "[red]Cannot reach server. Is it running?[/red]",
            )
            return False
        except httpx.HTTPStatusError as e:
            self.call_from_thread(
                self._log_system,
                f"[red]Session creation failed (HTTP {e.response.status_code})[/red]",
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
            self.call_from_thread(self._render_file_chips, files)
        except Exception:
            pass

    def action_quit_app(self) -> None:
        if self.session_id:
            try:
                self.http.delete(f"/sessions/{self.session_id}")
            except Exception:
                pass
        self.exit()

    # -- Streaming state ----------------------------------------------------

    def _set_streaming(self, active: bool) -> None:
        indicator = self.query_one("#streaming-indicator", StreamingIndicator)
        prompt = self.query_one("#prompt-input", Input)
        if active:
            indicator.add_class("active")
            prompt.disabled = True
        else:
            indicator.remove_class("active")
            prompt.disabled = False
            prompt.focus()

    # -- Chat ---------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.value = ""

        if self._streaming:
            self._log_system("Wait for the current response to finish\u2026")
            return

        self._log_user(msg)
        self._send_message(msg)

    @work(thread=True)
    def _send_message(self, message: str) -> None:
        if not self.session_id:
            if not self._create_session_sync():
                return

        self._streaming = True
        self.call_from_thread(self._set_streaming, True)

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

                    if assistant_text:
                        full = "".join(assistant_text)
                        self.call_from_thread(self._log_assistant, full)

        except httpx.ConnectError:
            self.call_from_thread(self._log_system, "[red]Lost connection to server.[/red]")
        except httpx.HTTPStatusError as e:
            self.call_from_thread(
                self._log_system,
                f"[red]HTTP {e.response.status_code}: {e.response.text[:200]}[/red]",
            )
        except Exception as e:
            self.call_from_thread(self._log_system, f"[red]Error: {e}[/red]")
        finally:
            self._streaming = False
            self.call_from_thread(self._set_streaming, False)
            self.call_from_thread(self._fetch_files)

    def _handle_sse(self, event_type: str, data: dict, text_parts: list[str]) -> None:
        if event_type == "text_delta":
            text_parts.append(data.get("text", ""))

        elif event_type == "tool_call":
            name = data.get("name", "?")
            args = data.get("args", {})
            args_str = json.dumps(args, indent=2) if args else ""
            if len(args_str) > 500:
                args_str = args_str[:500] + "\n..."
            self.call_from_thread(self._log_tool_call, name, args_str)

        elif event_type == "tool_result":
            content = data.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            content = str(content)
            if len(content) > 1000:
                content = content[:1000] + "\n..."
            is_error = data.get("is_error", False)
            self.call_from_thread(self._log_tool_result, content, is_error)

        elif event_type == "permission_request":
            tool = data.get("tool", "?")
            req_id = data.get("request_id", "?")
            self.call_from_thread(self._log_permission, tool, req_id)

        elif event_type == "usage":
            inp = data.get("input_tokens", 0)
            out = data.get("output_tokens", 0)
            self.call_from_thread(self._log_usage, inp, out)

        elif event_type == "compaction":
            self.call_from_thread(self._log_system, "Context compacted")


if __name__ == "__main__":
    app = HarnessTUI()
    app.run()
