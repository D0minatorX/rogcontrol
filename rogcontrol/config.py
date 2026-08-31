"""The user's config file: defaults, forward-migration, and disk I/O.

Extracted from the GTK3 app so the helper scripts, the enforcer service and
the tests can all read the same config the same way. Standard library plus
the profile tables -- no GTK, no hardware access.

The file lives at CONFIG_PATH and holds everything the user has set: their
profiles, which one is current, charge limit, window size. It is the only
thing here that cannot be regenerated, so the rules are conservative:
migration never overwrites a value, a save never truncates the old file, and
a config that cannot be parsed is moved aside rather than replaced.
"""

import json
import os
import stat
import sys
import tempfile
import time

from .profiles import (DEFAULT_PROFILES, default_kbd_color,
                       tailored_default_profiles)

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")

# Stamped into the config file. Nothing reads it yet: it exists so a future
# release that needs a genuine one-time step -- a rename or a split, something
# that cannot be detected by looking at the file -- has something to gate on.
CONFIG_VERSION = 1

DEFAULT_CONFIG = {
    "current_profile": "Balanced Performance",
    "kbd_brightness": 2,
    "charge_limit": 100,
    "ac_profile": "Performance",
    "battery_profile": "Quiet",
    # None, not a stock profile: unlike ac_profile/battery_profile this is a
    # refinement most machines have no USB-C PD supply to ever trigger, so
    # defaulting it to a profile would be a switch nobody asked for on
    # hardware that happens to expose the node.
    "usbc_profile": None,
    "window_size": [600, 700],
    "fan_display_unit": "percent",
    # Whether the app has already put the fan-calibration prompt in front of
    # the user once. Set the first time it is shown, whether they run it or
    # dismiss it -- this gates a one-time nudge, not a recurring nag, so a
    # config with it already True (an update, or a config carried over from
    # a backup) must never be re-prompted just because fan_rpm_cal happens
    # to still be missing.
    "fan_calibration_prompted": False,
    # The version stamp of the last hardware report written unasked on first
    # launch (empty string means none yet). Not a bool: a report written by
    # 1.0.0.7 says nothing about what 1.0.0.8 added to it, so an upgrade has
    # to be able to tell "already written this version" from "written an
    # older one" and write a fresh copy either way. Only ever consulted on a
    # non-AMD CPU -- see app.py's first-launch check -- and only ever set by
    # it, never read anywhere else.
    "hardware_report_written": "",
    # The charger-connect flash: opt-in, and its colour. Top-level rather
    # than inside the kbd_rgb block because that block is rebuilt from the
    # keyboard page's widgets on every save (see kbdcolor.merge_kbd_rgb) and
    # only carries the handful of keys listed in CARRIED_KEYS across; these
    # two are a feature toggle in the same shape as boot_sound and panel_od,
    # and they are read by the enforcer, which never touches kbd_rgb at all.
    #
    # False, so an existing config does not start blinking after an update.
    # The colour is materialised even while the flash is off, so the page has
    # a concrete swatch to put in front of the user before they enable it.
    "charger_flash": False,
    "charger_flash_color": [0, 255, 255],
    # Crash-loop breaker for the CPU undervolt (coall) -- see the block
    # below this dict. clean_shutdown defaults True so a fresh install (or
    # a config predating this key) is never treated as coming back from a
    # crash it never had.
    "boot_fail_count": 0,
    "safety_tripped": False,
    "clean_shutdown": True,
    # How often the System page checks GitHub for a newer release on its
    # own, unasked: "off", "launch" (once per window open) or "daily" (at
    # most once every 24h, tracked by last_update_check below). Off by
    # default -- a fresh install or an update to this version must not
    # start making unsolicited network calls that were not there before.
    "update_check": "off",
    "last_update_check": 0,
}


# The "leave it alone" entry in the two auto-switch pickers. It is a label,
# not a profile: choosing it stores null, and null is what every reader
# treats as "do not switch on this power source". Storing the label itself
# would name a profile that does not exist, and the switch would look
# configured while doing nothing.
NO_AUTO_SWITCH = "Don't auto-switch"

# Config keys the pickers write, by power source.
AUTO_SWITCH_KEYS = {"ac": "ac_profile", "battery": "battery_profile",
                    "usbc": "usbc_profile"}


