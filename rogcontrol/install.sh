#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OK="✓"; WARN="⚠"; ERR="✗"
say()  { printf '%s %s\n' "$OK" "$*"; }
warn() { printf '%s %s\n' "$WARN" "$*"; }
die()  { printf '%s %s\n' "$ERR" "$*" >&2; exit 1; }
step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

VERSION=1.0.0.5
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

# ---------------------------------------------------------------- system ----
# Detected once, here, and before anything else consults it, because three
# separate decisions further down turn on the answer: which package manager
# to install with, whether /usr can be written to at all, and whether the
# tray needs a desktop extension on top of the library.
#
# os-release is read in a subshell on purpose. It defines VERSION, and this
# script has its own VERSION (the install version, set above) that every
# fresh/update decision depends on -- sourcing it in place would silently
# overwrite that with the distro's version string.
OS_ID=""; OS_LIKE=""; OS_NAME=""
if [ -r /etc/os-release ]; then
    OS_ID="$(   . /etc/os-release 2>/dev/null; printf '%s' "${ID:-}" )"
    OS_LIKE="$( . /etc/os-release 2>/dev/null; printf '%s' "${ID_LIKE:-}" )"
    OS_NAME="$( . /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-${ID:-}}" )"
fi

# An atomic (rpm-ostree/bootc) system has a read-only /usr that only
# rpm-ostree may change. ostree writes /run/ostree-booted at boot, so this is
# true on Bazzite, Silverblue, Kinoite and any bootc image, and absent on
# every traditional distro including plain Fedora.
if [ -f /run/ostree-booted ]; then ATOMIC=1; else ATOMIC=0; fi

# ID_LIKE rather than ID, because the derivatives are the whole point:
# CachyOS reports ID=cachyos ID_LIKE=arch and Bazzite reports ID=bazzite
# ID_LIKE=fedora, so matching the family handles both -- and every other
# derivative -- without this script having to know their names.
case " $OS_ID $OS_LIKE " in
    *" arch "*)                PM=pacman ;;
    *" fedora "*|*" rhel "*)   PM=dnf ;;
    *" debian "*|*" ubuntu "*) PM=apt ;;
    *)  # No os-release, or a family not named above: fall back to whatever
        # is actually installed, which is how this script always worked.
        if   command -v pacman  >/dev/null 2>&1; then PM=pacman
        elif command -v dnf     >/dev/null 2>&1; then PM=dnf
        elif command -v apt-get >/dev/null 2>&1; then PM=apt
        else PM=unknown; fi ;;
esac
# An atomic host is in the fedora family but has no dnf to run: its packages
# come from rpm-ostree or from a container. Recorded separately from PM so
# the DEPS table below still knows which column of package names to read.
PM_HOST="$PM"
if [ "$ATOMIC" = 1 ] && ! command -v dnf >/dev/null 2>&1; then PM_HOST=none; fi

# The tray is a StatusNotifierItem. Plasma implements that in the panel
# itself; GNOME Shell does not, and needs an extension on top of the
# library -- which is a desktop question, not a distro one, so it is asked
# the same way on Bazzite and on CachyOS.
DESKTOP=other
case "$(printf '%s' "${XDG_CURRENT_DESKTOP:-${XDG_SESSION_DESKTOP:-}}" \
        | tr '[:upper:]' '[:lower:]')" in
    *gnome*)        DESKTOP=gnome ;;
    *kde*|*plasma*) DESKTOP=kde ;;
esac

# --- hard requirement: GTK4 + libadwaita ------------------------------------
# Checked before anything else happens: before any sudo, any package install
# and even before the settings backup, so a machine that cannot run the app is
# left exactly as it was found. Everything checked further down only costs a
# feature if it is missing; this one is the entire window.
#
# Named packages rather than the ImportError python would otherwise raise on
# first launch, which tells the user nothing they can act on.
step "Checking GTK4 and libadwaita"

PENDING_REBOOT=0

# The GUI packages, as one list: GTK4 and libadwaita for the window,
# PyGObject to reach them from python, GTK3 and an AppIndicator binding for
# the tray (a separate process -- see the DEPS note further down), cairo for
# the fan-curve graphs, libnotify for notifications. Only used on an atomic
# system; everywhere else the hard gate stays a hard gate and the optional
# ones go through the DEPS table as before.
GUI_PKGS="gtk4 libadwaita python3-gobject gtk3 libayatana-appindicator-gtk3 python3-cairo libnotify"

