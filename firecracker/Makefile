REPO_ROOT := $(shell cd .. && pwd)
GUEST_AGENT_DIR := $(REPO_ROOT)/guest-agent
GUEST_AGENT_TARGET := aarch64-unknown-linux-musl
GUEST_AGENT_BIN := $(GUEST_AGENT_DIR)/target/$(GUEST_AGENT_TARGET)/release/guest-agent

ROOTFS := rootfs/rootfs.ext4
SNAPSHOT_DIR := snapshot/base

.PHONY: all guest-agent rootfs snapshot clean help

all: snapshot

help:
	@echo "Targets:"
	@echo "  guest-agent  — Cross-compile Rust guest agent for aarch64-musl"
	@echo "  rootfs       — Build Alpine aarch64 rootfs with tools + agent"
	@echo "  snapshot     — Bake Firecracker base snapshot (boot + ping + snapshot)"
	@echo "  clean        — Remove build artifacts"

# --- Guest Agent ---

guest-agent: $(GUEST_AGENT_BIN)

$(GUEST_AGENT_BIN):
	cd $(GUEST_AGENT_DIR) && cargo build --release --target $(GUEST_AGENT_TARGET)

# --- Rootfs ---

rootfs: $(ROOTFS)

$(ROOTFS): $(GUEST_AGENT_BIN)
	GUEST_AGENT_BIN=$(GUEST_AGENT_BIN) bash rootfs/build-rootfs.sh

# --- Snapshot ---

snapshot: $(SNAPSHOT_DIR)/vmstate.bin

$(SNAPSHOT_DIR)/vmstate.bin: $(ROOTFS) kernel/vmlinux
	python3 snapshot/bake-snapshot.py

# --- Clean ---

clean:
	rm -f $(ROOTFS)
	rm -rf $(SNAPSHOT_DIR)
	cd $(GUEST_AGENT_DIR) && cargo clean
