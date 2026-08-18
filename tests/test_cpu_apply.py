"""The CPU apply: what it writes, in what order, and when it may write.

Two halves, and they are two different failures.

* **The order.** ryzenadj's limits, then cpufreq's boost, then EPP, then the
  clock ceiling. Writing ``boost`` refreshes every cpufreq policy and takes
  ``scaling_max_freq`` back up to hardware maximum with it, so a cap written
  before boost is silently undone -- the machine ends up unlimited while the
  window says 3.0 GHz. ``hardware.cpu_apply_plan`` is the one definition of
  that order, and it is pure, so it can be tested here rather than through a
  page that needs a display.

* **When.** The CPU and GPU pages used to apply a control ~400 ms after it
  stopped moving, with no Apply button anywhere: dragging a slider pushed
  settings at the hardware on the way past. The page modules are parsed
  rather than imported (they pull in GTK 4, which this suite must not touch)
  and the change handlers are read back out of them, because a handler that
  quietly grew a hardware call again is exactly how the old behaviour would
  come back.
"""

import ast
import unittest
from pathlib import Path

from rogcontrol import hardware, profiles

PACKAGE_DIR = Path(profiles.__file__).resolve().parent
CPU_PAGE = PACKAGE_DIR / "pages" / "cpu.py"
GPU_PAGE = PACKAGE_DIR / "pages" / "gpu.py"

# Everything a machine can do, so a plan is only ever shortened by the values
# it is given rather than by capability gating that is not under test.
ALL_CAPS = {"ryzenadj": True, "cpu_boost": True, "cpu_epp": ["power"],
            "cpu_clock": (400000, 5400000)}

FULL = {"stapm": 35000, "fast": 50000, "slow": 35000, "temp": 80,
        "coall": -5, "boost": False, "epp": "power", "max_freq": 3000000}


def steps(values, caps=ALL_CAPS):
    return [step for step, _args in hardware.cpu_apply_plan(values, caps)]


def args_for(step, values, caps=ALL_CAPS):
    for name, args in hardware.cpu_apply_plan(values, caps):
        if name == step:
            return args
    return None


class ApplyOrder(unittest.TestCase):

    def test_the_steps_come_out_in_the_hardware_order(self):
        self.assertEqual(steps(FULL), ["limits", "boost", "epp", "clock"])

    def test_the_clock_cap_is_always_written_after_boost(self):
        """The one that matters. Boost resets every policy's ceiling."""
        order = steps(FULL)
        self.assertLess(order.index("boost"), order.index("clock"))

    def test_the_order_does_not_follow_the_dict_it_was_given(self):
        """A values dict written in another order must not reorder the plan
        -- Python keeps insertion order, so this would silently work."""
        backwards = {key: FULL[key] for key in reversed(list(FULL))}
        self.assertEqual(steps(backwards), ["limits", "boost", "epp", "clock"])

    def test_the_plan_is_the_module_order(self):
        self.assertEqual(steps(FULL), list(hardware.CPU_APPLY_STEPS))


class WhatEachStepSends(unittest.TestCase):

    def test_the_limits_go_as_one_ryzenadj_call(self):
        self.assertEqual(args_for("limits", FULL),
                         ("cpu", 35000, 50000, 35000, 80, -5))

    def test_a_missing_curve_optimizer_value_is_stock_rather_than_absent(self):
        values = dict(FULL)
        del values["coall"]
        self.assertEqual(args_for("limits", values)[-1], 0)

    def test_boost_is_one_or_zero_not_a_bool(self):
        self.assertEqual(args_for("boost", dict(FULL, boost=True)),
                         ("cpuboost", 1))
        self.assertEqual(args_for("boost", dict(FULL, boost=False)),
                         ("cpuboost", 0))

    def test_epp_goes_by_name(self):
        self.assertEqual(args_for("epp", FULL), ("cpuepp", "power"))

    def test_a_clock_cap_is_sent_in_khz(self):
        self.assertEqual(args_for("clock", FULL), ("cpuclock", 3000000))

    def test_no_ceiling_is_sent_as_max_rather_than_skipped(self):
        """0 means "this profile wants no ceiling", and it still has to be
        written or a cap set by the previous profile survives."""
        self.assertEqual(args_for("clock", dict(FULL, max_freq=0)),
                         ("cpuclock", "max"))


