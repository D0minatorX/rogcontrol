#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OK="✓"; WARN="⚠"; ERR="✗"
say()  { printf '%s %s\n' "$OK" "$*"; }
warn() { printf '%s %s\n' "$WARN" "$*"; }
die()  { printf '%s %s\n' "$ERR" "$*" >&2; exit 1; }
step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

VERSION=1.0
STATE_DIR="$HOME/.local/share/rogcontrol"
STATE_FILE="$STATE_DIR/install-state"
APP_CONFIG="$HOME/.config/rogcontrol.json"

# Reads one key back out of the state file written by a previous install.
prev_get() {
    [ -f "$STATE_FILE" ] || return 0
    grep -E "^$1=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-
}

echo "== ROG Control installer =="
echo "For ASUS ROG laptops on Linux. Installs dependencies, the app, the"
echo "privileged helper, and the background services."
echo

[ "$(id -u)" -ne 0 ] || die "Do NOT run this as root. It uses sudo only where needed."

# --- fresh install or update? -----------------------------------------------
# An update must not undo work the user has already done: no re-running the
# two-minute fan calibration, no re-installing packages that are present,
# no re-asking questions that were already answered.
PREV_VER="$(prev_get version)"
if [ -n "$PREV_VER" ]; then
    MODE=update
elif [ -f "$HOME/.local/bin/rogcontrol.py" ]; then
    # Installed before this installer started recording state.
    MODE=update; PREV_VER=0
else
    MODE=fresh; PREV_VER=""
fi

case "$MODE" in
    fresh)  step "Fresh install (version $VERSION)" ;;
    update)
        if [ "$PREV_VER" = 0 ]; then
            step "Updating an existing install (pre-v10) to version $VERSION"
        elif [ "$PREV_VER" = "$VERSION" ]; then
            step "Reinstalling version $VERSION (repair)"
        else
            step "Updating version $PREV_VER to version $VERSION"
        fi
        say "Your settings, profiles and fan calibration are kept, not reset"

        # Any older version upgrades directly. There is no migration chain
        # to walk: the app only ever fills in config keys that are absent
        # and never rewrites ones that are present.
        if [ "$PREV_VER" != "$VERSION" ]; then
            echo "  Upgrading from $PREV_VER. See CHANGES.txt for what is new."
        fi

        # Belt and braces: the app migrates in place and the installer never
        # writes this file, but a copy costs nothing and an upgrade is
        # exactly when someone would want one.
        if [ -f "$APP_CONFIG" ]; then
            BACKUP="$APP_CONFIG.backup-v${PREV_VER:-unknown}-$(date +%Y%m%d%H%M%S)"
            cp -p "$APP_CONFIG" "$BACKUP" && say "Settings backed up to $(basename "$BACKUP")"
        fi
        ;;
esac

# ---------------------------------------------------------------- distro ----
step "Detecting system"
if   command -v pacman  >/dev/null 2>&1; then PM=pacman
elif command -v dnf     >/dev/null 2>&1; then PM=dnf
elif command -v apt-get >/dev/null 2>&1; then PM=apt
else PM=unknown; fi
say "Package manager: $PM"

# --- ASUS check -------------------------------------------------------------
# Everything here drives ASUS-specific firmware interfaces (asus-wmi, the
# asus_custom_fan_curve hwmon, the ASUS keyboard controller). On any other
# vendor the helper would be useless at best, so stop rather than install
# something that cannot work.
ASUS_DIR=/sys/devices/platform/asus-nb-wmi
VENDOR="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo unknown)"
PRODUCT="$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)"
say "Vendor:  $VENDOR"
say "Machine: $PRODUCT"

if [ "${ROGCONTROL_SKIP_VENDOR_CHECK:-0}" = 1 ]; then
    warn "Vendor check skipped by request (ROGCONTROL_SKIP_VENDOR_CHECK=1)"
elif ! printf '%s' "$VENDOR" | grep -qi 'asus'; then
    echo
    die "This machine reports vendor '$VENDOR', not ASUS.

