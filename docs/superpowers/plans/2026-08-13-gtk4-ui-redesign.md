# ROG Control GTK4 UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GTK3 notebook interface with a GTK4/libadwaita application, and extract the logic the app and its four helper scripts currently duplicate into shared modules.

**Architecture:** A `rogcontrol` Python package installed to `~/.local/lib`, containing four dependency-free logic modules (config, hardware, fancurve, profiles) and a GTK4 UI layer. The existing enforcer, boot-apply and hotkey scripts import the logic modules instead of carrying copies. The tray icon moves to a small GTK3 sidecar process, because AppIndicator links Gtk-3.0 and GTK3 cannot share a process with GTK4.

**Tech Stack:** Python 3.14, PyGObject 3.56, GTK 4.22, libadwaita 1.9, stdlib `unittest` (no new test dependency), GStreamer for the ambient sampler.

## Global Constraints

- Target GTK 4.22+ and libadwaita 1.9+. Every widget used must exist in those versions.
- Tests use stdlib `unittest` only. Do not add pytest or any runtime dependency.
- The config file format does not change. `CONFIG_VERSION` stays `1`.
- The privileged helper `/usr/local/bin/rogcontrol-helper` and its sudoers rule are not modified by this work.
- The fan apply sequence keeps its 8-second inter-channel gap; the embedded controller silently drops curve writes fired closer together.
- Profile apply order is boost → EPP → clock limit. Writing cpufreq's `boost` refreshes every policy and resets `scaling_max_freq`, so the clock cap must be written last.
- The existing GTK3 app stays installed and working until Task 16. Do not delete `rogcontrol.py` before then.
- No new hardware features. Behaviour changes are out of scope; this is a UI and structure change.

## File Structure

```
rogcontrol/                        package, installed to ~/.local/lib/rogcontrol
  __init__.py
  __main__.py       argument parsing, launches app or --self-test
  app.py            Adw.Application, window shell, navigation, toasts
  config.py         load / migrate / save                     [shared, no GTK]
  hardware.py       helper calls, sysfs reads, capabilities    [shared, no GTK]
  fancurve.py       interpolation, pwm conversion, rpm calib   [shared, no GTK]
  profiles.py       stock profiles, PPD mode map               [shared, no GTK]
  pages/
    __init__.py  overview.py  cpu.py  gpu.py
    fans.py  battery.py  keyboard.py  system.py
  widgets/
    __init__.py  curve_editor.py   ambient.py
tests/
  test_fancurve.py  test_profiles.py  test_config.py
  test_hardware.py  test_keyboard_colors.py
rogcontrol-tray                    GTK3 sidecar (installed to ~/.local/bin)
```

The four logic modules must not import `gi`. That is what makes them testable
and importable from the helper scripts.

---

### Task 1: Repository and test scaffolding

**Files:**
- Create: `.gitignore`, `tests/__init__.py`, `rogcontrol/__init__.py`, `rogcontrol/pages/__init__.py`, `rogcontrol/widgets/__init__.py`
- Create: `run-tests.sh`

**Interfaces:**
- Produces: `./run-tests.sh` runs the whole suite; package importable as `rogcontrol` from the project root.

- [ ] **Step 1: Initialise the repository**

```bash
cd ~/projects/rogcontrol
git init
printf '__pycache__/\n*.pyc\n*.zip\n' > .gitignore
```

- [ ] **Step 2: Create the package skeleton**

```bash
mkdir -p rogcontrol/pages rogcontrol/widgets tests
touch rogcontrol/__init__.py rogcontrol/pages/__init__.py \
      rogcontrol/widgets/__init__.py tests/__init__.py
```

Note: the existing flat scripts stay in `rogcontrol/` (the current source
directory) untouched. This package is created alongside them and only replaces
them at Task 16.

- [ ] **Step 3: Add the test runner**

```bash
cat > run-tests.sh <<'EOF'
#!/bin/bash
# Stdlib unittest only - the project deliberately has no test dependencies.
set -e
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
EOF
chmod +x run-tests.sh
```

- [ ] **Step 4: Verify it runs with zero tests**

