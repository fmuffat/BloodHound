# Tooling to build the OVA on any Linux host that has Docker but neither
# libguestfs nor root access (used by scripts/build-ova-local.sh).
# Run privileged: libguestfs starts a small VM (KVM when /dev/kvm is passed in,
# software emulation otherwise).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LIBGUESTFS_BACKEND=direct
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      libguestfs-tools qemu-utils qemu-system-x86 linux-image-virtual \
      docker.io ca-certificates curl python3 \
 && rm -rf /var/lib/apt/lists/* \
 && chmod 0644 /boot/vmlinuz-*   # libguestfs reads the kernel
