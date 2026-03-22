# Claude Harness v3 — Firecracker Migration Spec

## What We're Building

Replace the Docker-based sandbox backend with Firecracker microVMs using snapshot restore and copy-on-write (CoW) memory sharing. The goal is 10-100x container density and sub-5ms sandbox creation via `mmap(MAP_PRIVATE)` snapshot restore, rather than cold-booting containers.

## Who Is This For

Internal / own product team. Same audience as today — single deployment, not multi-tenant SaaS.

## Success Criteria

1. **Sub-5ms sandbox start** — Snapshot restore (not boot) gets sandbox creation from seconds to milliseconds
2. **10x+ user density** — Same hardware serves 10x more concurrent sessions via CoW memory sharing. A base snapshot of ~256MB is shared read-only; each session only allocates memory for dirty pages (~10-50MB)
3. **Works on macOS for development** — Local dev uses Lima (lightweight Linux VM with KVM) to host Firecracker. Production uses native Linux + KVM
4. **No API changes** — The HTTP/SSE API is identical. Clients (TUI, future frontends) don't know the backend changed. This is purely a `sandbox.py` + `pool.py` swap
5. **Docker legacy fallback** — Docker code stays as a deprecated fallback, selectable via config. Firecracker is the default

## Architecture Overview

```
macOS dev:   Python app ──► Lima VM (Ubuntu + KVM) ──► Firecracker API socket ──► microVMs
Production:  Python app ──► Firecracker API socket (native Linux + KVM) ──► microVMs
Fallback:    Python app ──► Docker SDK ──► containers (existing code, deprecated)
```

### Snapshot Lifecycle

```
1. BAKE (once):
   Boot base microVM ──► install tools ──► warm runtime ──► snapshot (memory + CPU + disk)

2. SERVE (per session):
   Snapshot ──► mmap(MAP_PRIVATE) ──► KVM restore CPU state ──► running VM (~0.8-5ms)
   All VMs share the same base pages (read-only). Only dirty pages cost real memory.

3. TEARDOWN:
   Kill VM ──► dirty pages freed instantly ──► no cleanup needed
```

### Backend Abstraction

```python
SANDBOX_BACKEND=firecracker  # default
SANDBOX_BACKEND=docker       # legacy fallback
```

Both backends implement the same interface (`create`, `exec`, `copy_to`, `copy_from`, `destroy`). The pool and agent layers don't know which backend is active.

## Out of Scope

- API/SSE changes (no new endpoints, no new event types)
- Frontend/UI changes
- Multi-tenant / authentication
- Persistent storage beyond VM lifecycle
- Networking inside microVMs (keep `network_mode=none` equivalent)
- KVM-PVM or custom kernel patches
- Production deployment automation (Terraform, Ansible, etc.)

## Constraints

- **Runtime**: Python 3.11+, `uv` for package management
- **Firecracker API**: HTTP over Unix socket (REST API, no SDK needed — just `httpx` or `aiohttp`)
- **macOS dev**: Lima (`brew install lima`) to run Ubuntu VM with KVM. Firecracker runs inside Lima. Python connects to Firecracker socket via Lima's socket forwarding
- **Production**: Bare-metal or VM with nested virt (Linux + `/dev/kvm`)
- **Snapshot format**: Firecracker's native snapshot (memory file + snapshot file + disk image)
- **Base image**: Build a rootfs (ext4) with the same tools as the current `claude-harness-sandbox` Docker image
- **Config**: `SANDBOX_BACKEND`, `FIRECRACKER_SOCKET_PATH`, `FIRECRACKER_SNAPSHOT_PATH` added to `config.py`
- **Keep it minimal**: Replace/refactor existing files, don't bloat the codebase