class CapabilityGating(unittest.TestCase):

    def test_no_ryzenadj_drops_the_limits_and_keeps_the_rest(self):
        caps = dict(ALL_CAPS, ryzenadj=False)
        self.assertEqual(steps(FULL, caps), ["boost", "epp", "clock"])

    def test_no_boost_switch_drops_boost_only(self):
        caps = dict(ALL_CAPS, cpu_boost=False)
        self.assertEqual(steps(FULL, caps), ["limits", "epp", "clock"])

    def test_no_epp_drops_epp_only(self):
        caps = dict(ALL_CAPS, cpu_epp=[])
        self.assertEqual(steps(FULL, caps), ["limits", "boost", "clock"])

    def test_no_cpufreq_ceiling_drops_the_cap_only(self):
        caps = dict(ALL_CAPS, cpu_clock=None)
        self.assertEqual(steps(FULL, caps), ["limits", "boost", "epp"])

    def test_a_machine_that_can_do_nothing_gets_an_empty_plan(self):
        self.assertEqual(hardware.cpu_apply_plan(FULL, {}), [])

    def test_no_caps_at_all_is_not_an_exception(self):
        self.assertEqual(hardware.cpu_apply_plan(FULL, None), [])


class ValuesTheCallerHasNoOpinionOn(unittest.TestCase):
    """A missing key means "leave it alone", not "write a default"."""

    def test_partial_limits_are_not_sent_at_all(self):
        values = dict(FULL)
        del values["fast"]
        self.assertNotIn("limits", steps(values))

    def test_a_profile_without_boost_leaves_boost_alone(self):
        values = dict(FULL)
        del values["boost"]
        self.assertNotIn("boost", steps(values))

    def test_a_profile_without_an_epp_leaves_it_alone(self):
        values = dict(FULL)
        del values["epp"]
        self.assertNotIn("epp", steps(values))

    def test_a_profile_without_a_clock_key_leaves_the_ceiling_alone(self):
        values = dict(FULL)
        del values["max_freq"]
        self.assertNotIn("clock", steps(values))


class EveryStepIsSaved(unittest.TestCase):
    """A step that reaches the hardware must also reach the profile.

    The regression this exists for: "minclock" was added to
    ``hardware.CPU_APPLY_STEPS`` and to the page's controls, but ``_save``
    was an if/elif chain and nobody added a branch for it. The clock floor
    was written to the chip on every Apply and never stored, so reloading
    the page put it straight back to no floor.

    The page is parsed rather than imported -- it pulls in GTK 4.
    """

    def tables(self):
        tree = ast.parse(CPU_PAGE.read_text(encoding="utf-8"))
        out = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            name = node.targets[0]
            if isinstance(name, ast.Name) and name.id in (
                    "STEP_SAVES", "STEP_ROWS", "STEP_LABELS"):
                out[name.id] = {k.value for k in node.value.keys}
        return out

    def test_every_apply_step_knows_what_to_save(self):
        saves = self.tables()["STEP_SAVES"]
        for step in hardware.CPU_APPLY_STEPS:
            self.assertIn(step, saves,
                          f"step '{step}' is applied but never saved")

    def test_every_apply_step_has_rows_and_a_label(self):
        tables = self.tables()
        for table in ("STEP_ROWS", "STEP_LABELS"):
            for step in hardware.CPU_APPLY_STEPS:
                self.assertIn(step, tables[table],
                              f"step '{step}' is missing from {table}")