def auto_switch_choices(cfg):
    """The picker's entries: the no-op first, then every profile."""
    profiles = cfg.get("profiles")
    names = list(profiles) if isinstance(profiles, dict) else []
    return [NO_AUTO_SWITCH] + names


def auto_switch_selected(cfg, key):
    """Index into auto_switch_choices for what is stored under ``key``.

    A stored profile that no longer exists -- renamed or deleted since it
    was chosen -- selects the no-op rather than nothing at all, so the
    picker cannot come up blank."""
    choices = auto_switch_choices(cfg)
    stored = cfg.get(key)
    return choices.index(stored) if stored in choices[1:] else 0


def auto_switch_value(label):
    """What to store for a chosen label: None for the no-op entry."""
    return None if label == NO_AUTO_SWITCH else label


# --- creating, deleting and moving profiles between machines ----------------
#
# All pure: a config dict in, a config dict edited in place, no I/O and no
# GTK. The window is a thin shell over these -- it asks for a name, shows a
# confirmation and picks a file, and every rule about what is allowed lives
# here where it can be tested.

# Stamped into an exported file. The format is deliberately "a dict with a
# profiles key", the same shape the config itself uses, so a whole config
# file is also a valid import and a hand-edited export cannot end up in some
# second, subtly different dialect.
PROFILE_FILE_VERSION = 1

# The sections a thing has to have at least one of before it is a profile
# rather than an arbitrary JSON object that happens to be in the file.
PROFILE_SECTIONS = ("cpu", "gpu", "fans")


def profile_name_error(cfg, name):
    """Why ``name`` cannot be a new profile, or None if it can.

    Duplicates are refused rather than silently overwritten: the name is the
    only handle the user has on a profile, and a "New" that quietly replaced
    the curves behind an existing name would destroy tuning with no warning
    and no undo."""
    name = (name or "").strip()
    if not name:
        return "A profile needs a name."
    profiles = cfg.get("profiles")
    if isinstance(profiles, dict) and name in profiles:
        return f"There is already a profile called “{name}”."
    return None


def free_profile_name(cfg, base):
    """``base``, or the first "base (2)", "base (3)" … that is free."""
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or base not in profiles:
        return base
    n = 2
    while f"{base} ({n})" in profiles:
        n += 1
    return f"{base} ({n})"


def create_profile(cfg, name, template=None):
    """Add ``name`` as a copy of ``template`` and make it current.

    ``template`` defaults to the current profile, so a new profile starts as
    the machine is running right now -- which is what makes "New" a way to
    branch off a profile you have just tuned, rather than a way to get an
    empty one you then have to fill in from nothing.

    The copy is deep. Sharing the sub-dicts would make editing the new
    profile's fan curve edit the one it was copied from.

    Raises ValueError with a message meant for the user."""
    error = profile_name_error(cfg, name)
    if error:
        raise ValueError(error)
    name = name.strip()
    profiles = cfg.setdefault("profiles", {})
    if template is None:
        template = profiles.get(cfg.get("current_profile")) or {}
    profiles[name] = json.loads(json.dumps(template))
    cfg["current_profile"] = name
    return name


def delete_profile(cfg, name):
    """Remove ``name``, leaving the config consistent. Returns the profile
    that is current afterwards.

    Three rules, all of them things the GTK3 version got wrong or did not
    consider:

    * The last profile cannot be deleted. A config with no profiles has
      nothing to apply and nothing to show, and the next migration would
      quietly hand back the stock four as if the user's had never existed.
    * ``current_profile`` must still name a profile that exists. Deleting a
      profile that is not the current one therefore leaves the current one
      alone -- the old version moved the user to the first profile in the
      list whichever one they deleted, so tidying up an unused profile
      silently switched the machine.
    * ``ac_profile``/``battery_profile`` pointing at the deleted profile are
      cleared. Left behind, they name something that no longer exists: the
      picker on the Battery page falls back to "Don't auto-switch" while the
      file still says otherwise, so the next person to read the file (or the
      next export) sees a switch configured to a ghost.

    Raises ValueError with a message meant for the user."""
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or name not in profiles:
        raise ValueError(f"There is no profile called “{name}”.")
    if len(profiles) <= 1:
        raise ValueError("This is the only profile left — there has to be "
                         "one. Create another first.")
    del profiles[name]
    if cfg.get("current_profile") not in profiles:
        cfg["current_profile"] = next(iter(profiles))
    for key in AUTO_SWITCH_KEYS.values():
        if cfg.get(key) == name:
            cfg[key] = None
    return cfg["current_profile"]