ROG Control talks to ASUS firmware interfaces (asus-wmi platform knobs,
the asus_custom_fan_curve hwmon, ASUS keyboard controllers). None of that
exists on other hardware, so installing it here would not do anything.

If you believe this is wrong (some models report an unusual vendor
string), re-run with:  ROGCONTROL_SKIP_VENDOR_CHECK=1 ./install.sh"
fi

if ! lsmod 2>/dev/null | grep -q '^asus_nb_wmi\|^asus_wmi' \
   && [ ! -d /sys/devices/platform/asus-nb-wmi ]; then
    warn "asus-nb-wmi kernel module not loaded - most controls will be unavailable."
    warn "It normally loads automatically on supported ASUS laptops."
fi

command -v nvidia-smi >/dev/null 2>&1 \
    && say "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)" \
    || warn "No nvidia-smi yet - GPU controls need the NVIDIA driver"

# ------------------------------------------------------------ deps: repo ----
# name|check-command|pacman|dnf|apt
DEPS=(
  "python-gobject|python3 -c 'import gi'|python-gobject|python3-gobject|python3-gi"
  "gtk3|python3 -c 'import gi; gi.require_version(\"Gtk\",\"3.0\")'|gtk3|gtk3|libgtk-3-0"
  "libnotify|command -v notify-send|libnotify|libnotify|libnotify-bin"
  "nvidia-utils (nvidia-smi)|command -v nvidia-smi|nvidia-utils|nvidia-driver|nvidia-utils"
  "nvidia-settings|command -v nvidia-settings|nvidia-settings|nvidia-settings|nvidia-settings"
  "supergfxctl|command -v supergfxctl|supergfxctl|supergfxctl|"
)

missing_pkgs=(); missing_names=()
for entry in "${DEPS[@]}"; do
    IFS='|' read -r name check pac dnfp aptp <<<"$entry"
    if ! eval "$check" >/dev/null 2>&1; then
        case "$PM" in
            pacman) pkg="$pac" ;; dnf) pkg="$dnfp" ;; apt) pkg="$aptp" ;; *) pkg="" ;;
        esac
        missing_names+=("$name")
        [ -n "$pkg" ] && missing_pkgs+=("$pkg")
    fi
done

# AppIndicator: several package names across distros, any one will do
if ! python3 -c "
import gi
try: gi.require_version('AppIndicator3','0.1')
except ValueError: gi.require_version('AyatanaAppIndicator3','0.1')
" >/dev/null 2>&1; then
    missing_names+=("appindicator (system tray icon)")
    case "$PM" in
        pacman) missing_pkgs+=("libayatana-appindicator") ;;
        dnf)    missing_pkgs+=("libayatana-appindicator-gtk3") ;;
        apt)    missing_pkgs+=("gir1.2-ayatanaappindicator3-0.1") ;;
    esac
fi

