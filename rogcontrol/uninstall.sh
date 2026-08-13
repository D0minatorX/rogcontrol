#!/usr/bin/env bash
set -euo pipefail

OK="✓"; WARN="⚠"; ERR="✗"
say()  { printf '%s %s\n' "$OK" "$*"; }
warn() { printf '%s %s\n' "$WARN" "$*"; }
die()  { printf '%s %s\n' "$ERR" "$*" >&2; exit 1; }
step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

APP_CONFIG="$HOME/.config/rogcontrol.json"
STATE_DIR="$HOME/.local/share/rogcontrol"
SUDOERS=/etc/sudoers.d/rogcontrol

echo "== ROG Control uninstaller =="
echo
[ "$(id -u)" -ne 0 ] || die "Do NOT run this as root. It uses sudo only where needed."

KEEP_SETTINGS=1
PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1; KEEP_SETTINGS=0 ;;
        -h|--help)
            echo "Usage: ./uninstall.sh [--purge]"
            echo
            echo "  (default)  Remove the app, services and helper."
            echo "             Your profiles, fan curves and calibration are KEPT"
            echo "             at $APP_CONFIG so a reinstall picks them up."
            echo "  --purge    Also delete settings, logs and install state."
            exit 0 ;;
        *) die "Unknown option: $arg (try --help)" ;;
    esac
done

step "What will be removed"
echo "  ~/.local/bin/rogcontrol*.py             (app and shortcut scripts)"
echo "  ~/.config/systemd/user/rogcontrol-*     (background services)"
echo "  ~/.local/share/applications/rogcontrol* (launchers)"
echo "  ~/.config/autostart/rogcontrol-*        (autostart entry)"
echo "  the app icon"
echo "  /usr/local/bin/rogcontrol-helper        (needs sudo)"
echo "  $SUDOERS                                (needs sudo)"
if [ "$PURGE" -eq 1 ]; then
    echo
    warn "--purge: settings, logs and install state will ALSO be deleted"
    echo "    $APP_CONFIG"
    echo "    $STATE_DIR"
else
    echo
    say "Your settings are KEPT: $APP_CONFIG"
    echo "    (profiles, fan curves, fan RPM calibration — run with --purge to delete)"
fi
echo
read -rp "Continue? [y/N] " a
[[ "${a:-N}" =~ ^[Yy] ]] || { echo "Cancelled — nothing was changed."; exit 0; }

# Offer a copy before touching anything. Uninstalling is exactly when
# someone realises later that they wanted their tuned curves back.
if [ "$KEEP_SETTINGS" -eq 0 ] && [ -f "$APP_CONFIG" ]; then
    BK="$HOME/rogcontrol-settings-$(date +%Y%m%d-%H%M%S).json"
    cp -p "$APP_CONFIG" "$BK" && say "Settings copied to $BK before deleting"
fi

step "Stopping services"
systemctl --user disable --now rogcontrol-enforcer.service >/dev/null 2>&1 || true
systemctl --user disable --now rogcontrol-apply.service    >/dev/null 2>&1 || true
pkill -f "local/bin/rogcontrol.py" 2>/dev/null || true
say "Services stopped and disabled, app closed"

step "Removing files"
rm -f "$HOME"/.local/bin/rogcontrol.py \
      "$HOME"/.local/bin/rogcontrol-enforcer.py \
      "$HOME"/.local/bin/rogcontrol-apply.py \
      "$HOME"/.local/bin/rogcontrol-cycle-profile.py \
      "$HOME"/.local/bin/rogcontrol-cycle-kbdlight.py \
      "$HOME"/.local/bin/rogcontrol-adjust-kbdbrightness.py
say "App and scripts removed"

rm -f "$HOME"/.config/systemd/user/rogcontrol-apply.service \
      "$HOME"/.config/systemd/user/rogcontrol-enforcer.service
systemctl --user daemon-reload
say "Service units removed"

rm -f "$HOME"/.local/share/applications/rogcontrol.desktop \
      "$HOME"/.local/share/applications/rogcontrol-cycle-profile.desktop \
      "$HOME"/.config/autostart/rogcontrol-autostart.desktop
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
for size in 16x16 22x22 24x24 32x32 48x48 64x64 128x128 256x256 512x512; do
    rm -f "$HOME/.local/share/icons/hicolor/$size/apps/rogcontrol.png"
done
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/rogcontrol.svg"
gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
say "Launchers, autostart and icon removed"

step "Removing the privileged helper"
if [ -e /usr/local/bin/rogcontrol-helper ] || sudo test -e "$SUDOERS" 2>/dev/null; then
    sudo rm -f /usr/local/bin/rogcontrol-helper
    sudo rm -f "$SUDOERS"
    say "Helper and sudoers rule removed"
else
    say "Nothing to remove (already gone)"
fi

if [ "$PURGE" -eq 1 ]; then
    step "Purging settings"
    rm -f "$APP_CONFIG" "$APP_CONFIG".backup-* "$APP_CONFIG".corrupt-*
    rm -rf "$STATE_DIR"
    say "Settings, logs and install state deleted"
fi

step "Note on hardware settings"
echo "  Uninstalling does not undo settings already written to the firmware."
echo "  Fan curves, power limits and the charge threshold stay as they are"
echo "  until something changes them or you reboot. If you want stock"
echo "  behaviour back, reset the fan curves in your BIOS or set the power"
echo "  profile from your desktop before removing this."

echo
echo "Done."
[ "$PURGE" -eq 0 ] && echo "  Settings kept at $APP_CONFIG — a reinstall will pick them up."
exit 0