GTK4_OK=0
python3 -c "
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
" >/dev/null 2>&1 && GTK4_OK=1

if [ "$GTK4_OK" = 1 ]; then
    say "GTK4 and libadwaita present"
elif [ "$ATOMIC" = 0 ]; then
    die "GTK4 and libadwaita are required - the ROG Control window is built on them.

  Arch:   sudo pacman -S gtk4 libadwaita python-gobject
  Fedora: sudo dnf install gtk4 libadwaita python3-gobject
  Debian: sudo apt install libgtk-4-1 libadwaita-1-0 python3-gi

Install them and run this again. Nothing has been changed."
else
    # Missing, on a system where they cannot simply be installed. There is a
    # way round it, but every one of those ways changes the machine, and the
    # promise this check makes is that a machine which cannot run the app is
    # left exactly as it was found -- so the asking and the doing wait until
    # after the ASUS check below has confirmed the app is worth installing
    # here at all.
    warn "GTK4/libadwaita/PyGObject are not usable from this system's python"
    warn "This is an atomic system - options come after the hardware check"
fi

# --- the atomic remedy, deferred until the machine has been vetted ----------
atomic_gui_setup() {
    # An atomic system. /usr is read-only and there is no dnf, so the missing
    # packages cannot simply be installed the way they are everywhere else.
    # rpm-ostree layers them onto the system as real packages; that needs a
    # REBOOT before the app can run, and layered packages make future OS
    # updates slower and can occasionally break them -- but the helper, the
    # services, the hardware access and the settings all land on the real
    # system exactly like every other install, with nothing containerised.
    command -v rpm-ostree >/dev/null 2>&1 \
        || die "rpm-ostree is not installed, so the GUI packages cannot be layered here."
    echo
    echo "  This is an atomic (rpm-ostree) system: ${OS_NAME:-unknown}"
    echo "  Its /usr is read-only, so the GUI packages are layered onto the"
    echo "  system with rpm-ostree. This needs a REBOOT before the app can run."
    echo
    step "Layering the GUI packages with rpm-ostree"
    echo "  This takes a few minutes. Nothing else is interactive."
    # --idempotent so a re-run after a partial install does not fail on
    # the packages that already went in.
    sudo rpm-ostree install --idempotent -y $GUI_PKGS \
        || die "rpm-ostree could not layer the packages. Nothing else has been changed."
    PENDING_REBOOT=1
    say "Packages layered - they become usable after a reboot"
}

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
        # The second half of an rpm-ostree install. Nothing special has to
        # happen -- every step below is written to be safe to repeat, and
        # the GTK check at the top now passes because the layered packages
        # came up with this boot -- but saying so beats looking like a
        # reinstall the user did not ask for.
        if [ "$(prev_get pending_reboot)" = 1 ]; then
            say "Finishing the install that was waiting on a reboot"
        fi
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
            echo "  Upgrading from $PREV_VER. See 2-FEATURES.txt for what the app does."
        fi

        # Migration used to be left to whichever process happened to load
        # the config next -- the enforcer service, the tray, the window,
        # whichever won the race after this script exited. That meant a
        # migration bug surfaced later, unverified, in a background service
        # with nobody watching, on top of a config this script had already
        # declared "kept, not reset" and moved on from.
        #
        # A same-version repair has nothing to migrate -- migrate_config
        # only fills in keys absent in an older schema, and the file is
        # already this schema -- so there is nothing here worth backing up
        # or re-running.
        if [ "$PREV_VER" != "$VERSION" ] && [ -f "$APP_CONFIG" ]; then
            BACKUP="$APP_CONFIG.backup-v${PREV_VER:-unknown}-$(date +%Y%m%d%H%M%S)"
            cp -p "$APP_CONFIG" "$BACKUP"
            MIGRATE_ERR="$(mktemp)"

            # Migrated and saved now, synchronously, instead of waiting for
            # whatever loads the config next. Verified by profile name
            # rather than a byte-for-byte diff: migrate_config's own
            # contract (config.py) is that it only ever fills in missing
            # keys and never touches or drops one already there, so a
            # profile going missing is the one thing that contract
            # promises cannot happen -- and therefore the one thing worth
            # actually checking rather than trusting.
            if PYTHONPATH="$HOME/.local/lib" python3 -c "
from rogcontrol import config

# profiles is a dict keyed by name, not a list -- so the names are its keys.
before = set(config.load_config('$BACKUP')['profiles'].keys())

