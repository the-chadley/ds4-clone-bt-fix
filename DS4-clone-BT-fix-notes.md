# DS4 Clone Bluetooth Fix — Notes (Nov 1, 2026)

End state (working): both PS4-clone controllers pair AND reconnect over
Bluetooth with the light bar staying on. This documents what was changed so
it can be reproduced / reversed.

## Issues this fixes on this box

1. **bluez `policy`-plugin double-connect race**: when a bonded controller
   (re)connects, bluez's `policy` plugin started its own outbound connect
   for the HID profile after a 2-second grace period, colliding with the
   controller's own inbound HID channels → book entry `input-hid` state
   machine refused/EALREADY'd them.

2. **`ClassicBondedOnly=true` at the BlueZ input server**: made the L2CAP
   HID listener sockets hold BT_IO_SEC_MEDIUM, which caused the
   `Connection refused – security block` refusal of the clone's channels
   that raced ahead of encryption.

3. **kernel `l2cap_core.c` hard `SEC_BLOCK`**: even with the listener at
   SDP level, the kernel's *inbound* L2CAP code refuses non-SDP PSMs when
   the ACL isn't already encrypted/authenticated (`hci_conn_check_link_mode`
   for SSP-capable peers). Genuine DS4 firmware retries after refusal;
   clone firmware doesn't. Patch: drop the hard refusal so the existing
   pending→late-accept path (`BT_CONNECT2` → `l2cap_conn_start()` sending
   `CR_SUCCESS` when auth/encryption complete) is used instead.

## Changes made (all still active)

| Bit | What | How to revert |
|---|---|---|
| `--noplugin=policy` on bluetoothd | disables policy plugin race | remove `/etc/systemd/system/bluetooth.service.d/override.conf`, `systemctl daemon-reload && systemctl restart bluetooth` |
| `ClassicBondedOnly=false` in `/etc/bluetooth/input.conf` | HID listeners don't demand MEDIUM | restore original line (`sudo sed -i 's/^ClassicBondedOnly=false/#ClassicBondedOnly=true/' /etc/bluetooth/input.conf`) |
| Patched `bluetooth.ko` (kernel module) | l2cap_core.c inbound no-SEC_BLOCK | `/lib/modules/$(uname -r)/kernel/net/bluetooth/bluetooth.ko.xz.orig` was saved; to revert: `sudo cp ...rigin && sudo depmod -a $(uname -r)` |
| `--debug` flag may still be set on bluetoothd (cosmetic) | keep noise out of the journal | remove `--debug` from override.conf |
Pairing note: controller bond info is in `/var/lib/bluetooth/<hci>/<mac>/info`.
If a controller ever fails to reconnect again due to a bad firmware bond
state, `bluetoothctl remove <MAC>` then re-pair with `Share + PS`
pairing-mode (fast double-blink light).

## To rebuild the kernel module after a future kernel update

Run `~/Agent/build-bluetooth-module.sh` after a new `dnf update` installs a
new kernel and *you reboot into it*. It does everything end-to-end (~15
minutes). The rolling prerequisites (dnf config-manager source repos,
etc.) may need slightly adapted flags on newer Fedora.

## Rollback of the local rebuilds

```bash
# 1) kernel module:
sudo cp /lib/modules/$(uname -r)/kernel/net/bluetooth/bluetooth.ko.xz.orig \
        /lib/modules/$(uname -r)/kernel/net/bluetooth/bluetooth.ko.xz
sudo depmod -a $(uname -r)
# 2) bluez daemon:
cd ~/rpmbuild/SPECS
rpmbuild -bi SPECS/bluez.spec >/dev/null 2>&1   # NOT RECOMMENDED — simpler:
sudo dnf reinstall bluez      # restores stock Fedora bluetoothd (different build, patch lost)
```

## References
- BlueZ bug #1765: "PS5 DualSense gamepad does not work on second use"
  https://github.com/bluez/bluez/issues/1765
- Upstream kernel code: `net/bluetooth/l2cap_core.c::l2cap_connect()` —
  the `SEC_BLOCK` early-refusal block.

## Files 
- `ds4-clone-l2cap-pending.patch` — the l2cap_core.c patch
- `build-bluetooth-module.sh` — end-to-end module rebuild helper