def export_payload(cfg, names):
    """What Export writes: the named profiles, in a dict keyed ``profiles``.

    Deep-copied, so the file being serialised cannot be changed under the
    writer by a page editing the live config."""
    profiles = cfg.get("profiles") or {}
    picked = {name: profiles[name] for name in names if name in profiles}
    return {
        "rogcontrol_profile_version": PROFILE_FILE_VERSION,
        "profiles": json.loads(json.dumps(picked)),
    }


def parse_import(data):
    """Validate a loaded profile file and return ``{name: profile}``.

    The file came from wherever the user pointed the file chooser, so it is
    checked rather than trusted: this is the one path by which arbitrary
    JSON can reach the config, and a config the app cannot read is the
    user's profiles gone. Everything that is not obviously a profile set is
    refused *before* anything is merged.

    Sections the file omits are filled in from the stock Balanced
    Performance profile, so a partial profile cannot produce an entry the
    pages then have to guard every lookup against.

    Raises ValueError with a message meant for the user."""
    if not isinstance(data, dict):
        raise ValueError("this is not a profile file (the top level is not "
                         "a JSON object)")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("this file has no “profiles” section")
    if not profiles:
        raise ValueError("this file contains no profiles")
    clean = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("this file has a profile with no name")
        if not isinstance(profile, dict):
            raise ValueError(f"“{name}” is not a profile")
        if not any(section in profile for section in PROFILE_SECTIONS):
            raise ValueError(f"“{name}” has no cpu, gpu or fans section")
        profile = json.loads(json.dumps(profile))
        base = DEFAULT_PROFILES["Balanced Performance"]
        for section in PROFILE_SECTIONS:
            if not isinstance(profile.get(section), dict):
                profile[section] = json.loads(json.dumps(base[section]))
        clean[name.strip()] = profile
    return clean


def import_profiles(cfg, data):
    """Merge a profile file into ``cfg``. Returns the names as imported.

    Nothing in ``cfg`` is touched until the whole file has validated, so a
    file that is half profiles and half junk changes nothing at all rather
    than leaving the config partly merged.

    An imported profile whose name is taken comes in as "Name (2)" instead
    of replacing what is there. Import is reached from a file chooser, where
    the file's contents are not on screen; silently overwriting a tuned
    profile because a stranger's export happened to use the same name is not
    something the user could have seen coming, and there is no undo."""
    incoming = parse_import(data)          # raises before anything is touched
    profiles = cfg.setdefault("profiles", {})
    imported = []
    for name, profile in incoming.items():
        name = free_profile_name(cfg, name)
        profiles[name] = profile
        imported.append(name)
    return imported


# Stamped only into a full backup, so Import can tell "everything, replace
# it" apart from an old-style file that shares one or more profiles to be
# merged in safely. Its absence, not its value, is what Import checks against
# -- there is deliberately no migration chain here either, for the same
# reason CONFIG_VERSION has none: nothing has needed one yet.
BACKUP_MARKER = "rogcontrol_backup_version"
BACKUP_FILE_VERSION = 1


def export_backup(cfg):
    """What Export writes: the whole config, not one profile.

    Every profile, which one is current, the charge limit, the keyboard
    settings, the AC/battery auto-switch targets, fan RPM calibration --
    everything that used to be left out. Deep-copied for the same reason
    export_payload is: the file being serialised must not change under the
    writer while a page edits the live config."""
    backup = json.loads(json.dumps(cfg))
    backup[BACKUP_MARKER] = BACKUP_FILE_VERSION
    return backup


def is_backup_file(data):
    """True for a file export_backup wrote, False for anything else --
    including an old single-profile export, which has no marker and is
    still a valid (safer, merge-only) import."""
    return isinstance(data, dict) and BACKUP_MARKER in data


