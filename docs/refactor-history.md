# Historical implementation notes

## app.py

The GTK4/libadwaita application: window, navigation and the apply plumbing.

This is the shell. It owns exactly three things every page depends on and
nothing else:

* ``window.config`` / ``window.caps`` -- the loaded config and the probed
  machine capabilities, read once and shared, so a page never re-reads the
  config file behind another page's back.
* ``window.toast(text)`` -- the single place anything reports success or
  failure. Pages never own a status label.
* ``window.apply_async(fn, on_done)`` -- every hardware call goes through
  here. ``run_helper`` shells out to sudo with a ten second timeout and
  nvidia-smi takes a couple of hundred milliseconds; either on the main loop
  freezes the window, and a frozen window during a fan-curve apply is what
  made the old version feel broken.

The application id must match the desktop entry's filename
(``org.rogcontrol.RogControl.desktop``). GNOME matches a window to its
launcher by application id, and when the two disagree the icon in the
applications grid starts the app but never attaches to the window it
opened -- which looks exactly like clicking the icon doing nothing at all.

## pages/system.py

System: asusd, power-mode sync, the boot sound, the log, and detection.

Read-mostly things, and one conflict worth naming.

The graphics mode picker used to live here and is now on the GPU page. It
belongs there: which card the screen is plugged into is a fact about the
graphics card, and every control that depends on it -- power limit,
temperature target, Dynamic Boost -- is on that page already.

The conflict is asusd. It is asusctl's daemon and it drives exactly the same
hardware as this app: the same asus-wmi platform knobs, the same three custom
fan curves, the same keyboard lighting. Two programs re-asserting different
fan curves at the same embedded controller is not a configuration, and the
fans are where it is audible. So the page says whether asusd is installed and
what it is doing, and can stop and disable it -- or put it back. What it does
not do is remove the package: that is a transaction the user should see, so
the exact command for the detected distro is shown instead.

