#!/usr/bin/env bash
# Build an Alpine aarch64 rootfs for Firecracker microVMs.
# Produces: rootfs.ext4 (~250MB) with dev tools + guest-agent binary.
#
# Prerequisites:
#   - Docker (for cross-arch chroot via alpine image)
#   - guest-agent binary at ../guest-agent (or pass GUEST_AGENT_BIN=)
#
# Usage:
#   ./build-rootfs.sh              # build rootfs.ext4 in current dir
#   ROOTFS_SIZE=512 ./build-rootfs.sh  # custom size in MB

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ROOTFS_SIZE="${ROOTFS_SIZE:-256}"  # MB
ROOTFS_FILE="${ROOTFS_FILE:-$SCRIPT_DIR/rootfs.ext4}"
GUEST_AGENT_BIN="${GUEST_AGENT_BIN:-$REPO_ROOT/guest-agent/target/aarch64-unknown-linux-musl/release/guest-agent}"
ALPINE_VERSION="${ALPINE_VERSION:-3.19}"
MOUNT_DIR=$(mktemp -d)

cleanup() {
    echo "Cleaning up..."
    # Unmount if still mounted
    if mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
        sudo umount "$MOUNT_DIR"
    fi
    rm -rf "$MOUNT_DIR"
}
trap cleanup EXIT

# --- Validate guest-agent binary ---

if [ ! -f "$GUEST_AGENT_BIN" ]; then
    echo "Guest agent binary not found at: $GUEST_AGENT_BIN"
    echo "Build it first: cd $REPO_ROOT/guest-agent && cargo build --release --target aarch64-unknown-linux-musl"
    exit 1
fi

# Verify it's an aarch64 ELF
if ! file "$GUEST_AGENT_BIN" | grep -q "aarch64"; then
    echo "Warning: guest-agent binary does not appear to be aarch64"
    echo "Got: $(file "$GUEST_AGENT_BIN")"
    echo "Expected: ELF 64-bit LSB executable, ARM aarch64, statically linked"
    exit 1
fi

echo "==> Creating ${ROOTFS_SIZE}MB ext4 image at $ROOTFS_FILE"

dd if=/dev/zero of="$ROOTFS_FILE" bs=1M count="$ROOTFS_SIZE" status=progress
mkfs.ext4 -F -L rootfs "$ROOTFS_FILE"
sudo mount -o loop "$ROOTFS_FILE" "$MOUNT_DIR"

# --- Bootstrap Alpine rootfs via Docker ---

echo "==> Bootstrapping Alpine $ALPINE_VERSION aarch64 rootfs"

CONTAINER_ID=$(docker create --platform linux/aarch64 "alpine:$ALPINE_VERSION" /bin/true)
docker export "$CONTAINER_ID" | sudo tar -xf - -C "$MOUNT_DIR"
docker rm "$CONTAINER_ID" >/dev/null

# --- Install packages inside the rootfs ---

echo "==> Installing packages"

# Set up DNS for apk
sudo mkdir -p "$MOUNT_DIR/etc"
echo "nameserver 8.8.8.8" | sudo tee "$MOUNT_DIR/etc/resolv.conf" >/dev/null

# Install packages via chroot (requires qemu-aarch64 binfmt on x86 hosts; native on arm64)
sudo chroot "$MOUNT_DIR" /bin/sh -c '
    apk update
    apk add --no-cache \
        bash \
        coreutils \
        curl \
        git \
        grep \
        jq \
        nodejs \
        npm \
        openssh-client \
        python3 \
        ripgrep \
        sed \
        tar \
        util-linux
'

# --- Set up workspace ---

echo "==> Setting up workspace and init"

sudo mkdir -p "$MOUNT_DIR/workspace/uploads"
sudo chroot "$MOUNT_DIR" chmod 777 /workspace /workspace/uploads

# --- Install guest-agent ---

echo "==> Installing guest-agent binary"

sudo cp "$GUEST_AGENT_BIN" "$MOUNT_DIR/usr/local/bin/guest-agent"
sudo chmod 755 "$MOUNT_DIR/usr/local/bin/guest-agent"

# --- Create init script ---
# Firecracker boots with init= pointing to this script.
# It mounts essential filesystems and starts the guest agent.

sudo tee "$MOUNT_DIR/init.sh" >/dev/null <<'INITEOF'
#!/bin/sh
# Minimal init for Firecracker microVM

# Mount essential filesystems
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

# Set hostname
hostname sandbox

# Start guest agent (vsock listener on port 5000)
exec /usr/local/bin/guest-agent
INITEOF

sudo chmod 755 "$MOUNT_DIR/init.sh"

# --- Finalize ---

echo "==> Finalizing rootfs"

sudo umount "$MOUNT_DIR"

echo "==> Done: $ROOTFS_FILE ($(du -h "$ROOTFS_FILE" | cut -f1))"
echo "   Use with Firecracker: --rootfs $ROOTFS_FILE --init /init.sh"