def parse_backup(data):
    """Validate a full backup file. Raises ValueError with a user message.

    Lighter than parse_import on purpose: a backup came from this app's own
    Export, not a stranger's hand-edited file, so it is checked for being
    *readable* rather than picked apart section by section."""
    if not isinstance(data, dict):
        raise ValueError("this is not a backup file (the top level is not "
                         "a JSON object)")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("this backup has no profiles")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("this backup has a profile with no name")
        if not isinstance(profile, dict):
            raise ValueError(f"“{name}” is not a profile")
    return data


def restore_backup(cfg, data):
    """Replace ``cfg`` in place with a full backup's contents.

    Unlike import_profiles, this REPLACES rather than merges: restoring a
    backup means going back to exactly that saved state, not layering it on
    top of whatever profiles happen to be here now. There is no undo, which
    is why the window confirms with the user before calling this -- by the
    time this function runs, that choice has already been made.

    migrate_config runs over the result so a backup taken on an older
    version still comes back with every key this version expects."""
    backup = parse_backup(data)
    cfg.clear()
    for key, value in backup.items():
        if key == BACKUP_MARKER:
            continue
        cfg[key] = json.loads(json.dumps(value))
    return migrate_config(cfg)


# How often an open window re-checks the config file. The window is not the
# only writer -- the enforcer's AC auto-switch and its power-mode adoption,
# the tray and the hotkey cycler all write it -- and a window that loaded the
# file once at startup and then wrote its whole in-memory copy back on every
# slider would silently revert every one of them.
CONFIG_POLL_SECONDS = 5


def config_mtime():
    """When the config file was last written, or None if it cannot be asked."""
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return None


def config_file_moved_on(last_mtime, mtime):
    """True when the config has been written since we last looked at it.

    ``last_mtime`` None is the first sample: record it and read nothing. The
    window has just loaded that exact file, so treating it as a change would
    make every startup reload the pages for no reason.

    ``mtime`` None means the file is not there (or unreadable) right now.
    That is not a change either -- there is nothing to re-read, and the copy
    in memory is the better of the two."""
    if last_mtime is None or mtime is None:
        return False
    return mtime != last_mtime


def reload_decision(current, fresh):
    """What a config written by something else means for an open window.

    Returns ``(profile_changed, contents_changed)``; both False means the
    file moved but says nothing new to us -- which is the common case,
    because the window's own saves change the mtime too.

    The profile NAME staying the same does not mean its contents did: the
    enforcer, the hotkey cycler and an import can all rewrite curves and
    limits under the same name. A window that only watched the name kept
    showing what it loaded at startup, so the graphs said one thing while
    the hardware did another -- and the next Apply pushed the stale picture
    back over the newer values."""
    name = fresh.get("current_profile")
    profile_changed = name != current.get("current_profile")
    contents_changed = (
        not profile_changed
        and (fresh.get("profiles") or {}).get(name)
        != (current.get("profiles") or {}).get(name))
    return profile_changed, contents_changed


# --- where the result of a deferred apply may be written ---------------------
#
# Every Apply in this app is deferred, and the fan page's is deferred by
# about ten seconds: three curve writes CHANNEL_GAP_S apart, because the EC
# can drop curves fired too close together. Pressing Apply and learning
# the answer are therefore about ten seconds apart, and the active profile can
# move in between -- the user can pick another one, so can the tray and the
# hotkey cycler, and the enforcer does it unprompted when the charger comes
# out or the OS power mode changes.
#
# So "which profile is current?" asked when the work FINISHES answers a
# different question from the one the user asked when they pressed the
# button, and the answer is written over a profile they were never editing.
# That is not a hypothetical: it silently collapsed four deliberately
# different fan curves into nearly the same one and destroyed the real
# settings. Capture the name at press time; decide here.

SAVE_OK = "ok"
SAVE_PROFILE_CHANGED = "profile-changed"
SAVE_PROFILE_GONE = "profile-gone"
SAVE_NO_PROFILE = "no-profile"