config.save_config(config.load_config('$APP_CONFIG'), '$APP_CONFIG')

after = set(config.load_config('$APP_CONFIG')['profiles'].keys())

if after != before:
    raise SystemExit(f'profiles before: {sorted(before)} '
                      f'after: {sorted(after)}')
" 2>"$MIGRATE_ERR"
            then
                rm -f "$BACKUP"
                say "Settings migrated to the new format and verified"
            else
                # $APP_CONFIG holds whatever save_config just wrote -- the
                # broken result -- and $BACKUP still holds the untouched
                # original, since nothing above has written to it. Captured
                # in that order: once the restore below runs, the broken
                # copy is gone from everywhere else it could be read back.
                cp -p "$APP_CONFIG" "$BACKUP.failed" 2>/dev/null
                cp -p "$BACKUP" "$APP_CONFIG"
                rm -f "$BACKUP"
                warn "Migration check failed - restored your settings from before it ran"
                warn "  $(cat "$MIGRATE_ERR" 2>/dev/null)"
                warn "  Your original settings are safe, unchanged, at $APP_CONFIG"
                warn "  What migration produced is kept at $BACKUP.failed for a bug report"
            fi
            rm -f "$MIGRATE_ERR"
        fi
        ;;
esac

# ---------------------------------------------------------------- distro ----
# Detection itself happened at the top, before the GTK4 gate that depends on
# it. This only reports what it found.
step "Detected system"
say "Distro: ${OS_NAME:-unknown}${OS_LIKE:+ (family: $OS_LIKE)}"
say "Package manager: $PM$( [ "$PM_HOST" = none ] && printf '%s' " (atomic - no host package manager)" )"
[ "$ATOMIC" = 1 ] && say "Atomic/ostree system - /usr is read-only"
case "$DESKTOP" in
    gnome) say "Desktop: GNOME" ;;
    kde)   say "Desktop: KDE Plasma" ;;
    *)     say "Desktop: ${XDG_CURRENT_DESKTOP:-unknown}" ;;
esac

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

# The machine is an ASUS and the app is worth installing here, so the atomic
# question deferred at the top can now be asked. Deliberately after the
# vendor check and not before it: this is the first step in the whole script
# that changes anything, and a machine that was going to be turned away
# should be turned away without a container or a layered package on it.
if [ "$GTK4_OK" = 0 ] && [ "$ATOMIC" = 1 ]; then
    atomic_gui_setup
fi

# ------------------------------------------------------------ deps: repo ----
# GTK4 and libadwaita were already required above, hard. What follows is the
# soft list: each one costs a single feature if it is absent, so it is offered
# rather than demanded. GTK3 is still on it because the tray icon is a
# separate GTK3 process (AppIndicator has no GTK4 binding, and one process
# cannot load both toolkits) -- the window itself no longer touches GTK3.
#
# tuned-ppd is what Bazzite ships for the same job and it answers on the same
# D-Bus name (see hardware.py's PPD_BUS_NAMES / read_power_mode) -- the app
# already works against it with no powerprofilesctl installed. dnf/rpm-ostree
# refuse power-profiles-daemon outright while tuned-ppd is on the system
# (both provide ppd-service), so this has to be checked before that package
# is ever offered, not just left for the app to cope with at runtime.
have_ppd_or_equiv() {
    command -v powerprofilesctl >/dev/null 2>&1 && return 0
    rpm -q tuned-ppd >/dev/null 2>&1
}

# name|check-command|pacman|dnf|apt
DEPS=(
  "python-gobject|python3 -c 'import gi'|python-gobject|python3-gobject|python3-gi"
  "gtk3 (tray icon)|python3 -c 'import gi; gi.require_version(\"Gtk\",\"3.0\")'|gtk3|gtk3|libgtk-3-0"
  "libnotify|command -v notify-send|libnotify|libnotify|libnotify-bin"
  "nvidia-utils (nvidia-smi)|command -v nvidia-smi|nvidia-utils|nvidia-driver|nvidia-utils"
  "nvidia-settings|command -v nvidia-settings|nvidia-settings|nvidia-settings|nvidia-settings"
  "supergfxctl|command -v supergfxctl|supergfxctl|supergfxctl|"
  "python-cairo (fan curve graphs)|python3 -c 'import cairo'|python-cairo|python3-cairo|python3-cairo"
  "power-profiles-daemon (OS power-mode sync)|have_ppd_or_equiv|power-profiles-daemon|power-profiles-daemon|power-profiles-daemon"
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
if ! eval "python3 -c \"
import gi
try: gi.require_version('AppIndicator3','0.1')
except ValueError: gi.require_version('AyatanaAppIndicator3','0.1')
\"" >/dev/null 2>&1; then
    missing_names+=("appindicator (system tray icon)")
    case "$PM" in
        pacman) missing_pkgs+=("libayatana-appindicator") ;;
        dnf)    missing_pkgs+=("libayatana-appindicator-gtk3") ;;
        apt)    missing_pkgs+=("gir1.2-ayatanaappindicator3-0.1") ;;
    esac