The sync row exists because this app and the OS both think they own the
power mode. Selecting a profile sets power-profiles-daemon to match (the
window's profile switch pushes the mode before anything else, because
changing it is what wipes the EC's fan curve). GNOME's power menu can set
it back, and until the enforcer notices, the machine is running one thing
and reporting another. Rather than hide that, the page names both sides and
says whether they agree.

What the enforcer then does is adopt, not revert: an externally set mode is
treated as a request to switch profile, so the disagreement is resolved by
this app moving to the profile that mode maps to. The row used to tell the
user to "re-select the profile to push it back", which was never possible
-- selecting the profile that is already current is a no-op in the switcher,
by design, since it would otherwise cost a full ~20 second re-apply.

The boot sound is here rather than on a tuning page because it is a property
of the machine and not of how hard it is being driven: the firmware plays it
before any operating system is running, and switching profile must not change
it. So it is written straight to the hardware, kept at the top level of the
config rather than inside a profile, and re-asserted at login by the
boot-apply service in case a firmware reset has brought the chime back.

Panel overdrive sits beside it on all four of those counts, which is why it
is here and not on the GPU page. The GPU page is about the discrete card --
what it is allowed to draw, how hot it may get, which card the screen is
plugged into -- and overdrive is none of those: it is the panel's own
response-time setting, written to the same asus-wmi platform device as the
chime, held in firmware, and no more part of a profile than the chime is.

## cli.py

```text
        # One definition of what a CPU apply writes and in what order,
        # shared with the window and the enforcer. It used to be a chain
        # of ifs here as well, and every setting added since has had to
        # be added to each copy by hand -- the clock floor reached three
        # of the four and was silently dropped by the fourth.
```

## cli.py

```text
# The rotation is KBD_RGB_MODES in insertion order, minus the two modes that
# are not effects a hotkey can cycle into.
#
# It used to be a hand-written list here, with a comment saying it "must stay
# in step with KBD_RGB_MODES" and nothing making it so -- and the stated
# consequence was real: a mode present there and missing here makes the
# hotkey jump back to Static instead of advancing, because the saved mode
# name is then not found in this list.
#
# The order that list was written in is the order KBD_RGB_MODES already has,
# including the property its comment claimed: the modes needing no
# temperature reading come first, so the common case does not depend on
# hwmon or nvidia-smi being reachable from a shortcut context.
#
# EXCLUSIVE_MODES is what comes out. Ambient needs a live screen-capture
# session, so it only exists while the main window is running; cycling into
# it from a hotkey would set a mode nothing is driving. Profile Colour is out
# for the opposite reason -- it is not an effect the user picks between, it
# is the keyboard being handed to the profile switcher, which owns it until
# the user takes it back on the Keyboard page. Cycling INTO it would paint
# one colour and look like Static; cycling OUT of it is exactly what should
# happen, and does: the saved name is not in this list, so the next press
# lands on Static and the profile switcher stops painting.
```

## cli.py

```text
# All four the package's. This script had hand-copied numbers, a hand-copied
# SPEED_MODES tuple and its own apply_speed -- four independent transcriptions
# of things kbdcolor owns, and its own comment admitted the tuple was "kept in
# step by hand". Adding a fifth animated mode to kbdcolor now reaches this
# script; before, it silently reported "has no speed to change".
```

## cli.py: apply_speed

Re-send the current mode at a new speed.

kbdcolor.helper_args builds the same call the Keyboard page and the boot
apply send, which is the point: this used to build it here, and the copy
got Breathing's second colour from r2/g2/b2 with no clamping, so a config
a user had edited by hand could reach the helper as a value it refused.

Returns ``(ok, message)``. ``args`` is None only for a mode with no
speed, which main() has already ruled out.

## cli.py: apply_mode

Put ``mode_name`` on the keyboard. Returns ``(ok, message)``.

Every argument comes from kbdcolor now. This function used to build each
call by hand -- the gradient ramp, the pulse speed clamp, the temperature
and battery colour maths were all transcribed here -- alongside its own
read_cpu_temp, read_battery and read_gpu_temp. Two of those transcriptions
had already drifted: the colours were taken from the config unclamped, so
a hand-edited config reached the helper as values it refused, and the
zone ramp rounded independently of gradient_zone_colors.

## cli.py: available_modes

The rotation this machine can actually perform.

Through kbdcolor.supported_modes, the same gate the picker in the window
uses, rather than a second set of rules -- this script had its own copy
of MULTI_ZONE_MODES and of the Aura product-ID table, and a keyboard
added to one would have been missing from the other.

Only the two capabilities that cost a sysfs read are probed. Everything
supported_modes can also gate on (an NVIDIA card, the screen-capture
portal) is left at its default of "present", which keeps this script's
existing behaviour: GPU Temp Color stays in the rotation on a machine
with no card and reports what went wrong when it is reached, rather than
being silently withheld. See pages/keyboard.py for why that is the
doctrine.

## cli.py note

```text
# Every setting this machine has; the helper refuses anything it cannot
# do, and this script has no capability probe of its own. ryzenadj is the
# exception -- see rogcontrol-apply: a chip it cannot talk to is a failed
# call, not a refused one, so the vendor is checked here instead.
# cpu_power_limits is asked about for the same reason and covers Intel's
# ppt/RAPL backend too -- see hardware.cpu_power_limits_backend.
```

## cli.py note

```text
# The package's, so a hotkey that half-applied a profile is announced the
# same way the enforcer announces an automatic switch -- and so this script
# stops carrying a copy that had no -a flag and so showed up unattributed.
```

## cli.py note

```text
        # Asked for, not assumed -- see rogcontrol-apply.py. Nothing catches
        # an exception in this script at all, so a profile with an empty gpu
        # section made the hotkey traceback and stop, leaving the fan curves
        # below unwritten.
```

## cli.py note

```text
        # These must be applied here too. The enforcer only re-asserts them
        # on a full apply now, not every cycle, so switching profiles from
        # this shortcut has to set them itself or they would be left on the
        # previous profile's values.
```

## cli.py note

```text
        # Through the package's own call rather than a hand-built command
        # line: it has a timeout, where this had none. A hotkey that hangs
        # on nvidia-settings is a keypress that never finishes and a profile
        # switch left half-applied.
```

## cli.py note

```text
    # Only the channels whose curve is not already the one the controller is
    # running -- see rogcontrol-apply.py and app.py's _apply_profile_worker.
    # This script used to skip that check and pay the CHANNEL_GAP_S EC gap
    # for all three channels on every switch, including a switch back to a
    # profile whose fans matched exactly -- which is why the notify-send
    # after it felt slow even when nothing about the fans had changed.
```

## cli.py note

```text
    # Not while a fan boost is running -- see rogcontrol-apply.py for why
    # writing the profile's curves over a live boost only makes the two
    # fight until the boost expires.
```

## cli.py note

```text
    # Through config.update_config rather than a bare load/save: the read
    # happens immediately before the write, so a GUI apply, the enforcer's AC
    # auto-switch or the tray landing between the two can no longer be
    # silently overwritten. This script is bound to a keyboard shortcut --
    # the one profile-switch path most likely to be fired twice in a second
    # -- so that gap mattered more here than anywhere else it was fixed.
```

## cli.py note

```text
    # Before applying, and before the fan writes in particular: the OS power
    # mode has to move with the profile or the enforcer switches the profile
    # back within a minute, and changing the mode is what makes the EC drop
    # the custom curve. Returns None, changing nothing, for a profile of the
    # user's own that maps to no OS mode.
```

## cli.py note

```text
    # The keys go with it. This shortcut is the one profile switch that
    # happens with nothing else on screen -- no window, no tray menu, no
    # notification yet -- so the keyboard changing colour is often the only
    # confirmation the user gets before the notify-send lands. Returns None
    # and writes nothing unless the saved lighting mode is Profile Color.
```

## cli.py note

```text
    # Guarded, because the config already says this profile is current --
    # it is saved above so the enforcer does not switch it straight back --
    # and an exception here would leave that claim standing over a machine
    # the settings never finished reaching, with a bare traceback on a
    # stderr no one is reading and no notification at all. The empty-gpu
    # KeyError that used to do exactly that is fixed, but it was only ever
    # one way in.
```

## cli.py note

```text
    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
```

## cli.py note

```text
    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
```

## cli.py note

```text
    # config.load_config, not a bare json.load with a fallback to {}. That
    # fallback wrote its near-empty dict straight back over an unparseable
    # config, destroying every profile in it; load_config keeps a
    # .corrupt-<timestamp> copy instead and hands back a fresh default.
```

## cli.py note

```text
        # The mode key on its own, in a freshly read config -- not the whole
        # kbd_rgb block read above. The helper call in between is long enough
        # for the speed hotkey or the Keyboard page to have written that
        # block, and putting the older copy back reverted their change.
```

## cli.py note

```text
        # Re-read and write in one step rather than saving the copy read
        # above: the helper call between the two is long enough for the
        # window, the tray or the enforcer to have written the file, and
        # writing the older copy back threw away whatever they changed.
```

## cli.py note

```text
        # The speed key on its own, in a freshly read config -- not the whole
        # kbd_rgb block read above. The helper call in between is long enough
        # for the kbdlight cycler or the Keyboard page to have written that
        # block, and putting the older copy back reverted their mode or
        # colour along with it.
```

## cli.py note

```text
        # With the reason: see rogcontrol-adjust-kbdbrightness.py.
```

## cli.py note

```text
        # With the reason, not just "failed". This is the only channel
        # this script has, and a hotkey that says nothing but "failed" is
        # one the user cannot act on.
```