Run: `./run-tests.sh`
Expected: `Ran 0 tests`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: initialise repository and test scaffolding"
```

---

### Task 2: fancurve.py

**Files:**
- Create: `rogcontrol/fancurve.py`
- Test: `tests/test_fancurve.py`
- Source to port from: `rogcontrol/rogcontrol.py` (`interpolate_curve`, `pct_to_pwm255`, `get_rpm_cal`, `FAN_RPM_CAL`)

**Interfaces:**
- Produces:
  - `interpolate_curve(points: list[tuple[int,int]], n: int = 8) -> list[tuple[int,int]]`
  - `pct_to_pwm255(pct: int|float) -> int`
  - `get_rpm_cal(config: dict|None, channel: str) -> tuple[float,float]`
  - `pct_to_rpm(pct, floor, slope) -> int`
  - `FAN_RPM_CAL: dict[str, tuple[float,float]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fancurve.py
import unittest
from rogcontrol import fancurve


class InterpolateCurve(unittest.TestCase):
    def test_users_own_points_are_preserved(self):
        pts = [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)]
        out = fancurve.interpolate_curve(pts, 8)
        for p in pts:
            self.assertIn(p, out)

    def test_always_returns_exactly_n(self):
        for pts in ([(40, 20), (80, 80)],
                    [(50, 5), (57, 10), (63, 15), (68, 22), (73, 52), (78, 85)]):
            self.assertEqual(len(fancurve.interpolate_curve(pts, 8)), 8)

    def test_fills_the_widest_gap_first(self):
        out = fancurve.interpolate_curve([(50, 10), (52, 12), (90, 90)], 4)
        self.assertIn((70, 51), out)

    def test_temperatures_strictly_increase(self):
        out = fancurve.interpolate_curve([(50, 5), (51, 9)], 8)
        temps = [t for t, _ in out]
        self.assertEqual(temps, sorted(set(temps)))

    def test_more_points_than_wanted_are_truncated(self):
        pts = [(t, t) for t in range(40, 50)]
        self.assertEqual(len(fancurve.interpolate_curve(pts, 8)), 8)


