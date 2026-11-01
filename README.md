# ds4-clone-bt-fix

Fix for PS4 clone "Wireless Controller" pads that **pair fine but drop a few
seconds after every Bluetooth reconnect** on Linux (verified on Fedora with
bluez 5.87+git and kernel 7.2.x).

## What's here

| File | What it is |
|---|---|
| `ds4-clone-l2cap-pending.patch` | One-line-hunk patch to `net/bluetooth/l2cap_core.c`: don't hard-`SEC_BLOCK` inbound L2CAP channels when auth/encryption is merely *in flight* — let the existing BT_CONNECT2 / `l2cap_conn_start()` pending flow answer `Pending`, then auto-complete with `Success` once the link is encrypted. |
| `build-bluetooth-module.sh` | End-to-end script: fetches the kernel source RPM matching your running kernel, applies the patch, rebuilds only `bluetooth.ko`, installs it. Run after every kernel update. |
| `DS4-clone-BT-fix-notes.md` | Full root-cause analysis and the complete change/rollback inventory. |

## The short diagnosis

Three stacked issues make clone DS4 pads fail reconnect:

1. **bluez `policy` plugin** auto-connects the HID profile ~2 s after the ACL
   comes up — colliding with the pad's own inbound channels (bluez bug
   [#1765](https://github.com/bluez/bluez/issues/1765)).
2. **`ClassicBondedOnly=true`** (upstream default since 2023) forces the HID
   server sockets to security level MEDIUM → inbound L2CAP requests that race
   ahead of the link-key exchange get refused with
   `Connection refused – security block`.
3. **The kernel then hard-`SEC_BLOCK`s** the clone's channel request because
   the ACL isn't encrypted *yet*, and — uniquely for clone firmware — the pad
   **gives up instead of retrying**, so the connection dies a few seconds
   later.

Genuine DS4/DualSense pads retry the refused channel, which is why they
mostly survive. Clone firmware doesn't. Fix (1) and (2) via config; (3) needs
the kernel patch.

## TL;DR install (Fedora)

```bash
# 1. bluez daemon: exclude the policy plugin (creates the outbound race)
sudo tee /etc/systemd/system/bluetooth.service.d/override.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=policy
EOF
sudo systemctl daemon-reload

# 2. Lower the HID server security bar
sudo sed -i 's/^#ClassicBondedOnly.*/ClassicBondedOnly=false/' /etc/bluetooth/input.conf

# 3. Patched bluetooth.ko (kernel side)
sudo dnf install -y rpm-build rpmdevtools
sudo dnf builddep -y kernel  # pulls kernel build deps when a source repo is enabled
./build-bluetooth-module.sh

# 4. Pair/power-cycle the pad (hold PS ~10 s to fully power off, then PS)
```

## Status / KNOWN trade-offs

- Dropped `policy` plugin: profile auto-connect logic for *audio* devices
  (headset reconnect) is also disabled. `bluetoothctl` manual connections
  still work. Fine for a desktop PC, consider before laptops with BT audio.
- `ClassicBondedOnly=false`: connection-level security is relaxed — inbound
  gamepads may not force encryption. This matches pre-2023 upstream behavior.
- The kernel module changes Bluetooth system-wide. Rebuild it after every
  kernel update (`./build-bluetooth-module.sh`).
- If you boot an *older* kernel afterwards, restore your backup copy or
  re-run the script for that kernel.

## Credits

Root cause analysis and fix based on debugging on HP Elitedesk G1, Fedora 44
(kernel 7.2.13-200.fc44, bluez 5.87+1.git). Upstream context:
bluez [#1765](https://github.com/bluez/bluez/issues/1765),
[#614](https://github.com/bluez/bluez/issues/614).
