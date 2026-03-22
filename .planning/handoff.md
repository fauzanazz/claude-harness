# Handoff — Firecracker Migration v3

## What's Done

### Wave 1: Backend Abstraction (committed: `cce282b`)
- `src/backend.py` — `SandboxBackend` Protocol: 7 methods + `recyclable` property
- `src/sandbox.py` — `SandboxManager` renamed to `DockerBackend`, alias kept for compat
- `src/pool.py` — Uses `SandboxBackend` type, `release()` checks `recyclable` (recycle vs destroy)
- `src/tools.py`, `src/agent.py` — Type annotations updated to `SandboxBackend`
- `src/api.py` — `create_backend()` factory reads `settings.sandbox_backend`
- `src/config.py` — Added `sandbox_backend: str = "docker"`

### Wave 2: Rust Guest Agent (committed: `9d70b1c`, `f9849a5`)
- `guest-agent/` — Full agent with vsock/TCP, length-prefixed JSON protocol, 5 methods, 17 tests

### Wave 3: Rootfs + Snapshot Tooling (uncommitted)
- `firecracker/rootfs/build-rootfs.sh` — Alpine aarch64 rootfs builder (Docker-based bootstrap, same tools as Docker image + guest-agent binary)
- `firecracker/snapshot/bake-snapshot.py` — Boot VM, wait for agent ping, create full snapshot via Firecracker REST API
- `firecracker/Makefile` — Build targets: `guest-agent`, `rootfs`, `snapshot`, `clean`
- `firecracker/kernel/.gitkeep` — Placeholder for vmlinux
- `firecracker/.gitignore` — Ignores rootfs.ext4, vmlinux, snapshot/base/

### Wave 4: FirecrackerBackend + VsockClient (uncommitted)
- `src/firecracker.py` — Full implementation:
  - `VsockClient`: async client speaking length-prefixed JSON protocol over Unix domain socket (Firecracker vsock UDS)
  - `FirecrackerBackend`: implements `SandboxBackend` Protocol
    - `create()`: starts Firecracker process, restores from snapshot (drives + vsock pre-config, snapshot load, resume)
    - All tool methods delegate to VsockClient (exec, read_file, write_file, list_files)
    - `destroy()`: terminates Firecracker process, cleans temp dir
    - `recyclable = False` (snapshot restore is cheaper than cleanup)
  - Sync/async bridge via `_run_async()` for nested event loop handling
- `src/config.py` — Added: `firecracker_bin`, `firecracker_kernel_path`, `firecracker_rootfs_path`, `firecracker_snapshot_path`
- `pyproject.toml` — Added `httpx` to runtime dependencies
- `tests/test_firecracker.py` — 13 unit tests (mock vsock server for protocol tests + backend state tests)

### Wave 5: Lima + Wiring (uncommitted)
- `firecracker/lima.yaml` — Ubuntu 24.04 aarch64, nested virt, Firecracker auto-installed, project mounted at /harness
- `.env.example` — Updated with Firecracker config vars
- Backend wiring already done in Wave 1 (`api.py` factory)

### Test status:
- 17/17 Rust tests pass (`cargo test`)
- 28/28 Python unit tests pass (`uv run pytest tests/ -v -m "not integration"`)

## What's Next

### E2E Testing (requires Lima VM + hardware)
- Set up Lima VM: `limactl create --name fc firecracker/lima.yaml && limactl start fc`
- Inside Lima: download aarch64 kernel, build rootfs (`make rootfs`), bake snapshot (`make snapshot`)
- Run Firecracker integration tests with `SANDBOX_BACKEND=firecracker`
- Test full request flow: API → Session → Agent → Firecracker VM → guest-agent

### Production Polish
- Reflink (`cp --reflink=auto`) for rootfs CoW copies on supported filesystems
- Configurable guest CID allocation (currently hardcoded to 3)
- Firecracker process lifecycle monitoring / health checks
- Switch default: `sandbox_backend: str = "firecracker"`

## Key Decisions
- **aarch64 native** (user has M3 Pro, no emulation needed)
- **vsock + Rust guest agent** for exec inside microVMs (not serial console)
- **Lima with `nestedVirtualization: true`** for macOS dev
- **Docker stays as legacy fallback** via `SANDBOX_BACKEND=docker`
- **Non-recyclable backends**: pool destroys on release instead of cleaning+recycling
- **Guest agent TCP fallback**: on non-Linux (macOS dev), agent uses TCP:5000 instead of vsock
- **httpx for Firecracker API**: sync client for snapshot restore, async UDS for vsock

## Spec File
Spec at: `.planning/spec-v3.md`