class PwmConversion(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(fancurve.pct_to_pwm255(0), 0)
        self.assertEqual(fancurve.pct_to_pwm255(100), 255)

    def test_clamps_out_of_range(self):
        self.assertEqual(fancurve.pct_to_pwm255(-10), 0)
        self.assertEqual(fancurve.pct_to_pwm255(150), 255)


class RpmCalibration(unittest.TestCase):
    def test_builtin_used_when_user_has_none(self):
        self.assertEqual(fancurve.get_rpm_cal({}, "1"), fancurve.FAN_RPM_CAL["1"])

    def test_user_calibration_wins(self):
        cfg = {"fan_rpm_cal": {"1": [1000, 40.0]}}
        self.assertEqual(fancurve.get_rpm_cal(cfg, "1"), (1000, 40.0))

    def test_malformed_calibration_falls_back(self):
        cfg = {"fan_rpm_cal": {"1": "nonsense"}}
        self.assertEqual(fancurve.get_rpm_cal(cfg, "1"), fancurve.FAN_RPM_CAL["1"])

    def test_pct_to_rpm(self):
        self.assertEqual(fancurve.pct_to_rpm(20, 1655, 49.3), 2641)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./run-tests.sh`
Expected: FAIL — `ModuleNotFoundError: No module named 'rogcontrol.fancurve'`

- [ ] **Step 3: Port the implementation**

Copy `interpolate_curve`, `pct_to_pwm255`, `get_rpm_cal` and `FAN_RPM_CAL` from
`rogcontrol/rogcontrol.py` verbatim, including their docstrings and the
`NOTE FOR OTHER MACHINES` comment above `FAN_RPM_CAL`. The logic is already
correct and tested by hand; do not rewrite it. Add one new function:

```python
def pct_to_rpm(pct, floor, slope):
    """Fan percentage to rpm using this machine's measured calibration."""
    return round(floor + slope * pct)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./run-tests.sh`
Expected: 9 tests, all pass.

- [ ] **Step 5: Commit**

```bash
git add rogcontrol/fancurve.py tests/test_fancurve.py
git commit -m "feat: extract fan curve maths into a shared module"
```

---

### Task 3: profiles.py

**Files:**
- Create: `rogcontrol/profiles.py`
- Test: `tests/test_profiles.py`
- Source to port from: `rogcontrol/rogcontrol.py` (`DEFAULT_PROFILES`, `PROFILE_TO_PPD_MODE`, `tailored_default_profiles`) and `rogcontrol-enforcer.py` (`PPD_MODE_TO_PROFILE`)

**Interfaces:**
- Produces:
  - `DEFAULT_PROFILES: dict[str, dict]` — Quiet, Balanced Power, Balanced Performance, Performance, in that order
  - `PROFILE_TO_PPD_MODE: dict[str, str]`
  - `PPD_MODE_TO_PROFILE: dict[str, str]` — first name wins
  - `tailored_default_profiles(gpu_min_w: int, gpu_max_w: int) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_profiles.py
import unittest
from rogcontrol import profiles


class StockProfiles(unittest.TestCase):
    def test_order_is_quietest_first(self):
        self.assertEqual(list(profiles.DEFAULT_PROFILES),
                         ["Quiet", "Balanced Power",
                          "Balanced Performance", "Performance"])

    def test_every_profile_has_the_required_sections(self):
        for name, prof in profiles.DEFAULT_PROFILES.items():
            for section in ("cpu", "gpu", "fans"):
                self.assertIn(section, prof, f"{name} is missing {section}")
            self.assertEqual(sorted(prof["fans"]), ["1", "2", "3"])

    def test_every_profile_names_an_energy_preference(self):
        want = {"Quiet": "power", "Balanced Power": "balance_power",
                "Balanced Performance": "balance_performance",
                "Performance": "performance"}
        for name, epp in want.items():
            self.assertEqual(profiles.DEFAULT_PROFILES[name]["cpu"]["epp"], epp)

    def test_the_two_balanced_profiles_differ_only_in_epp(self):
        a = dict(profiles.DEFAULT_PROFILES["Balanced Power"]["cpu"])
        b = dict(profiles.DEFAULT_PROFILES["Balanced Performance"]["cpu"])
        a.pop("epp"); b.pop("epp")
        self.assertEqual(a, b)
        self.assertEqual(profiles.DEFAULT_PROFILES["Balanced Power"]["fans"],
                         profiles.DEFAULT_PROFILES["Balanced Performance"]["fans"])


class PowerModeMapping(unittest.TestCase):
    def test_every_stock_profile_maps_to_a_mode(self):
        for name in profiles.DEFAULT_PROFILES:
            self.assertIn(name, profiles.PROFILE_TO_PPD_MODE)

    def test_only_the_three_real_ppd_modes_are_used(self):
        self.assertEqual(set(profiles.PROFILE_TO_PPD_MODE.values()),
                         {"performance", "balanced", "power-saver"})

    def test_shared_mode_resolves_to_the_first_profile(self):
        # Both Balanced profiles map to "balanced"; the reverse map must be
        # deterministic, not whichever key happened to be written last.
        self.assertEqual(profiles.PPD_MODE_TO_PROFILE["balanced"],
                         "Balanced Performance")


class TailoredDefaults(unittest.TestCase):
    def test_gpu_watts_scale_to_the_card(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=70)
        self.assertLessEqual(out["Performance"]["gpu"]["watts"], 70)
        self.assertGreater(out["Performance"]["gpu"]["watts"],
                           out["Quiet"]["gpu"]["watts"])

    def test_cpu_limits_are_not_scaled(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=70)
        self.assertEqual(out["Quiet"]["cpu"],
                         profiles.DEFAULT_PROFILES["Quiet"]["cpu"])

    def test_result_is_a_copy(self):
        out = profiles.tailored_default_profiles(gpu_min_w=5, gpu_max_w=140)
        out["Quiet"]["cpu"]["stapm"] = 1
        self.assertNotEqual(profiles.DEFAULT_PROFILES["Quiet"]["cpu"]["stapm"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./run-tests.sh`
Expected: FAIL — no module named `rogcontrol.profiles`.

- [ ] **Step 3: Port the implementation**

Copy `DEFAULT_PROFILES` and `PROFILE_TO_PPD_MODE` verbatim. Change
`tailored_default_profiles` to take `gpu_min_w` and `gpu_max_w` as arguments
rather than reading module globals, so it has no dependency on hardware
detection. Build the reverse map first-wins, as the enforcer already does:

```python
PPD_MODE_TO_PROFILE = {}
for _name, _mode in PROFILE_TO_PPD_MODE.items():
    PPD_MODE_TO_PROFILE.setdefault(_mode, _name)
```

`PROFILE_TO_PPD_MODE` must list `Balanced Performance` before `Balanced Power`
so the reverse map resolves `balanced` to the performance-leaning one, matching
today's behaviour.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./run-tests.sh`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add rogcontrol/profiles.py tests/test_profiles.py
git commit -m "feat: extract stock profiles and power-mode mapping"
```

---

### Task 4: config.py

**Files:**
- Create: `rogcontrol/config.py`
- Test: `tests/test_config.py`
- Source to port from: `rogcontrol/rogcontrol.py` (`CONFIG_PATH`, `CONFIG_VERSION`, `DEFAULT_CONFIG`, `migrate_config`, `load_config`, `save_config`)

**Interfaces:**
- Consumes: `rogcontrol.profiles.DEFAULT_PROFILES`, `tailored_default_profiles`
- Produces:
  - `CONFIG_PATH: str`, `CONFIG_VERSION: int` (value `1`)
  - `migrate_config(cfg: dict, gpu_min_w=1, gpu_max_w=140) -> dict`
  - `load_config(path: str | None = None) -> dict`
  - `save_config(cfg: dict, path: str | None = None) -> None` — atomic

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import json, os, tempfile, unittest
from rogcontrol import config


class Migration(unittest.TestCase):
    def test_empty_config_gets_stock_profiles(self):
        out = config.migrate_config({})
        self.assertEqual(list(out["profiles"]),
                         ["Quiet", "Balanced Power",
                          "Balanced Performance", "Performance"])
        self.assertEqual(out["config_version"], 1)

    def test_user_values_are_never_touched(self):
        cfg = {"profiles": {"Mine": {"cpu": {"stapm": 12345}, "gpu": {},
                                     "fans": {"1": [[50, 5]]}}},
               "current_profile": "Mine", "charge_limit": 61}
        out = config.migrate_config(json.loads(json.dumps(cfg)))
        self.assertEqual(out["profiles"]["Mine"]["cpu"]["stapm"], 12345)
        self.assertEqual(out["profiles"]["Mine"]["fans"]["1"], [[50, 5]])
        self.assertEqual(out["charge_limit"], 61)

    def test_deleted_stock_profile_stays_deleted(self):
        cfg = config.migrate_config({})
        del cfg["profiles"]["Quiet"]
        cfg["current_profile"] = "Performance"
        out = config.migrate_config(cfg)
        self.assertNotIn("Quiet", out["profiles"])

    def test_missing_section_is_filled_from_the_stock_profile(self):
        cfg = {"profiles": {"Quiet": {"cpu": {"stapm": 1}}},
               "current_profile": "Quiet"}
        out = config.migrate_config(cfg)
        self.assertIn("fans", out["profiles"]["Quiet"])
        self.assertIn("gpu", out["profiles"]["Quiet"])

    def test_current_profile_must_exist(self):
        out = config.migrate_config({"profiles": {"A": {"cpu": {}, "gpu": {},
                                                        "fans": {}}},
                                     "current_profile": "Gone"})
        self.assertEqual(out["current_profile"], "A")

    def test_unknown_keys_survive(self):
        out = config.migrate_config({"something_from_a_newer_build": 7})
        self.assertEqual(out["something_from_a_newer_build"], 7)

    def test_idempotent(self):
        once = config.migrate_config({})
        twice = config.migrate_config(json.loads(json.dumps(once)))
        self.assertEqual(once, twice)


class SaveAndLoad(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "rogcontrol.json")

    def test_round_trip(self):
        cfg = config.migrate_config({})
        config.save_config(cfg, self.path)
        self.assertEqual(config.load_config(self.path)["profiles"].keys(),
                         cfg["profiles"].keys())

    def test_save_is_atomic(self):
        cfg = config.migrate_config({})
        config.save_config(cfg, self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_corrupt_config_is_preserved_not_replaced(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        config.load_config(self.path)
        backups = [n for n in os.listdir(self.dir) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./run-tests.sh`
Expected: FAIL — no module named `rogcontrol.config`.

- [ ] **Step 3: Port the implementation**

Copy the current functions. Two changes:

1. `load_config` and `save_config` take an optional `path`, defaulting to
   `CONFIG_PATH`, so tests never touch the real config.
2. `save_config` writes to `path + ".tmp"` and `os.replace()`s it. The current
   implementation writes in place, which can truncate the file if the process
   dies mid-write. This is a real bug being fixed while the code moves.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./run-tests.sh`
Expected: all pass.

- [ ] **Step 5: Verify against the real config, read-only**

```bash
python3 -c "
import copy, json, os
from rogcontrol import config
real = json.load(open(os.path.expanduser('~/.config/rogcontrol.json')))
out = config.migrate_config(copy.deepcopy(real))
assert all(real['profiles'][n]['fans'] == out['profiles'][n]['fans'] for n in real['profiles'])
assert all(real['profiles'][n].get('cpu') == out['profiles'][n].get('cpu') for n in real['profiles'])
assert real['kbd_rgb'] == out['kbd_rgb']
print('real config survives migration unchanged')"
```

- [ ] **Step 6: Commit**

```bash
git add rogcontrol/config.py tests/test_config.py
git commit -m "feat: extract config handling, make saves atomic"
```

---

### Task 5: hardware.py

**Files:**
- Create: `rogcontrol/hardware.py`
- Test: `tests/test_hardware.py`
- Source to port from: `rogcontrol/rogcontrol.py` (`read_file`, `find_hwmon_by_name`, `run_helper`, `detect_capabilities`, `read_battery`, `read_epp_preferences`, `read_cpu_clock_range`, `read_current_cpu_clock_cap`) and `rogcontrol-enforcer.py` (`boost_control_available`, `epp_control_available`, `clock_limit_available`)

**Interfaces:**
- Produces:
  - `read_file(path) -> str | None`
  - `find_hwmon_by_name(name, root="/sys/class/hwmon") -> str | None`
  - `run_helper(*args) -> tuple[bool, str]`
  - `detect_capabilities(root="/sys") -> dict`
  - `read_battery(root="/sys/class/power_supply") -> tuple[int|None, bool|None]`
  - `read_cpu_clock_range(root=...) -> tuple[int,int] | None`
  - `boost_control_available()`, `epp_control_available()`, `clock_limit_available()`

Every reader takes a root path so tests can point it at a fake sysfs tree.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hardware.py
import os, tempfile, unittest
from rogcontrol import hardware


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FakeSysfs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_find_hwmon_by_name(self):
        write(os.path.join(self.root, "hwmon3", "name"), "k10temp\n")
        write(os.path.join(self.root, "hwmon4", "name"), "asus\n")
        self.assertTrue(hardware.find_hwmon_by_name("asus", self.root)
                        .endswith("hwmon4"))

    def test_find_hwmon_missing_returns_none(self):
        self.assertIsNone(hardware.find_hwmon_by_name("nope", self.root))

    def test_read_file_strips_and_tolerates_missing(self):
        write(os.path.join(self.root, "v"), " 42 \n")
        self.assertEqual(hardware.read_file(os.path.join(self.root, "v")), "42")
        self.assertIsNone(hardware.read_file(os.path.join(self.root, "absent")))

    def test_read_battery(self):
        base = os.path.join(self.root, "BAT0")
        write(os.path.join(base, "type"), "Battery\n")
        write(os.path.join(base, "capacity"), "77\n")
        write(os.path.join(base, "status"), "Charging\n")
        self.assertEqual(hardware.read_battery(self.root), (77, True))

    def test_read_battery_full_is_not_charging(self):
        base = os.path.join(self.root, "BAT0")
        write(os.path.join(base, "type"), "Battery\n")
        write(os.path.join(base, "capacity"), "100\n")
        write(os.path.join(base, "status"), "Full\n")
        self.assertEqual(hardware.read_battery(self.root), (100, False))

    def test_read_battery_absent(self):
        self.assertEqual(hardware.read_battery(self.root), (None, None))

    def test_clock_range_uses_hardware_limits_not_current_window(self):
        pol = os.path.join(self.root, "policy0")
        write(os.path.join(pol, "cpuinfo_min_freq"), "421798\n")
        write(os.path.join(pol, "cpuinfo_max_freq"), "5386028\n")
        write(os.path.join(pol, "scaling_min_freq"), "1492514\n")
        write(os.path.join(pol, "scaling_max_freq"), "3200000\n")
        self.assertEqual(hardware.read_cpu_clock_range(self.root),
                         (421798, 5386028))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./run-tests.sh`
Expected: FAIL — no module named `rogcontrol.hardware`.

- [ ] **Step 3: Port the implementation**

Move the listed functions across, adding the `root` parameter to each reader.
`run_helper` keeps its exact current form — `sudo -n /usr/local/bin/rogcontrol-helper …`,
returning `(ok, message)`. `detect_capabilities` keeps every existing key,
including `kbd_ambient`, and keeps its documented behaviour of never disabling
keyboard controls.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./run-tests.sh`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add rogcontrol/hardware.py tests/test_hardware.py
git commit -m "feat: extract hardware access into a shared module"
```

---

### Task 6: Point the helper scripts at the shared modules

**Files:**
- Modify: `rogcontrol/rogcontrol-enforcer.py`, `rogcontrol/rogcontrol-apply.py`, `rogcontrol/rogcontrol-cycle-profile.py`, `rogcontrol/rogcontrol-cycle-kbdlight.py`

**Interfaces:**
- Consumes: `rogcontrol.fancurve`, `rogcontrol.profiles`, `rogcontrol.hardware`, `rogcontrol.config`

This is the task that pays for the split: these four scripts currently carry
their own copies of `interpolate_curve`, `pct_to_pwm255` and `run_helper`, and
that duplication has already caused the apply-ordering fix to be made in four
places.

- [ ] **Step 1: Add the import shim to each script**

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.local/lib"))
from rogcontrol.fancurve import interpolate_curve, pct_to_pwm255
from rogcontrol.hardware import run_helper
from rogcontrol.profiles import PROFILE_TO_PPD_MODE, PPD_MODE_TO_PROFILE
```

The path insert is needed because these run as standalone executables from
`~/.local/bin`, not as part of the package.

- [ ] **Step 2: Delete the duplicated definitions**

Remove the local `interpolate_curve`, `pct_to_pwm255`, `run_helper`,
`PROFILE_TO_PPD_MODE` and `PPD_MODE_TO_PROFILE` from all four scripts. Leave
everything else alone.

- [ ] **Step 3: Verify each script still parses and behaves**

```bash
for f in rogcontrol/rogcontrol-{enforcer,apply,cycle-profile,cycle-kbdlight}.py; do
    python3 -m py_compile "$f" && echo "OK $f"
done
PYTHONPATH=. python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('e', 'rogcontrol/rogcontrol-enforcer.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('enforcer imports shared modules:', m.PPD_MODE_TO_PROFILE['balanced'])"
```

Expected: all four compile; the enforcer prints `Balanced Performance`.

- [ ] **Step 4: Commit**

```bash
git add rogcontrol/rogcontrol-*.py
git commit -m "refactor: helper scripts import shared modules instead of copying them"
```

---

### Task 7: Application shell

**Files:**
- Create: `rogcontrol/app.py`, `rogcontrol/__main__.py`

**Interfaces:**
- Consumes: `rogcontrol.config`, `rogcontrol.hardware`, `rogcontrol.profiles`
- Produces:
  - `RogControlApplication(Adw.Application)` with `--minimized`, `--toggle`, `--self-test`
  - `RogControlWindow.toast(text: str)` — every page reports through this
  - `RogControlWindow.apply_async(fn, on_done)` — runs a hardware call off the main thread
  - `PAGES: list[tuple[str, str, type]]` — (id, title, page class)

- [ ] **Step 1: Write the shell**

```python
# rogcontrol/app.py (structure)
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

class RogControlWindow(Adw.ApplicationWindow):
    """Split view: sidebar of pages, content in a ToolbarView with a header
    bar carrying the profile switcher, wrapped in a ToastOverlay."""

class RogControlApplication(Adw.Application):
    """Single instance via application_id, as the GTK3 version already does."""
```

Keep `application_id="com.fadi.rogcontrol"` — the desktop file, the autostart
entry and `--toggle` all depend on it.

- [ ] **Step 2: Add `--self-test`**

```python
# rogcontrol/__main__.py
# --self-test builds every page with the real config and exits non-zero on
# exception. GUI code cannot be unit tested here, so this is the smoke test.
```

- [ ] **Step 3: Verify the window opens and self-test passes**

```bash
PYTHONPATH=. python3 -m rogcontrol --self-test; echo "exit=$?"
PYTHONPATH=. python3 -m rogcontrol   # opens a window with an empty sidebar
```

Expected: `exit=0`; a window with the sidebar, header bar and profile switcher.

- [ ] **Step 4: Commit**

```bash
git add rogcontrol/app.py rogcontrol/__main__.py
git commit -m "feat: GTK4 application shell with sidebar navigation"
```

---

### Task 8: Fan curve editor widget

**Files:**
- Create: `rogcontrol/widgets/curve_editor.py`
- Source to port from: `rogcontrol/rogcontrol.py`, the `FanCurveGraph` class

**Interfaces:**
- Consumes: `rogcontrol.fancurve.get_rpm_cal`, `pct_to_rpm`
- Produces: `CurveEditor(Gtk.DrawingArea)` with `set_points(list)`, `get_points() -> list`, and a `changed` signal

- [ ] **Step 1: Port the drawing**

GTK4 replaces the `draw` signal with `set_draw_func(self, area, cr, w, h)`. The
Cairo drawing itself is unchanged — axes, grid, the curve, the point handles,
labels in rpm.

- [ ] **Step 2: Port the interaction**

GTK3 event boxes become gesture controllers:

```python
drag = Gtk.GestureDrag()
drag.connect("drag-begin", self._on_drag_begin)
drag.connect("drag-update", self._on_drag_update)
drag.connect("drag-end", self._on_drag_end)
self.add_controller(drag)
```

- [ ] **Step 3: Add keyboard nudging**

A `Gtk.EventControllerKey` moving the selected point by 1 °C or 1 % per arrow
press. The GTK3 editor had no keyboard access at all.

- [ ] **Step 4: Verify by hand**

Run the app, drag each point, confirm six points are kept, values clamp at the
axis limits, and the rpm labels match `pct_to_rpm`.

- [ ] **Step 5: Commit**

```bash
git add rogcontrol/widgets/curve_editor.py
git commit -m "feat: GTK4 fan curve editor with gestures and keyboard support"
```

---

### Task 9-15: Pages

Each page is `Adw.PreferencesPage` of `Adw.PreferencesGroup` cards, added to
`PAGES` in `app.py`, and each ends with the same verification: run
`--self-test`, then open the app and confirm the page renders and its controls
apply.

- [ ] **Task 9: Overview** — `rogcontrol/pages/overview.py`. Live rows fed by a
  2-second `GLib.timeout_add_seconds`: CPU temperature, peak clock and package
  power; GPU temperature and power; all three fans with the curve's requested
  rpm beside the measured rpm; battery and charge limit; two status rows —
  whether `pwmN_enable` reads 1, and whether the OS power mode matches the
  profile. Commit: `feat: overview page`.

- [ ] **Task 10: CPU** — `rogcontrol/pages/cpu.py`. *Power limits* group:
  STAPM, Fast, Slow, temperature target. *Tuning* group: Curve Optimizer with
  the subtitle "Too negative freezes the machine under load — move in small
  steps", turbo boost as `Adw.SwitchRow`, max clock with the no-limit value
  named in its subtitle. Commit: `feat: CPU page`.

- [ ] **Task 11: GPU** — `rogcontrol/pages/gpu.py`. Power limit, core and
  memory clock offsets, clock ceiling, Dynamic Boost, temperature target,
  force-maximum switch. Commit: `feat: GPU page`.

- [ ] **Task 12: Fans** — `rogcontrol/pages/fans.py`. Three `CurveEditor`s, the
  explicit **Apply fan curves** button with an `Adw.Banner` showing progress,
  and the calibration action. The apply keeps the 8-second inter-channel gap
  and runs off the main thread. Commit: `feat: fans page`.

- [ ] **Task 13: Battery** — `rogcontrol/pages/battery.py`. Charge limit, plus
  the AC and battery profile pickers moved out of the window header. Commit:
  `feat: battery page`.

- [ ] **Task 14: Keyboard** — `rogcontrol/pages/keyboard.py`, and move the
  ambient sampler to `rogcontrol/widgets/ambient.py` unchanged. Brightness,
  mode, two `Gtk.ColorDialogButton`s replacing the six RGB spin buttons, speed.
  Commit: `feat: keyboard page`.

- [ ] **Task 15: System** — `rogcontrol/pages/system.py`. GPU mode, power-mode
  sync state, log view, about dialog. Commit: `feat: system page`.

---

### Task 16: Tray sidecar

**Files:**
- Create: `rogcontrol/rogcontrol-tray`
- Source to port from: `rogcontrol/rogcontrol.py`, the `build_tray` function

**Interfaces:**
- Reads `~/.config/rogcontrol.json` for the profile list and the active profile
- On switch: writes `current_profile` atomically, then runs `rogcontrol-apply.py`
- "Show window" runs `rogcontrol --toggle`; "Quit" stops both processes

- [ ] **Step 1: Write the sidecar**

GTK3 plus `AyatanaAppIndicator3`, carrying over the radio-item menu, the
`_tray_updating` guard that stops a programmatic selection from re-triggering
an apply, and the rule that only stock profiles appear in the menu.

- [ ] **Step 2: Poll for external changes**

A 5-second `GLib.timeout_add_seconds` re-reads the config so the tray follows
profile changes made in the window, by the hotkey, or by the enforcer adopting
an OS power-mode change.

- [ ] **Step 3: Verify**

Run the sidecar, switch profile from the tray, and confirm the config changes,
`rogcontrol-apply.py` runs, and the window reflects it within 5 seconds.

- [ ] **Step 4: Commit**

```bash
git add rogcontrol/rogcontrol-tray
git commit -m "feat: GTK3 tray sidecar"
```

---

### Task 17: Installer and switchover

**Files:**
- Modify: `rogcontrol/install.sh`, `rogcontrol/uninstall.sh`, `rogcontrol/rogcontrol.desktop`, `rogcontrol/rogcontrol-autostart.desktop`
- Delete: `rogcontrol/rogcontrol.py` (the GTK3 application)

- [ ] **Step 1: Add the dependency check**

```bash
# GTK4 and libadwaita are required. Name the packages rather than letting
# python die with an ImportError the user cannot act on.
python3 -c "
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, Adw" 2>/dev/null || die \
"GTK4 and libadwaita are required.
  Arch:   sudo pacman -S gtk4 libadwaita python-gobject
  Fedora: sudo dnf install gtk4 libadwaita python3-gobject
  Debian: sudo apt install libgtk-4-1 libadwaita-1-0 python3-gi"
```

- [ ] **Step 2: Install the package and launcher**

Install the `rogcontrol/` package directory to `~/.local/lib/rogcontrol`, a
launcher to `~/.local/bin/rogcontrol` running `python3 -m rogcontrol`, and the
tray sidecar to `~/.local/bin/rogcontrol-tray`. Remove any previously installed
`~/.local/bin/rogcontrol.py`.

- [ ] **Step 3: Update the desktop and autostart entries**

`Exec=rogcontrol` for the launcher; the autostart entry starts
`rogcontrol-tray` rather than `rogcontrol --minimized`.

- [ ] **Step 4: Verify a full install on this machine**

```bash
./install.sh
rogcontrol --self-test; echo "exit=$?"
systemctl --user is-active rogcontrol-enforcer.service
python3 -c "
import json, os
c = json.load(open(os.path.expanduser('~/.config/rogcontrol.json')))
print('profiles intact:', list(c['profiles']))"
```

Expected: install completes, self-test exits 0, enforcer active, profiles
unchanged.

- [ ] **Step 5: Manual checklist before declaring done**

Switch profile in the window; switch profile from the tray; apply a fan curve
and confirm all three channels reach the EC; change a keyboard mode; enable
Ambient and confirm no new permission prompt; reboot and confirm the boot apply
still runs.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: install the GTK4 application and retire the GTK3 one"
```

## Self-Review

- **Spec coverage:** module split (Tasks 2-6), navigation shell (7), curve
  editor (8), all seven pages (9-15), tray sidecar (16), installer, dependency
  check and switchover (17), tests (2-5), `--self-test` (7). The spec's testing
  section names config, fancurve, profiles and keyboard colour maths — the
  first three are Tasks 2-5; keyboard colour maths moves with Task 14 and its
  tests go in `tests/test_keyboard_colors.py` as part of that task.
- **Placeholders:** none. Every step names exact files, commands and expected
  output.
- **Type consistency:** `interpolate_curve`, `pct_to_pwm255`, `run_helper`,
  `PROFILE_TO_PPD_MODE` and `PPD_MODE_TO_PROFILE` keep the names they have
  today, so Task 6 can delete the duplicates without renaming anything.
