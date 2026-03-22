# Handoff — Firecracker Migration v3

## What's Done (Wave 1 — committed, tests green)

Backend abstraction layer is complete. All existing code refactored behind a `SandboxBackend` Protocol.

### Files changed:
- **`src/backend.py`** (new) — `SandboxBackend` Protocol: 7 methods (`create`, `exec`, `copy_to`, `copy_from`, `list_files`, `destroy`, `is_running`) + `recyclable` property
- **`src/sandbox.py`** — Renamed `SandboxManager` → `DockerBackend`, added `is_running()` + `recyclable -> True`, kept `SandboxManager = DockerBackend` alias
- **`src/pool.py`** — Uses `SandboxBackend` type, fixed Docker SDK leak (`sandbox.client.containers.get()` → `sandbox.is_running()`), `release()` checks `recyclable` (True = clean+recycle, False = destroy+fresh)
- **`src/tools.py`** — Type annotations `SandboxManager` → `SandboxBackend`
- **`src/agent.py`** — Type annotations `SandboxManager` → `SandboxBackend`
- **`src/api.py`** — Added `create_backend()` factory that reads `settings.sandbox_backend` ("docker" or "firecracker")
- **`src/config.py`** — Added `sandbox_backend: str = "docker"`

### Test status:
- 15/15 unit tests pass (`uv run pytest tests/ -v -m "not integration"`)
- Integration tests also pass (they import `SandboxManager` which is aliased to `DockerBackend`)
- NOT YET COMMITTED — needs `git add` + `git commit`

## What's Next

### Wave 2: Rust Guest Agent
- Create `guest-agent/Cargo.toml` + `guest-agent/src/main.rs`
- Static binary for `aarch64-unknown-linux-musl` (~1-2MB)
- Listens on vsock CID `VMADDR_CID_ANY`, port 5000
- Length-prefixed JSON protocol (4-byte big-endian + JSON)
- Methods: `exec`, `read_file`, `write_file`, `list_files`, `ping`

### Wave 3: Rootfs + Snapshot Tooling
- `firecracker/rootfs/build-rootfs.sh` — Alpine aarch64 rootfs with ripgrep, jq, curl, git, nodejs, npm
- `firecracker/snapshot/bake-snapshot.py` — Boot VM, wait for agent ping, snapshot
- `firecracker/rootfs/Makefile` — Build targets
- `firecracker/kernel/.gitkeep` — vmlinux placeholder

### Wave 4: FirecrackerBackend + Pool Update
- `src/firecracker.py` — `VsockClient` + `FirecrackerBackend` implementing `SandboxBackend`
- Update pool release for non-recyclable backends (already done in Wave 1)

### Wave 5: Lima Setup + E2E
- `firecracker/lima.yaml` — Ubuntu aarch64, nested virt, socket forwarding
- Switch default to `sandbox_backend: str = "firecracker"`
- E2E tests

## Key Decisions
- **aarch64 native** (user has M3 Pro, no emulation needed)
- **vsock + Rust guest agent** for exec inside microVMs (not serial console)
- **Lima with `nestedVirtualization: true`** for macOS dev
- **Docker stays as legacy fallback** via `SANDBOX_BACKEND=docker`
- **Non-recyclable backends**: pool destroys on release instead of cleaning+recycling (snapshot restore is ~5ms, cheaper than cleanup)

## Plan File
Full plan at: `.claude/plans/memoized-gathering-tarjan.md`

## Spec File
Spec at: `.planning/spec-v3.md`
