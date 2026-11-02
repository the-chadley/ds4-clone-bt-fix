#!/bin/sh
# Per-user install for ds4-tester (no root needed):
#   * application menu entry (System category) + icon
#   * lightbar colour restore service (started by the udev rule on connect)
#
# The system-wide parts (ds4-fullmode + 70-ds4-tester.rules) need root;
# see the header of 70-ds4-tester.rules.
#
# Re-run after moving the repo: the menu entry points at this checkout.
# Uninstall: ./install-user.sh --uninstall
set -e
here=$(cd "$(dirname "$0")" && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}
conf=${XDG_CONFIG_HOME:-$HOME/.config}
sizes="32 48 64 128 256"

if [ "$1" = "--uninstall" ]; then
    rm -f "$data/applications/ds4-tester.desktop" \
          "$HOME/.local/bin/ds4-lightbar-restore" \
          "$conf/systemd/user/ds4-lightbar@.service"
    for s in $sizes; do
        rm -f "$data/icons/hicolor/${s}x${s}/apps/ds4-tester.png"
    done
    systemctl --user daemon-reload 2>/dev/null || true
    echo "removed ds4-tester menu entry, icons and lightbar service"
    exit 0
fi

# icons, rendered from the same artwork code the app uses
for s in $sizes; do
    mkdir -p "$data/icons/hicolor/${s}x${s}/apps"
    python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import ds4art; open(sys.argv[3], "wb").write(ds4art.icon_png(int(sys.argv[2])))' \
        "$here" "$s" "$data/icons/hicolor/${s}x${s}/apps/ds4-tester.png"
done

mkdir -p "$data/applications"
cat > "$data/applications/ds4-tester.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DS4 Tester
GenericName=Controller Tester
Comment=Test DS4 clone controllers: buttons, sticks, touchpad, lightbar and rumble
Exec=python3 "$here/ds4-tester.py"
Icon=ds4-tester
Terminal=false
Categories=System;
Keywords=gamepad;controller;joystick;ds4;dualshock;lightbar;
StartupWMClass=Ds4-tester
EOF

install -Dm 0755 "$here/ds4-lightbar-restore" "$HOME/.local/bin/ds4-lightbar-restore"
install -Dm 0644 "$here/ds4-lightbar@.service" "$conf/systemd/user/ds4-lightbar@.service"
systemctl --user daemon-reload 2>/dev/null || true

command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

echo "installed: DS4 Tester (System menu), icons, lightbar restore service"