class NothingIsAppliedOnDrag(unittest.TestCase):
    """The pages are read, not imported: they pull in GTK 4."""

    # Handlers that fire while the user is still moving a control. None of
    # them may reach the hardware, directly or through the window's worker.
    CHANGE_HANDLERS = ("_on_control_changed", "_on_switch_changed",
                       "_on_changed", "_update_banner", "_dirty_keys")

    FORBIDDEN = ("run_helper", "apply_async", "set_nvidia_clock_offset",
                 "save_config")

    def functions(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return {node.name: node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)}

    def names_called(self, node):
        out = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute):
                out.add(child.attr)
            elif isinstance(child, ast.Name):
                out.add(child.id)
        return out

    def assert_clean_handlers(self, path):
        functions = self.functions(path)
        for name in self.CHANGE_HANDLERS:
            node = functions.get(name)
            if node is None:
                continue
            called = self.names_called(node)
            for forbidden in self.FORBIDDEN:
                self.assertNotIn(
                    forbidden, called,
                    f"{path.name}: {name} reaches {forbidden} -- moving a "
                    f"control must not write to the hardware")

    def test_the_cpu_page_applies_nothing_while_a_control_moves(self):
        self.assert_clean_handlers(CPU_PAGE)

    def test_the_gpu_page_applies_nothing_while_a_control_moves(self):
        self.assert_clean_handlers(GPU_PAGE)

    def assert_has_apply_button(self, path):
        text = path.read_text(encoding="utf-8")
        self.assertIn("_on_apply_clicked", text,
                      f"{path.name} has no Apply handler")
        # The button itself is built by widgets/action_buttons.py now, so
        # what is checked here is that the page still asks for one. The
        # widget module is what guarantees it is a button labelled Apply --
        # see TestActionButtons in test_hardware.py.
        self.assertIn("apply_revert_buttons(", text,
                      f"{path.name} has no Apply button")
        self.assertIn("Adw.Banner()", text,
                      f"{path.name} has no unapplied-changes banner")

    def test_the_graphics_mode_picker_lives_on_the_gpu_page(self):
        """It moved off the System page, and must not drift back.

        Which card the screen is plugged into is a fact about the graphics
        card, and every control that depends on it -- power limit,
        temperature target, Dynamic Boost -- is on the GPU page."""
        gpu = GPU_PAGE.read_text(encoding="utf-8")
        system = (GPU_PAGE.parent / "system.py").read_text(encoding="utf-8")
        self.assertIn("_build_gpu_mode", gpu,
                      "the GPU page has no graphics mode section")
        self.assertNotIn("_build_gpu_mode", system,
                         "the System page still builds a graphics mode picker")

    def test_integrated_from_the_mux_mode_is_refused_not_attempted(self):
        """The switch that froze this machine at every login.

        supergfxd accepts Integrated while the MUX still has the panel on
        the NVIDIA card, stores it, powers that card down -- and re-applies
        the stored mode at every subsequent login. The page has to refuse
        and offer Hybrid rather than pass the request on."""
        gpu = GPU_PAGE.read_text(encoding="utf-8")
        self.assertIn("mode_needs_hybrid_first", gpu)
        self.assertIn("_offer_hybrid_first", gpu)
        # Checked before the switch is started, not after.
        handler = gpu.index("def _on_mode_changed")
        started = gpu.index("_on_mode_response", handler)
        self.assertLess(gpu.index("mode_needs_hybrid_first", handler), started,
                        "the unsafe switch is checked too late")

    def test_the_mux_switch_offers_a_reboot(self):
        """A MUX change is applied at POST, so the page has to offer one."""
        gpu = GPU_PAGE.read_text(encoding="utf-8")
        self.assertIn("mode_change_needs_reboot", gpu)
        self.assertIn("reboot_system", gpu)

    def test_the_gpu_page_does_not_wake_the_card_to_poll_it(self):
        """dgpu_is_suspended is checked before nvidia-smi runs.

        Running nvidia-smi wakes the card to answer, so polling it every two
        seconds held the dGPU awake for as long as the page was open."""
        gpu = GPU_PAGE.read_text(encoding="utf-8")
        self.assertLess(gpu.index("dgpu_is_suspended"),
                        gpu.index("read_nvidia_stats"),
                        "nvidia-smi is called before the suspend check")

    def test_the_action_buttons_live_in_the_header_not_on_the_page(self):
        """Apply/Revert are handed to the window, not added to the page.

        They have moved twice: bottom of the page (off screen exactly when a
        control had just been moved), then top of the page (a full-width
        card of empty space above every page), and now the header bar. A
        page that adds its own action box back to its PreferencesPage would
        put that empty card back."""
        fans = (CPU_PAGE.parent / "fans.py").read_text(encoding="utf-8")
        for path, text in ((CPU_PAGE, CPU_PAGE.read_text(encoding="utf-8")),
                           (GPU_PAGE, GPU_PAGE.read_text(encoding="utf-8")),
                           (CPU_PAGE.parent / "fans.py", fans)):
            self.assertIn("self.action_box", text,
                          f"{path.name} exposes no action_box for the header")
            self.assertNotIn("page.add(self._build_actions_group())", text,
                             f"{path.name} still adds its buttons to the page")

    def test_the_two_live_readings_share_one_row(self):
        """Temperature and its fan sit side by side, not stacked.

        They were a full ActionRow each -- two rows, plus a two-line
        subtitle on the CPU page -- for two four-character numbers, above
        the controls the page exists for."""
        for path in (CPU_PAGE, GPU_PAGE):
            text = path.read_text(encoding="utf-8")
            self.assertIn("build_stat_row(", text,
                          f"{path.name} does not pair its live readings")
            self.assertNotIn("self._live_row(", text,
                             f"{path.name} still stacks a reading per row")

    def test_opening_the_window_brings_the_tray_back(self):
        """Quit stops the tray for good, so launching has to restore it.

        The tray's Quit exits with a code systemd is told never to restart
        (QUIT_EXIT_CODE), which is what makes Quit actually quit. Without a
        counterpart on the launch side, the icon could only be brought back
        with a systemctl command or a reboot."""
        app = CPU_PAGE.parent.parent / "app.py"
        text = app.read_text(encoding="utf-8")
        self.assertIn("hardware.start_tray()", text)
        # In _ensure_window, not do_activate: --show and --toggle open a
        # window without going through activate.
        before = text.index("def _ensure_window")
        after = text.index("def ", before + 10)
        self.assertIn("start_tray", text[before:after],
                      "start_tray is not on the path every launch takes")

    def test_the_system_page_reports_the_graphics_daemon(self):
        """Absent and silent are different problems with different fixes.

        The picker moved to the GPU page; when supergfxd is not answering
        that picker is greyed out, and the reason has to be somewhere a
        user hunting "why can I not switch" will actually find it."""
        system = (CPU_PAGE.parent / "system.py").read_text(encoding="utf-8")
        self.assertIn("_render_supergfx", system)
        for token in ("SUPERGFX_ABSENT", "SUPERGFX_SILENT", "SUPERGFX_OK"):
            self.assertIn(token, system,
                          f"system.py cannot report {token}")

    def test_the_window_packs_every_page_s_action_box(self):
        app = CPU_PAGE.parent.parent / "app.py"
        text = app.read_text(encoding="utf-8")
        self.assertIn("header.pack_start(self.page_actions)", text)
        self.assertIn("_show_page_actions", text,
                      "nothing swaps the header buttons when the page changes")

    def test_the_cpu_page_has_an_apply_button_and_a_banner(self):
        self.assert_has_apply_button(CPU_PAGE)

    def test_the_gpu_page_has_an_apply_button_and_a_banner(self):
        self.assert_has_apply_button(GPU_PAGE)

    def test_the_cpu_page_applies_through_the_tested_plan(self):
        """The page must not carry its own copy of the order."""
        self.assertIn("cpu_apply_plan",
                      CPU_PAGE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
