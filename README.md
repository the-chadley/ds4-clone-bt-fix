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
| `ds4-ctl/ds4-tester.py` | Steam-style GUI tester: live button/stick/trigger/touchpad view, lightbar colours, rumble test, per-MAC pad names. Needs `python3-evdev` and `python3-cairo`. |
| `ds4-ctl/ds4-fullmode` | Switches clone pads from the minimal Bluetooth report to the full one, which turns the touchpad (and motion) on. See below. |
| `ds4-ctl/70-ds4-tester.rules` | udev rules: runs `ds4-fullmode` on every Bluetooth connect, and gives the desktop user access to the touchpad and lightbar LEDs. |
| `ds4-ctl/install-user.sh` | Per-user install: application menu entry + icon, and the lightbar restore service. |
| `ds4-ctl/ds4-lightbar-restore`, `ds4-ctl/ds4-lightbar@.service` | Put each pad's last chosen lightbar colour back when it reconnects (user service started by udev). |
| `ds4-ctl/ds4led` | Command-line lightbar colour tool. |

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

Everything, in order, on a fresh install. Steps 1–4 fix reconnecting, 5 turns
the touchpad on, and 6–7 are the tester. Run from a clone of this repo:

```bash
git clone https://github.com/the-chadley/ds4-clone-bt-fix.git
cd ds4-clone-bt-fix

# 1. bluez daemon: exclude the policy plugin (creates the outbound race)
sudo mkdir -p /etc/systemd/system/bluetooth.service.d
sudo tee /etc/systemd/system/bluetooth.service.d/override.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=policy
EOF
sudo systemctl daemon-reload

# 2. Lower the HID server security bar
sudo sed -i 's/^#ClassicBondedOnly.*/ClassicBondedOnly=false/' /etc/bluetooth/input.conf
sudo systemctl restart bluetooth

# 3. Patched bluetooth.ko (kernel side). Re-run after every kernel update.
sudo dnf install -y rpm-build rpmdevtools
sudo dnf builddep -y kernel  # pulls kernel build deps when a source repo is enabled
./build-bluetooth-module.sh

# 4. Pair/power-cycle the pad (hold PS ~10 s to fully power off, then PS)

# 5. Touchpad + lightbar: full-report switch on connect, user access via udev
sudo install -m 0755 ds4-ctl/ds4-fullmode /usr/local/bin/ds4-fullmode
sudo cp ds4-ctl/70-ds4-tester.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --action=change --subsystem-match=input --subsystem-match=leds
sudo ds4-fullmode

# 6. Tester dependencies
sudo dnf install -y python3-evdev python3-cairo python3-tkinter

# 7. Tester in the System menu + lightbar colour restore (per user, no sudo)
ds4-ctl/install-user.sh
```

On a reinstall, `~/.config/ds4-tester/pads.json` holds your pad names and
colours. Copy it across if you want to keep them.

## Touchpad dead on clones (minimal report mode)

Once they reconnect reliably, the clones have a second quirk. Over Bluetooth
they keep sending the **minimal 10-byte input report `0x01`**: sticks, buttons
and triggers, but **no touchpad and no motion data**. The touchpad stops
moving the desktop pointer, games don't see it, and even clicking it does
nothing.

A genuine DS4 switches to the full `0x11` report when the host reads a
calibration feature report. The kernel's `hid-playstation` reads feature
report `0x05` at probe time, and **clones ignore that**. They do switch when
**feature report `0x02`** is read. Steam does that read, which is why the
touchpad sometimes works while Steam is running and sometimes doesn't.

You can check which mode a pad is in by reading a few reports from its hidraw
node: first byte `0x01` with 10-byte reports means minimal mode, `0x11` with
78-byte reports means full mode.

The fix is to read feature `0x02` on every connect:

```bash
cd ds4-ctl
sudo install -m 0755 ds4-fullmode /usr/local/bin/ds4-fullmode
sudo cp 70-ds4-tester.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --action=change --subsystem-match=input --subsystem-match=leds
sudo ds4-fullmode          # pads that are already connected
```

The same rules file lets the logged-in user read the touchpad and set the
lightbar without root, so `ds4-tester.py` runs fully without `sudo`. (The
tester also sends the `0x02` read itself when it opens a pad.)

## Tester

```bash
python3 ds4-ctl/ds4-tester.py          # GUI
python3 ds4-ctl/ds4-tester.py --list   # just list connected pads
```

- Every control lights up on the drawing as you press it, and the panels
  below show raw trigger, stick and touchpad values.
- Click the pad's name to rename it. The name and the last lightbar colour
  you picked are remembered per MAC address in `~/.config/ds4-tester/pads.json`.
- **Rumble** runs the strong (left) motor, then the weak (right) motor.
- **Identify** flashes the lightbar so you can tell which pad is which.

To add **DS4 Tester** to your application menu (System category) and get
your saved lightbar colour back automatically on every reconnect, run the
per-user installer. It needs no root:

```bash
ds4-ctl/install-user.sh              # ./install-user.sh --uninstall to remove
```

It installs the menu entry and icon, plus a small user service
(`ds4-lightbar@.service`). The udev rule above starts that service each time
a pad connects, and it reapplies the colour you last picked, since the kernel
resets the lightbar to a default colour on connect.

## Status / KNOWN trade-offs

- Dropped `policy` plugin: audio *devices that self-reconnect still work*;
  only host-initiated re-dial of dropped audio streams is disabled (i.e. if
  a headset *expects the PC to call it back* after range loss / suspend,
  you'll need a manual connect). Rare; matters mostly on laptops with BT
  headsets after suspend. Manual `bluetoothctl connect` / desktop applet
  still work.
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