def deferred_save_target(cfg, captured_name):
    """Where a background apply's result may be saved, if anywhere.

    ``captured_name`` is the profile that was active when the user pressed
    Apply. Returns ``(SAVE_OK, profile_dict)`` when writing is safe, or
    ``(<a refusal status>, None)`` when it is not.

    A profile that moved is deliberately not guessed at from either end.
    The result belongs to the profile the user was editing, which they may
    no longer be looking at, and it plainly does not belong to the one that
    is current now -- writing it there is the data loss. Neither is written,
    and the caller is expected to say so rather than fail silently: the
    settings did reach the hardware, they just are not saved.

    The profile is looked up by NAME here rather than the caller keeping the
    dict it had at press time, and that matters as much as the name check.
    A window that follows an external config write does
    ``config.clear()``/``update()``, which replaces every profile object in
    it; a reference captured before that is still writable and still saves
    -- into an orphan dict that nothing will ever read back."""
    if not captured_name:
        return SAVE_NO_PROFILE, None
    if cfg.get("current_profile") != captured_name:
        return SAVE_PROFILE_CHANGED, None
    profile = (cfg.get("profiles") or {}).get(captured_name)
    if not isinstance(profile, dict):
        # Renamed, deleted or imported over while the apply was running.
        return SAVE_PROFILE_GONE, None
    return SAVE_OK, profile


def deferred_save_refusal(status, captured_name, what, where="the hardware"):
    """The sentence a page shows when the save above was refused.

    One wording, in one place, because all three Apply buttons have to tell
    the user the same true and slightly awkward thing: the settings are on
    the machine, and they are not in the config."""
    tail = f"the {what} were written to {where} but not saved"
    if status == SAVE_PROFILE_CHANGED:
        return f"Profile changed while applying; {tail} to {captured_name}."
    if status == SAVE_PROFILE_GONE:
        return f"{captured_name} no longer exists; {tail}."
    return f"No profile was active; {tail}."


def save_deferred(cfg, captured_name, section, values, what,
                  where="the hardware", path=None):
    """Merge a finished apply's ``values`` into one section of the profile it
    was started from, and save. The whole write, so that the rule above
    cannot be enforced in one page and forgotten in the next.

    ``section`` is "fans", "cpu" or "gpu"; ``values`` is what actually
    reached the hardware, so a setting the machine refused is never recorded
    as fact. Returns None when the config was written, or the sentence for
    the user when the write was refused -- refused meaning the profile moved
    while the apply was running, in which case NOTHING is written: not the
    profile the user was editing, which they may have left, and above all
    not the one that is current now, which they never touched.

    Empty ``values`` saves nothing and is not an error: every step can have
    failed, or the only step that took (EPP) may keep nothing in the
    config."""
    status, profile = deferred_save_target(cfg, captured_name)
    if status != SAVE_OK:
        return deferred_save_refusal(status, captured_name, what, where)
    if values:
        profile.setdefault(section, {}).update(values)
        save_config(cfg, path)
    return None