fi

# supergfxctl is not in Fedora's own repos at all (official or
# updates-archive) -- it only ever comes from the asus-linux COPR. Without
# this, `rpm-ostree install supergfxctl` (and plain `dnf install` on
# traditional Fedora) fails with "Packages not found" even on a correctly
# imaged Bazzite box, which looks like a missing package but is really a
# missing repo.
ensure_asus_linux_copr() {
    local relver repo_file
    relver="$(rpm -E %fedora 2>/dev/null)"
    [ -n "$relver" ] || return 1
    repo_file="/etc/yum.repos.d/lukenukem-asus-linux-fedora-$relver.repo"
    [ -f "$repo_file" ] && return 0
    step "Adding asus-linux COPR (supergfxctl lives there, not in Fedora's repos)"
    sudo curl -fsSL \
        "https://copr.fedorainfracloud.org/coprs/lukenukem/asus-linux/repo/fedora-$relver/lukenukem-asus-linux-fedora-$relver.repo" \
        -o "$repo_file" \
        && say "asus-linux COPR added" \
        || { warn "Could not add the asus-linux COPR - supergfxctl install will fail"; return 1; }
}

step "Checking dependencies"
if [ ${#missing_names[@]} -eq 0 ]; then
    say "All repository dependencies already present - nothing to install"
elif [ "$PM_HOST" = none ]; then
    # rpm-ostree path. Every one of these is optional, so a failure here
    # costs a feature, not the install -- attempted automatically rather than
    # just printed, same as the GTK4 layering above, but errors are warned
    # past instead of fatal.
    warn "Missing: ${missing_names[*]}"
    if [ ${#missing_pkgs[@]} -gt 0 ]; then
        case " ${missing_pkgs[*]} " in *" supergfxctl "*) ensure_asus_linux_copr ;; esac
        step "Layering optional packages with rpm-ostree"
        echo "  ${missing_pkgs[*]}"
        if sudo rpm-ostree install --idempotent -y "${missing_pkgs[@]}"; then
            PENDING_REBOOT=1
            say "Layered - they become usable after a reboot"
        else
            warn "rpm-ostree could not layer some of these - the features they"
            warn "cover will stay unavailable. To retry by hand:"
            warn "  sudo rpm-ostree install ${missing_pkgs[*]}"
        fi
    fi
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

# ------------------------------------------------------------- sleep hook ---
step "Installing suspend/resume fan hook"
# The hook is a plain script driven by a systemd unit, NOT a drop-in in a
# system-sleep directory. Both of the earlier attempts at the drop-in were
# broken, in opposite directions:
#
#   /usr/lib/systemd/system-sleep  is the only directory systemd-sleep ever
#       executes -- and /usr is read-only on an atomic system, so writing it
#       there killed the install partway through on Bazzite.
#   /etc/systemd/system-sleep      is writable everywhere, and is read by
#       nothing. systemd-sleep has exactly one hard-coded directory and there
#       is no /etc counterpart to it, unlike almost every other systemd path.
#       So the hook installed cleanly, looked installed, and never ran once.
#
# A unit wanted by sleep.target has neither problem: /etc/systemd/system is
# writable on every distro including the atomic ones and is where systemd
# looks for units. See rogcontrol-fan-sleep.service for how one oneshot unit
# covers both the "pre" and the "post" half.
#
# The script goes to /usr/local/bin beside the helper it calls -- the same
# root-owned, mode-755, writable-even-on-ostree location, already proven by
# the helper install above. The username is baked in at install time: this
# file runs as root with no login session, so it cannot resolve ~ on its own
# for the "post" step.
SLEEP_HOOK=/usr/local/bin/rogcontrol-fan-sleep-hook
SLEEP_UNIT=/etc/systemd/system/rogcontrol-fan-sleep.service
tmp="$(mktemp)"
sed "s/__ROGCONTROL_USER__/$USER/" "$SCRIPT_DIR/rogcontrol-fan-sleep-hook" > "$tmp"
sudo install -D -o root -g root -m 755 "$tmp" "$SLEEP_HOOK"
rm -f "$tmp"
sudo install -D -o root -g root -m 644 "$SCRIPT_DIR/rogcontrol-fan-sleep.service" "$SLEEP_UNIT"
sudo systemctl daemon-reload
# Not --now: starting it would run the "pre" half and park the fans at their
# floor on a machine that is wide awake. sleep.target pulls it in by itself
# at the next suspend, which is the only time it should ever run.
if sudo systemctl enable rogcontrol-fan-sleep.service >/dev/null 2>&1; then
    say "Suspend hook enabled (rogcontrol-fan-sleep.service)"
else
    warn "Could not enable rogcontrol-fan-sleep.service - fans will keep the"
    warn "active profile's curve while the machine is asleep."
fi
# Copies from both of the earlier drop-in locations. The /usr/lib one runs on
# top of the unit and would do the whole thing twice per suspend -- and the
# "pre" half spends a second per fan channel, so the cost is visible. The /etc
# one never ran at all, but it calls a helper that an uninstall removes, so
# leaving it behind is a stale file pointed at a binary that may be gone.
for OLD_SLEEP_HOOK in /usr/lib/systemd/system-sleep/rogcontrol-fan-sleep-hook \
                      /etc/systemd/system-sleep/rogcontrol-fan-sleep-hook; do
    if sudo test -e "$OLD_SLEEP_HOOK" 2>/dev/null; then
        sudo rm -f "$OLD_SLEEP_HOOK" \
            && say "Removed the old drop-in copy from $(dirname "$OLD_SLEEP_HOOK")"
    fi
done
say "Fans will drop to idle before suspend and restore the active profile on resume"

# ------------------------------------------------------------------ app -----
step "Installing application"
mkdir -p "$HOME/.local/bin"

# The application is a python package now, not one big script, so it goes
# where an import can find it rather than into ~/.local/bin. The enforcer and
# the tray both already look for it here (they add ~/.local/lib to sys.path
# when there is no package beside them).
#
# Wiped first: a module deleted or renamed between versions would otherwise
# stay behind and keep being importable, and a stale __pycache__ next to a
# newer source file is its own class of confusing. Nothing of the user's
# lives here -- settings are in ~/.config/rogcontrol.json.
#
# The enforcer imports out of this directory and is Restart=always, so it has
# to be stopped for the moment the directory does not exist. Left running, it
# dies on ImportError, systemd restarts it, and it dies again -- and an
# install interrupted anywhere in here (set -e, a full disk, Ctrl-C) leaves
# it crash-looping for as long as the machine is up. Stopped, the worst case
# is a service that is not running until the next install or login, which is
# visible and harmless. It is started again at the end of the services step.
ENFORCER_WAS_RUNNING=0
if systemctl --user is-active --quiet rogcontrol-enforcer.service 2>/dev/null; then
    ENFORCER_WAS_RUNNING=1
    systemctl --user stop rogcontrol-enforcer.service >/dev/null 2>&1 || true
    say "Stopped the enforcer while the library is replaced"
fi
# Same reasoning as the enforcer above: now that the tray is Restart=on-
# failure too, an ImportError from a half-replaced library would otherwise
# crash-loop it for as long as the directory is missing.
if systemctl --user is-active --quiet rogcontrol-tray.service 2>/dev/null; then
    systemctl --user stop rogcontrol-tray.service >/dev/null 2>&1 || true
    say "Stopped the tray while the library is replaced"
fi

LIBDIR="$HOME/.local/lib/rogcontrol"
rm -rf "$LIBDIR"
mkdir -p "$LIBDIR/pages" "$LIBDIR/widgets" "$LIBDIR/icons"
for f in "$SCRIPT_DIR"/*.py; do
    # rogcontrol-*.py are the standalone executables sitting in the same
    # directory. They are not part of the package (and are not even importable
    # module names), so they go to ~/.local/bin below instead.
    case "$(basename "$f")" in rogcontrol-*) continue ;; esac
    install -m 644 "$f" "$LIBDIR/"
done
for sub in pages widgets; do
    install -m 644 "$SCRIPT_DIR/$sub"/*.py "$LIBDIR/$sub/"
done
# app.py's sidebar icons -- bundled so the sidebar looks the same regardless
# of the desktop's icon theme (see the add_search_path call in app.py).
install -m 644 "$SCRIPT_DIR"/icons/*.svg "$LIBDIR/icons/"
say "Application package installed to ~/.local/lib/rogcontrol"

# The launcher. `python3 -m rogcontrol` with ~/.local/lib on the path, which
# is the one thing a user, a .desktop file and the tray all need and none of
# them should have to know. Flags are passed straight through, so
# --minimized/--toggle/--self-test/--quit all still work.
cat > "$HOME/.local/bin/rogcontrol" <<EOF
#!/usr/bin/env bash
# ROG Control launcher - generated by install.sh, edits will be overwritten.
export PYTHONPATH="$HOME/.local/lib\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m rogcontrol "\$@"
EOF
chmod 755 "$HOME/.local/bin/rogcontrol"
say "Launcher installed: ~/.local/bin/rogcontrol"

for s in rogcontrol-tray rogcontrol-cycle-profile.py rogcontrol-cycle-kbdlight.py \
         rogcontrol-adjust-kbdbrightness.py rogcontrol-adjust-kbdspeed.py \
         rogcontrol-apply.py rogcontrol-enforcer.py; do
    install -m 755 "$SCRIPT_DIR/$s" "$HOME/.local/bin/$s"
done
say "Tray and shortcut scripts installed to ~/.local/bin"

# The GTK3 application this replaces. Removed so that nothing -- an old
# autostart entry a desktop has already cached, a hotkey the user bound by
# hand, muscle memory at a terminal -- can start the retired window against
# the same config the new one is using. Only that one file: the settings, the
# services and the helper are shared by both and stay exactly where they are.
if [ -f "$HOME/.local/bin/rogcontrol.py" ]; then
    rm -f "$HOME/.local/bin/rogcontrol.py"
    say "Removed the old GTK3 app (~/.local/bin/rogcontrol.py) - settings kept"
    if pgrep -f "local/bin/rogcontrol.py" >/dev/null 2>&1; then
        warn "The old app is still running. Quit it from its tray icon (or log"
        warn "out) before using the new one - two trays would fight over the config."
    fi
fi

mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"
install -m 644 "$SCRIPT_DIR/rogcontrol.png" \
    "$HOME/.local/share/icons/hicolor/256x256/apps/rogcontrol.png"
# The tray's per-profile-colour icons -- red is rogcontrol.png itself
# (Performance and any custom profile), these two cover Quiet and the two
# Balanced profiles. See rogcontrol-tray's PROFILE_ICON_PATH.
install -m 644 "$SCRIPT_DIR/rogcontrol-quiet.png" \
    "$HOME/.local/share/icons/hicolor/256x256/apps/rogcontrol-quiet.png"
install -m 644 "$SCRIPT_DIR/rogcontrol-balanced.png" \
    "$HOME/.local/share/icons/hicolor/256x256/apps/rogcontrol-balanced.png"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/rogcontrol.svg"
for size in 16x16 22x22 24x24 32x32 48x48 64x64 128x128 512x512; do
    rm -f "$HOME/.local/share/icons/hicolor/$size/apps/rogcontrol.png"
done
gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
say "Icon installed (stale sizes removed, cache refreshed)"

mkdir -p "$HOME/.local/share/applications"
sed "s|/home/YOUR_USERNAME|$HOME|g" "$SCRIPT_DIR/org.rogcontrol.RogControl.desktop" \
    > "$HOME/.local/share/applications/org.rogcontrol.RogControl.desktop"
sed "s|/home/YOUR_USERNAME|$HOME|g" "$SCRIPT_DIR/rogcontrol-cycle-profile.desktop" \
    > "$HOME/.local/share/applications/rogcontrol-cycle-profile.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
say "App-grid entry installed; the tray starts at login via its own service"

# -------------------------------------------------------------- services ----
step "Installing services"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$SCRIPT_DIR/rogcontrol-apply.service" \
               "$SCRIPT_DIR/rogcontrol-enforcer.service" \
               "$SCRIPT_DIR/rogcontrol-tray.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now rogcontrol-apply.service    >/dev/null 2>&1 || true
# This is also what brings the enforcer back up after it was stopped for the
# library replacement above -- restart starts a stopped unit.
systemctl --user restart      rogcontrol-enforcer.service >/dev/null 2>&1 || true
systemctl --user enable       rogcontrol-enforcer.service >/dev/null 2>&1 || true
say "Boot-reapply and enforcer services enabled"

# The tray used to be a plain XDG autostart entry, which only ever runs once
# at login: a crash (a D-Bus hiccup, the AppIndicator extension not ready
# yet) killed it for the rest of the session with nothing to bring it back.
# A user unit gets the same Restart=on-failure the enforcer already has. Any
# leftover autostart entry from an older install is removed so login never
# starts two trays fighting over the config. Likewise, an update running
# over a tray that was started the old way (nohup, or that autostart entry)
# is not a unit systemd knows about, so "restart" below would not touch it
# and would leave it running alongside the new service-managed one -- killed
# by hand first so there is exactly one tray either way.
rm -f "$HOME/.config/autostart/rogcontrol-autostart.desktop"
pkill -f 'python3? .*/rogcontrol-tray$' 2>/dev/null || true
systemctl --user restart rogcontrol-tray.service >/dev/null 2>&1 || true
systemctl --user enable  rogcontrol-tray.service  >/dev/null 2>&1 || true

# Checked rather than assumed, because this install stopped it on purpose:
# an enforcer that fails to come back is the one failure mode that is
# invisible from the window and only shows up as fan curves quietly drifting.
if systemctl --user is-active --quiet rogcontrol-enforcer.service 2>/dev/null; then
    say "Enforcer is running on the new library"
elif [ "$ENFORCER_WAS_RUNNING" = 1 ]; then
    warn "The enforcer did not come back up after the update."
    warn "  systemctl --user status rogcontrol-enforcer.service"
fi

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

# That the package is importable from where it was just put -- and
# specifically the app module, not just the empty top-level package: app.py
# pulls in every page, and pages/fans.py pulls in widgets/curve_editor.py's
# `import cairo` with it, so this is what actually catches a dependency the
# DEPS list above missed (pycairo has done exactly this once already).
# Deliberately not a --self-test: that builds every page and needs a
# display, which an install run from a TTY does not have, and a plain
# `import rogcontrol.app` needs none either -- GTK/Adw classes are defined
# at import time, not realized against a display until something is shown.
#
# Skipped entirely when packages have been layered but not yet rebooted
# into, where a failure would mean nothing except that the reboot has not
# happened yet.
if [ "$PENDING_REBOOT" = 1 ]; then
    warn "Skipping the import check until after the reboot"
elif eval "env PYTHONPATH=\"$HOME/.local/lib\" python3 -c \"import rogcontrol.app\"" >/dev/null 2>&1; then
    say "Application package imports from ~/.local/lib"
else
    warn "The installed package does not import - the window will not start."
    warn "Run this to see why: PYTHONPATH=~/.local/lib python3 -c 'import rogcontrol.app'"
fi

# Exec=rogcontrol in the .desktop file resolves through PATH, and the launcher
# is useless from a terminal otherwise too. Most desktops add ~/.local/bin,
# but not all of them do.
if [ "$(command -v rogcontrol 2>/dev/null)" = "$HOME/.local/bin/rogcontrol" ]; then
    say "Launcher is on PATH"
else
    warn "~/.local/bin is not on your PATH, so 'rogcontrol' will not be found."
    warn "Add it in ~/.profile (or your shell's rc file) and log back in:"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
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
[ -e "$ASUS_DIR/panel_od" ] && f=1 || f=0
cap "Panel overdrive" $f panel_od "asus-wmi does not expose it"

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

# --- fresh install: create the settings file and put it on the hardware ------
# Without this a fresh install leaves no config at all, and the boot-apply
# service returns immediately because there is nothing to apply -- so a new
# user gets whatever the firmware happened to be doing until they open the
# window. Creating it here means the stock profiles exist and the chosen one
# is actually running the moment the install finishes.
#
# load_config() writes the defaults out when the file is absent, and leaves an
# existing file untouched, so this is safe to run unconditionally.
if [ ! -f "$APP_CONFIG" ]; then
    if PYTHONPATH="$HOME/.local/lib" python3 -c "
from rogcontrol import config, hardware
limits = hardware.detect_gpu_limits()
config.save_config(config.load_config(gpu_min_w=limits['min_w'], gpu_max_w=limits['max_w']))
" 2>/dev/null
    then
        say "Default settings written to $(basename "$APP_CONFIG")"
        # Applying takes about 20 seconds, most of it the mandatory 8-second
        # gaps between fan channels, so it runs in the background rather than
        # holding the installer open.
        ("$HOME/.local/bin/rogcontrol-apply.py" >/dev/null 2>&1 &) 2>/dev/null \
            || (python3 "$HOME/.local/bin/rogcontrol-apply.py" >/dev/null 2>&1 &)
        say "Applying the default profile in the background (about 20 seconds)"
    else
        warn "Could not write the default settings - open the app once to create them"
    fi
fi

# --- record state for next time ---------------------------------------------
mkdir -p "$STATE_DIR"
{
    echo "version=$VERSION"
    echo "installed_at=$(date -Is)"
    # So the run after the reboot below can tell it is finishing an install
    # rather than starting one.
    echo "pending_reboot=$PENDING_REBOOT"
    printf '%s' "$CAP_STATE"
} > "$STATE_FILE"
say "Install state recorded (next run will detect this as an update)"

# Ship the uninstaller next to everything else so it is findable later.
install -m 755 "$SCRIPT_DIR/uninstall.sh" "$HOME/.local/bin/rogcontrol-uninstall.sh" 2>/dev/null \
    && say "Uninstaller available: ~/.local/bin/rogcontrol-uninstall.sh"

# --- confirm the tray ---------------------------------------------------------
# rogcontrol-tray.service was already started and enabled in the services
# step above. Checked here rather than assumed, same reasoning as the
# enforcer check: a tray that fails to launch (no AppIndicator support in
# the session, e.g. a desktop with no such extension) is silent everywhere
# except a status check like this one.
if [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
    say "No graphical session here - the tray starts at your next login"
elif systemctl --user is-active --quiet rogcontrol-tray.service 2>/dev/null; then
    say "Tray running - look for the icon in your status area"
else
    warn "The tray did not stay running. Try it by hand to see why:"
    warn "  ~/.local/bin/rogcontrol-tray"
    warn "Or check:  systemctl --user status rogcontrol-tray.service"
fi

# The library above is necessary but not sufficient, and which half is
# missing depends on the desktop rather than the distro -- so this is asked
# of the session, and reads the same on Bazzite and on CachyOS.
case "$DESKTOP" in
gnome)
    # GNOME Shell has no StatusNotifierItem support of its own. Without the
    # extension the tray process starts, stays running, and shows nothing --
    # which looks exactly like the app being broken, and is the one failure
    # here that a status check cannot see.
    if gnome-extensions list 2>/dev/null | grep -qi appindicator; then
        say "GNOME AppIndicator extension present - the tray icon can show"
    else
        warn "GNOME needs an extension before any tray icon can appear:"
        warn "  'AppIndicator and KStatusNotifierItem Support'"
        if [ "$ATOMIC" = 1 ]; then
            warn "  Bazzite ships it - enable it in the Extensions app."
        else
            warn "  Install it from extensions.gnome.org, or your distro's"
            warn "  gnome-shell-extension-appindicator package, then log back in."
        fi
        warn "Everything else works without it; only the icon is missing."
    fi
    ;;
kde)
    say "KDE Plasma shows tray icons natively - no extension needed"
    ;;
esac

echo
if [ "$PENDING_REBOOT" = 1 ]; then
    echo "Almost done - one reboot to go."
    echo
    echo "  Everything is installed: the helper, the services, your settings,"
    echo "  the menu entry. The only thing missing is the GTK libraries that"
    echo "  were layered a moment ago, and rpm-ostree cannot make those live"
    echo "  in a running system - they arrive with the next boot."
    echo
    echo "    1. Reboot"
    echo "    2. Run ./install.sh again from this same folder"
    echo
    echo "  The second run keeps every setting, skips everything already done,"
    echo "  and will not ask you anything again. Your profiles are already"
    echo "  applied and the background services are already running, so the"
    echo "  hardware is under control in the meantime either way."
else
    echo "Done."
    echo "  Launch from the app grid ('ROG Control'), or just: rogcontrol"
    echo "  The tray icon is running now (see above) and starts at every login;"
    echo "  the window opens from it."
    echo "  Services:  systemctl --user status rogcontrol-enforcer.service"
    echo "             systemctl --user status rogcontrol-tray.service"
fi
echo
echo "  Optional keyboard shortcuts (bind in your desktop settings):"
echo "    ~/.local/bin/rogcontrol-cycle-profile.py"
echo "    ~/.local/bin/rogcontrol-cycle-kbdlight.py"
echo "    ~/.local/bin/rogcontrol-adjust-kbdbrightness.py up|down"
echo "    ~/.local/bin/rogcontrol-adjust-kbdspeed.py up|down"
echo "    rogcontrol --show   (bring the window up)"
echo "    rogcontrol --hide   (put it away without quitting)"
