#!/usr/bin/env bash
# Re-applies the DS4-clone / L2CAP "pending" patch and rebuilds the bluetooth
# kernel module for the CURRENTLY RUNNING kernel. Run after installing a new
# Fedora kernel (dnf kernel update replaces kernel/bluetooth modules).
# Usage: ./build-bluetooth-module.sh   (will sudo for build-behind + install)
set -eu
KVER=$(uname -r)

echo "Target kernel: $KVER"
echo "Enabling source repo, installing builddeps + kernel-devel"
sudo dnf install -y rpm-build rpmdevtools "kernel-devel-${KVER}"
sudo dnf builddep -y "kernel-${KVER}" 2>/dev/null || \
    sudo dnf builddep -y reproduce || true

# Fetch matching kernel source rpm for this exact kernel version
SRC_RPM="kernel-${KVER}.src.rpm"
sudo rm -f ~/rpmbuild/SOURCES/kernel-*.src.rpm "kernel-${KVER}.src.rpm"
dnf download --source "kernel-${KVER%.fc*}" 2>/dev/null || \
    dnf download --source kernel
mv kernel-*src.rpm /tmp/ 2>/dev/null || true
rpm -ivh --replacepkgs /tmp/kernel-${KVER}.src.rpm 2>/dev/null || \
    rpm -ivh /tmp/kernel-*.src.rpm

echo "Preparing kernel build tree (rpmbuild -bp)..."
cd ~/rpmbuild
rpmbuild -bp SPECS/kernel.spec > /tmp/kprep.log 2>&1

KTREE=$(find ~/rpmbuild/BUILD -maxdepth 3 -path "*linux-${KVER}/" | head -1)
[ -d "$KTREE" ] || { echo "Could not locate build tree"; exit 1; }
echo "Kernel tree: $KTREE"

cp /boot/config-${KVER} "$KTREE"/.config
# fix EXTRAVERSION: the tree's Makefile has an empty EXTRAVERSION by default
# which yields a UTS_RELEASE of "7.2.13-200" instead of "7.2.13-200.fc44.x86_64"
sed -i "s|^EXTRAVERSION =.*|EXTRAVERSION = -${KVER#*-}|n" "$KTREE"/Makefile
rm -f "$KTREE/include/generated/utsrelease.h"

echo "Applying the DS4-clone l2cap patch..."
( cd "$KTREE" && patch -p1 --dry-run >/dev/null 2>&1 || patch -p1 ) \
    < "$(dirname "$0")/ds4-clone-l2cap-pending.patch" || {
        echo "Patch already applied (recovering file)..."
    }

echo "modules_prepare (long, ~10 minutes)..."
make -C "$KTREE" prepare0 > /tmp/mprep.log 2>&1
make -C "$KTREE" olddefconfig > /dev/null 2>&1
make -C "$KTREE" prepare0 > /dev/null 2>&1
make -C "$KTREE" M="$KTREE/net/bluetooth" modules > /tmp/btmod.log 2>&1

echo "Stripping and compressing..."
strip --strip-debug "$KTREE/net/bluetooth/bluetooth.ko"
rm -f "$KTREE/net/bluetooth/bluetooth.ko.xz"
xz -9e --check=crc32 --lzma2=dict=2MiB -k "$KTREE/net/bluetooth/bluetooth.ko"
xz -t "$KTREE/net/bluetooth/bluetooth.ko.xz"

echo "Verifying vermagic..."
strings "$KTREE/net/bluetooth/bluetooth.ko" | grep "SMP preempt"

echo "Installing..."
ORIG="/lib/modules/${KVER}/kernel/net/bluetooth/bluetooth.ko.xz.orig"
[ -f "$ORIG" ] || sudo cp "/lib/modules/${KVER}/kernel/net/bluetooth/bluetooth.ko.xz" "$ORIG"
sudo install -m644 "$KTREE/net/bluetooth/bluetooth.ko.xz" \
    "/lib/modules/${KVER}/kernel/net/bluetooth/bluetooth.ko.xz"
sudo depmod -a "$KVER"

echo "Done. Reboot or: sudo modprobe -r bluetooth && sudo modprobe bluetooth"