def migrate_config(cfg, gpu_min_w=1, gpu_max_w=140):
    """Bring a config from any older version up to date WITHOUT touching
    anything the user has set.

    Only missing keys are filled in. Every existing value is left exactly as
    it is, including ones this version doesn't recognise -- an unknown key
    is more likely to be from a newer build than junk, and silently dropping
    it would lose the user's settings on a downgrade/upgrade cycle.

    ``gpu_min_w``/``gpu_max_w`` describe the card actually fitted and are
    used only when stock profiles are created from scratch. They are
    arguments rather than module globals because nothing in here may depend
    on hardware detection having run; the defaults describe the 140W card
    the built-in numbers were chosen against.

    There is deliberately no migration chain here. config_version is only a
    stamp, so that a future release which needs a genuine one-time step -- a
    rename, a split, anything that cannot be detected from the file itself --
    has something to gate on."""
    for key, value in DEFAULT_CONFIG.items():
        # Deep-copied per config: window_size is a list, and handing the same
        # object to every config would let one of them edit the defaults.
        cfg.setdefault(key, json.loads(json.dumps(value)))

    # Profiles: keep the user's, add the stock ones only if there are none
    # at all. A user who deleted "Quiet" should not silently get it back.
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        # Only reached on a fresh install (or a config with no profiles at
        # all), so this is the one place hardware-tailored defaults apply.
        # An update never gets here, which is what keeps existing profiles
        # from being replaced.
        cfg["profiles"] = tailored_default_profiles(gpu_min_w, gpu_max_w)
    else:
        # Fill in only sections a profile is missing entirely, so profiles
        # written by an older version still load.
        for name, prof in profiles.items():
            if not isinstance(prof, dict):
                continue
            base = DEFAULT_PROFILES.get(name) or DEFAULT_PROFILES["Balanced Performance"]
            for section in ("cpu", "gpu", "fans"):
                if section not in prof:
                    prof[section] = json.loads(json.dumps(base[section]))

    # Every profile gets the keyboard colour Profile Color paints it in, if
    # it has not got one already. Outside the two branches above rather than
    # inside either, because both need it: a fresh install has just been
    # handed the stock profiles, and an existing config has profiles that
    # predate the key entirely. Nothing here overwrites a colour the user has
    # set -- setdefault, like every other line in this function.
    #
    # Materialising it (rather than leaving kbdcolor to fall back on read)
    # is what puts a concrete swatch in front of the user to change. The
    # fallback stays anyway, for a profile imported after this ran.
    for _name, _prof in cfg["profiles"].items():
        if isinstance(_prof, dict):
            _prof.setdefault("kbd_color", default_kbd_color(_name))

    # current_profile must name a profile that exists
    if cfg.get("current_profile") not in cfg["profiles"]:
        cfg["current_profile"] = next(iter(cfg["profiles"]))

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config(path=None, gpu_min_w=1, gpu_max_w=140):
    """Load the user's config, migrating it forward in place. A config that
    cannot be parsed is preserved as a .corrupt-<timestamp> copy rather than
    being silently replaced -- the previous behaviour overwrote it on the
    next save, destroying the user's profiles with no way back.

    ``path`` defaults to CONFIG_PATH, and is read when called rather than
    bound into the signature so that tests (and anything else with its own
    config) can point this somewhere else.

    ``gpu_min_w``/``gpu_max_w`` are passed straight through to
    migrate_config, and matter only the one time it creates the stock
    profiles from scratch: a caller that has already asked the real card
    (hardware.detect_gpu_limits) gets a fresh install tailored to it
    instead of the numbers this was written against. Callers with no such
    answer -- the hotkey scripts, the tests -- are unaffected by leaving
    these at their defaults, which are exactly migrate_config's own."""
    path = CONFIG_PATH if path is None else path
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return migrate_config(cfg, gpu_min_w=gpu_min_w, gpu_max_w=gpu_max_w)
            raise ValueError("config is not a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            stamp = int(time.time())
            backup = f"{path}.corrupt-{stamp}"
            # Two failures in the same second must not overwrite the first
            # backup -- that would lose the very thing we are saving.
            n = 1
            while os.path.exists(backup):
                backup = f"{path}.corrupt-{stamp}-{n}"
                n += 1
            try:
                os.replace(path, backup)
                print(f"rogcontrol: could not read config ({e}); "
                      f"kept a copy at {backup}", file=sys.stderr)
            except OSError:
                pass
    return migrate_config({}, gpu_min_w=gpu_min_w, gpu_max_w=gpu_max_w)


def update_config(mutate, path=None):
    """Read the config, apply ``mutate`` to it, write it back.

    For everything that changes ONE setting: the hotkey scripts, the tray,
    anything that is not the window holding the whole config in memory. What
    it buys over load-mutate-save spelled out by hand is that the read
    happens immediately before the write, so the only gap another process
    can land in is the ``mutate`` call itself.

    That gap used to be a whole hardware write wide. The keyboard scripts
    read the config, spent a second or two in the helper, then wrote their
    whole stale copy back -- so a profile switch, a charge limit or a curve
    saved in between was silently thrown away. The enforcer already re-read
    before saving for exactly this reason; this is that, in one place.

    ``mutate`` is called with the config dict and edits it in place; its
    return value is ignored. Returns the config that was written."""
    cfg = load_config(path=path)
    mutate(cfg)
    save_config(cfg, path=path)
    return cfg


def save_config(cfg, path=None):
    """Write the config out atomically.

    The new file is written alongside the old one and renamed over it, so a
    reader only ever sees the complete old config or the complete new one.
    Writing in place -- which this used to do -- truncates the file the
    moment it is opened, so a crash, a full disk or a value that will not
    serialise left the user with an empty or half-written config and no
    profiles."""
    path = CONFIG_PATH if path is None else path
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # A unique temporary file, not a fixed "<path>.tmp". Five processes save
    # this config -- the window, the tray, the hotkey cycler, the boot apply
    # and the enforcer -- and two of them saving at once would open, truncate
    # and write the same temp file, then rename the interleaved result over
    # the user's config. mkstemp in the same directory keeps the rename on
    # one filesystem, which is what makes it atomic.
    fd, tmp = tempfile.mkstemp(dir=directory,
                               prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
            # The rename is only atomic with respect to the file's contents
            # if those contents have actually reached the disk first.
            f.flush()
            os.fsync(f.fileno())
        # mkstemp creates 0600. Carry the real config's own permissions over
        # so saving cannot quietly change them; a config that does not exist
        # yet keeps the private 0600, which is the safer of the two.
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        # Never leave a half-written .tmp sitting next to the real config.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --- crash-loop breaker for the CPU undervolt --------------------------------
#
# A bad all-core Curve Optimizer value (coall) can freeze the machine, and
# the login apply and the enforcer both re-assert the current profile's
# undervolt unconditionally -- so a value that freezes the machine on this
# boot freezes it again on the next one, forever, with no window ever open
# long enough to change it back.
#
# The break is a streak counter that survives across boots. It cannot know a
# freeze happened -- nothing survives that to report it -- so it infers one
# from its absence: a boot counts as a crash unless something recorded that
# THIS one ended cleanly, either by running long enough to prove itself
# (mark_boot_survived, from a detached watchdog the login apply starts) or by
# shutting down on its own rather than freezing (mark_clean_shutdown, from
# the enforcer's SIGTERM handler). Two such unproven boots in a row and the
# undervolt is forced to stock until the user acknowledges it.

# How many boots in a row with no proof of survival before the undervolt is
# forced to stock. Not 1: a single reboot for an unrelated reason (a BIOS
# update, a kernel upgrade) must not look identical to a crash loop.
BOOT_FAIL_THRESHOLD = 2

# How long a boot has to run, uninterrupted, before it counts as proof the
# current undervolt is safe. Long enough to cover instability that only
# shows up under sustained load, not just an instant freeze on apply.
BOOT_SURVIVAL_SECONDS = 20 * 60


def record_boot_attempt(cfg):
    """Update the crash-loop counters for one login apply. Mutates ``cfg``.

    Must be called -- and the result saved to disk -- BEFORE the undervolt
    is written to the chip, so that if this boot freezes right there, the
    incremented count has already survived to be read on the next one.

    Returns True when the undervolt should be forced to stock this boot
    rather than the profile's own value."""
    if cfg.get("clean_shutdown", True):
        cfg["boot_fail_count"] = 0
    else:
        cfg["boot_fail_count"] = cfg.get("boot_fail_count", 0) + 1
    # Unproven until this boot's own watchdog or shutdown hook says
    # otherwise -- so a second freeze before either fires still counts.
    cfg["clean_shutdown"] = False
    if cfg["boot_fail_count"] >= BOOT_FAIL_THRESHOLD:
        cfg["safety_tripped"] = True
    return cfg.get("safety_tripped", False)


def mark_boot_survived(path=None):
    """This boot ran BOOT_SURVIVAL_SECONDS without freezing -- proof the
    current undervolt is safe. Called by the login apply's detached
    watchdog, never inline: nothing that runs inside the login apply itself
    lives long enough to call this."""
    update_config(lambda cfg: cfg.update(boot_fail_count=0), path=path)


def mark_clean_shutdown(path=None):
    """This session ended on its own rather than by freezing. Called from
    the enforcer's SIGTERM handler, so a deliberate reboot or logout before
    the survival watchdog fires is not counted as a crash."""
    update_config(lambda cfg: cfg.update(clean_shutdown=True), path=path)


def clear_safety_trip(cfg):
    """The user has seen the crash-loop banner and asked to try the
    undervolt again. Mutates ``cfg``; the caller still has to save it and
    then actually apply, or the old value just comes back forced to stock
    at the next boot."""
    cfg["safety_tripped"] = False
    cfg["boot_fail_count"] = 0


def stock_cpu_values(cpu):
    """``cpu`` with the undervolt forced off, everything else untouched.

    Used in place of a profile's own cpu section when safety_tripped is
    set: the power limits, boost and clock settings are not what freezes a
    machine, only coall is, so only it needs overriding."""
    values = dict(cpu)
    values["coall"] = 0
    return values