step "Checking dependencies"
if [ ${#missing_names[@]} -eq 0 ]; then
    say "All repository dependencies already present - nothing to install"
else
    warn "Missing: ${missing_names[*]}"
    if [ ${#missing_pkgs[@]} -gt 0 ] && [ "$PM" != unknown ]; then
        echo "  Will install: ${missing_pkgs[*]}"
        read -rp "  Install them now with sudo? [Y/n] " a
        if [[ ! "${a:-Y}" =~ ^[Nn] ]]; then
            case "$PM" in
                pacman) sudo pacman -S --needed --noconfirm "${missing_pkgs[@]}" ;;
                dnf)    sudo dnf install -y "${missing_pkgs[@]}" ;;
                apt)    sudo apt-get update && sudo apt-get install -y "${missing_pkgs[@]}" ;;
            esac
            say "Repository dependencies installed"
        else
            warn "Skipped - some features will not work"
        fi
    else
        warn "Install these manually for your distro"
    fi
fi

# -------------------------------------------------------------- deps: AUR ---
# rogauracore (keyboard RGB) and ryzenadj (CPU power limits) are not in the
# normal repos. Only attempted on Arch-family systems.
if [ "$PM" = pacman ]; then
    if ! command -v rogauracore >/dev/null 2>&1 || ! command -v ryzenadj >/dev/null 2>&1; then
        step "AUR dependencies"
        AUR=""
        for h in yay paru; do command -v "$h" >/dev/null 2>&1 && { AUR="$h"; break; }; done

        if [ -z "$AUR" ]; then
            warn "No AUR helper (yay/paru) found"
            read -rp "  Install yay from source? [Y/n] " a
            if [[ ! "${a:-Y}" =~ ^[Nn] ]]; then
                sudo pacman -S --needed --noconfirm git base-devel
                tmp="$(mktemp -d)"
                git clone --depth 1 https://aur.archlinux.org/yay-bin.git "$tmp/yay-bin"
                ( cd "$tmp/yay-bin" && makepkg -si --noconfirm )
                rm -rf "$tmp"; AUR=yay; say "yay installed"
            fi
        fi

        if [ -n "$AUR" ]; then
            aur_want=()
            command -v rogauracore >/dev/null 2>&1 || aur_want+=("rogauracore-git")
            command -v ryzenadj    >/dev/null 2>&1 || aur_want+=("ryzenadj")
            if [ ${#aur_want[@]} -gt 0 ]; then
                echo "  Installing from AUR: ${aur_want[*]}"
                "$AUR" -S --needed --noconfirm "${aur_want[@]}" || \
                    warn "AUR install had problems - check output above"
            fi
        else
            warn "Skipping AUR packages. Keyboard RGB needs rogauracore;"
            warn "CPU power limits need ryzenadj."
        fi
    fi
fi

command -v rogauracore >/dev/null 2>&1 || warn "rogauracore missing - keyboard RGB colour/modes unavailable (brightness still works)"
command -v ryzenadj    >/dev/null 2>&1 || [ -x /usr/local/bin/ryzenadj ] || warn "ryzenadj missing - CPU power limit controls unavailable"

# --------------------------------------------------------------- helper -----
step "Installing privileged helper"
sudo install -o root -g root -m 755 "$SCRIPT_DIR/rogcontrol-helper" /usr/local/bin/rogcontrol-helper
say "Helper installed at /usr/local/bin/rogcontrol-helper"

# The app calls the helper through `sudo -n` (non-interactive) from a
# background service, so it needs a passwordless rule. Scoped to exactly
# this one binary for this one user - not a general sudo exemption.
# Validated with visudo before being put in place; a malformed sudoers file
# can lock you out of sudo entirely, so this never writes it directly.
SUDOERS=/etc/sudoers.d/rogcontrol
RULE="$USER ALL=(root) NOPASSWD: /usr/local/bin/rogcontrol-helper"
if sudo test -f "$SUDOERS" && sudo grep -qF "$RULE" "$SUDOERS" 2>/dev/null; then
    say "sudoers rule already present"
else
    tmp="$(mktemp)"; printf '%s\n' "$RULE" > "$tmp"
    if sudo visudo -cqf "$tmp" 2>/dev/null; then
        sudo install -o root -g root -m 440 "$tmp" "$SUDOERS"
        say "sudoers rule installed (passwordless, this binary only)"
    else
        warn "sudoers rule failed validation - NOT installed"
        warn "Add manually with 'sudo visudo -f $SUDOERS':"
        warn "  $RULE"
    fi
    rm -f "$tmp"
fi

# ------------------------------------------------------------------ app -----
step "Installing application"
mkdir -p "$HOME/.local/bin"
install -m 755 "$SCRIPT_DIR/rogcontrol.py" "$HOME/.local/bin/rogcontrol.py"
for s in rogcontrol-cycle-profile.py rogcontrol-cycle-kbdlight.py \
         rogcontrol-adjust-kbdbrightness.py rogcontrol-apply.py rogcontrol-enforcer.py; do
    install -m 755 "$SCRIPT_DIR/$s" "$HOME/.local/bin/$s"
done
say "App and scripts installed to ~/.local/bin"

mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"
install -m 644 "$SCRIPT_DIR/rogcontrol.png" \
    "$HOME/.local/share/icons/hicolor/256x256/apps/rogcontrol.png"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/rogcontrol.svg"
for size in 16x16 22x22 24x24 32x32 48x48 64x64 128x128 512x512; do
    rm -f "$HOME/.local/share/icons/hicolor/$size/apps/rogcontrol.png"
done
gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
say "Icon installed (stale sizes removed, cache refreshed)"

mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart"
sed "s|/home/YOUR_USERNAME|$HOME|g" "$SCRIPT_DIR/rogcontrol.desktop" \
    > "$HOME/.local/share/applications/rogcontrol.desktop"
sed "s|/home/YOUR_USERNAME|$HOME|g" "$SCRIPT_DIR/rogcontrol-cycle-profile.desktop" \
    > "$HOME/.local/share/applications/rogcontrol-cycle-profile.desktop"
sed "s|%h|$HOME|g" "$SCRIPT_DIR/rogcontrol-autostart.desktop" \
    > "$HOME/.config/autostart/rogcontrol-autostart.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
say "Launcher + silent autostart installed"

# -------------------------------------------------------------- services ----
step "Installing services"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$SCRIPT_DIR/rogcontrol-apply.service" \
               "$SCRIPT_DIR/rogcontrol-enforcer.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now rogcontrol-apply.service    >/dev/null 2>&1 || true
systemctl --user restart      rogcontrol-enforcer.service >/dev/null 2>&1 || true
systemctl --user enable       rogcontrol-enforcer.service >/dev/null 2>&1 || true
say "Boot-reapply and enforcer services enabled"

# Leftovers from an older build that used keyboard power-event hooks.
if [ -f /etc/systemd/system-sleep/rogcontrol-sleep-hook.py ] || \
   [ -f /etc/systemd/system/rogcontrol-shutdown-hook.service ]; then
    sudo systemctl disable --now rogcontrol-shutdown-hook.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/rogcontrol-shutdown-hook.service \
               /etc/systemd/system-sleep/rogcontrol-sleep-hook.py \
               /usr/local/bin/rogcontrol-shutdown-hook.py
    sudo systemctl daemon-reload
    say "Removed leftover hooks from an earlier install"
fi

# --------------------------------------------------------------- verify -----
step "Verifying"
if sudo -n /usr/local/bin/rogcontrol-helper asusd_status >/dev/null 2>&1; then
    say "Passwordless helper works"
else
    warn "Helper not callable without a password - the enforcer cannot work."
    warn "Check $SUDOERS."
fi

# What this particular machine supports. The app greys out anything missing
# rather than offering a control that silently does nothing, so this is
# just telling you up front what you will and will not see.
echo
echo "  Feature support on this machine:"
# Probing is just a handful of sysfs and PATH checks, so it costs nothing
# and always runs -- that way a package installed since last time is picked
# up instead of a stale "missing" being carried forward. What an update
# skips is the expensive, user-visible work: package installs that are
# already satisfied, and the fan calibration.
#
# cap <label> <0|1> <state-key> [note]
CAP_STATE=""
cap() {
    local label="$1" now="$2" key="$3" note="${4:-}" before mark suffix=""
    before="$(prev_get "cap_$key")"
    CAP_STATE="${CAP_STATE}cap_$key=$now"$'\n'
    if [ "$MODE" = update ] && [ -n "$before" ] && [ "$before" != "$now" ]; then
        if [ "$now" -eq 1 ]; then suffix="  <- NEW since last install"
        else suffix="  <- NO LONGER AVAILABLE"; fi
    elif [ "$MODE" = update ] && [ -z "$before" ]; then
        suffix="  <- newly checked in v$VERSION"
    fi
    if [ "$now" -eq 1 ]; then mark="$OK"; else mark="$WARN"; fi
    printf '    %s %s%s%s\n' "$mark" "$label" \
        "$( [ "$now" -eq 1 ] || printf '%s' "${note:+ - $note}" )" "$suffix"
}
grep -qx asus_custom_fan_curve /sys/class/hwmon/*/name 2>/dev/null && f=1 || f=0
cap "Fan curves" $f fan_curve "no asus_custom_fan_curve on this kernel/model"
grep -qx asus /sys/class/hwmon/*/name 2>/dev/null && f=1 || f=0
cap "Fan RPM readout" $f fan_rpm "no asus hwmon"
[ -e "$ASUS_DIR/nv_temp_target" ]   && f=1 || f=0
cap "GPU temperature target" $f nv_temp_target "asus-wmi does not expose it"
[ -e "$ASUS_DIR/nv_dynamic_boost" ] && f=1 || f=0
cap "GPU dynamic boost" $f nv_dynamic_boost "asus-wmi does not expose it"
command -v nvidia-smi >/dev/null 2>&1      && f=1 || f=0
cap "GPU power / clock limit" $f nvidia "nvidia-smi missing"
command -v nvidia-settings >/dev/null 2>&1 && f=1 || f=0
cap "GPU clock offsets" $f nvidia_settings "nvidia-settings missing"
command -v supergfxctl >/dev/null 2>&1     && f=1 || f=0
cap "GPU mode switching" $f supergfxctl "supergfxctl missing"
{ command -v ryzenadj >/dev/null 2>&1 || [ -x /usr/local/bin/ryzenadj ]; } && f=1 || f=0
cap "CPU power limits / undervolt" $f ryzenadj "ryzenadj missing (AMD Ryzen only)"
command -v rogauracore >/dev/null 2>&1 && f=1 || f=0
cap "Keyboard RGB colours/modes" $f rogauracore "rogauracore missing"
[ -e /sys/class/leds/asus::kbd_backlight/brightness ] && f=1 || f=0
cap "Keyboard backlight brightness" $f kbd_backlight "no asus::kbd_backlight LED"
f=0; for b in /sys/class/power_supply/*/charge_control_end_threshold; do [ -e "$b" ] && f=1; done
cap "Battery charge limit" $f charge_limit "battery has no charge threshold"

# --- fan calibration status -------------------------------------------------
# The calibration lives in the app's own config and is never touched by the
# installer, so an update keeps it. Only say something if it is missing.
echo
if grep -q '"fan_rpm_cal"' "$APP_CONFIG" 2>/dev/null; then
    say "Fan RPM calibration found - kept as is, no need to re-run it"
elif [ "$(prev_get cap_fan_curve)" = 1 ] || grep -qx asus_custom_fan_curve /sys/class/hwmon/*/name 2>/dev/null; then
    if [ "$MODE" = fresh ]; then
        warn "Fans are not calibrated yet."
        echo "     The RPM numbers shown come from the developer's laptop and will be"
        echo "     somewhat wrong on yours. Open the Fans tab and press"
        echo "     \"Calibrate fan RPM\" once (about two minutes) to measure your own."
    else
        warn "Fans still not calibrated - see the Fans tab, \"Calibrate fan RPM\""
    fi
fi

# --- record state for next time ---------------------------------------------
mkdir -p "$STATE_DIR"
{
    echo "version=$VERSION"
    echo "installed_at=$(date -Is)"
    printf '%s' "$CAP_STATE"
} > "$STATE_FILE"
say "Install state recorded (next run will detect this as an update)"

# Ship the uninstaller next to everything else so it is findable later.
install -m 755 "$SCRIPT_DIR/uninstall.sh" "$HOME/.local/bin/rogcontrol-uninstall.sh" 2>/dev/null \
    && say "Uninstaller available: ~/.local/bin/rogcontrol-uninstall.sh"

echo
echo "Done."
echo "  Launch from the app grid ('ROG Control'), or: ~/.local/bin/rogcontrol.py"
echo "  It also starts minimised to the tray at login."
echo "  Services:  systemctl --user status rogcontrol-enforcer.service"
echo
echo "  Optional keyboard shortcuts (bind in your desktop settings):"
echo "    ~/.local/bin/rogcontrol-cycle-profile.py"
echo "    ~/.local/bin/rogcontrol-cycle-kbdlight.py"
echo "    ~/.local/bin/rogcontrol-adjust-kbdbrightness.py up|down"
