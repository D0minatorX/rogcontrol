"""Shared page behavior and graphics-backend boundaries (GTK display required).

Hardware calls and config writes are mocked; these tests never apply settings
or switch the real graphics mode.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rogcontrol import config, hardware
from rogcontrol.ui import Adw
from rogcontrol.pages import gpu, gpu_supergfx, system, system_supergfx


class PageBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        for name, value in {
            "dgpu_available": True,
            "read_nv_dynamic_boost": 10,
            "read_nv_temp_target": 80,
            "asusd_uninstall_command": None,
            "read_log_tail": "",
            "read_fan_boost": None,
        }.items():
            self.enterContext(patch.object(hardware, name, return_value=value))
        self.enterContext(patch.object(config, "save_config"))

    def window(self, caps):
        profile = {"gpu": {"watts": 90, "clock_offset": 0}}
        return SimpleNamespace(
            caps=caps, config={"profiles": {"Balanced": profile}},
            current_profile=lambda: profile,
            current_profile_name=lambda: "Balanced",
            apply_async=Mock(), toast=Mock(), reload_pages=Mock(),
            claim_hardware=Mock(return_value=True), release_hardware=Mock(),
            _gpu_runtime_ready=False, gpu_mode_changed=Mock(),
        )

    def page(self, cls, caps):
        page = cls(self.window(caps))
        self.addCleanup(page._on_destroy, None)
        return page

    def test_gpu_construction_and_revert_do_not_write_hardware(self):
        for cls in (gpu.GpuPage, gpu_supergfx.GpuPage):
            with self.subTest(backend=cls.__module__), \
                 patch.object(hardware, "run_helper") as helper:
                page = self.page(cls, {"gpu_power_limit": True})
                page.rows["watts"].set_value(80)
                self.assertIn("watts", page._dirty_keys())
                page._on_revert_clicked(None)
                self.assertEqual(page.rows["watts"].get_value(), 90)
                self.assertFalse(page._dirty_keys())
                helper.assert_not_called()
                page.window.claim_hardware.assert_not_called()
                self.assertEqual(page.rows["dyn_boost"].get_subtitle(),
                                 "Extra GPU power allowance from the shared CPU/GPU budget")

    def test_apply_preserves_order_and_continues_after_a_failure(self):
        wanted = [("watts", 80), ("clock_offset", 100)]
        for cls in (gpu.GpuPage, gpu_supergfx.GpuPage):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls, {})
                with patch.object(page, "_write", side_effect=[
                        (False, "not supported"), (True, "")]) as write:
                    result = page._apply_worker(wanted)
                self.assertEqual([call.args for call in write.call_args_list], wanted)
                self.assertEqual(result, [("watts", 80, False, "not supported"),
                                          ("clock_offset", 100, True, "")])

    def test_failed_apply_restores_only_failed_controls(self):
        for cls in (gpu.GpuPage, gpu_supergfx.GpuPage):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls, {"gpu_power_limit": True,
                                      "nvidia_core_clock_offset": True})
                page.rows["watts"].set_value(80)
                page.rows["clock_offset"].set_value(100)
                with patch.object(config, "save_deferred", return_value=None) as save:
                    page._on_applied("Balanced", [
                        ("watts", 80, False, "refused"),
                        ("clock_offset", 100, True, "")], None)
                self.assertEqual(page.rows["watts"].get_value(), 90)
                self.assertEqual(page.rows["clock_offset"].get_value(), 100)
                self.assertEqual(save.call_args.args[1:4],
                                 ("Balanced", "gpu", {"clock_offset": 100}))
                page.window.release_hardware.assert_called_once()

    def test_voltage_confirmation_remains_required_for_both_backends(self):
        for cls in (gpu.GpuPage, gpu_supergfx.GpuPage):
            with self.subTest(backend=cls.__module__):
                page = self.page(cls, {"nvidia_voltage_boost": True})
                page.rows["voltage_boost"].set_value(20)
                with patch.object(Adw, "AlertDialog") as dialog:
                    page._on_apply_clicked(None)
                dialog.assert_called_once()
                page.window.claim_hardware.assert_not_called()

    def test_cardwire_blocks_direct_controls_but_keeps_firmware_controls(self):
        page = self.page(gpu.GpuPage, {
            "gpu_power_limit": True, "nv_dynamic_boost": True})
        page.set_nvidia_accessible(False)
        self.assertFalse(page.rows["watts"].get_sensitive())
        self.assertTrue(page.rows["dyn_boost"].get_sensitive())
        self.assertEqual(set(dict(page._pending_values())), {"dyn_boost"})
        page.set_nvidia_accessible(True)
        self.assertIn("watts", dict(page._pending_values()))

    def test_sampling_does_not_wake_a_suspended_gpu(self):
        for cls in (gpu.GpuPage, gpu_supergfx.GpuPage):
            with self.subTest(backend=cls.__module__), \
                 patch.object(hardware, "dgpu_is_suspended", return_value=True), \
                 patch.object(hardware, "nvidia_driver_loaded", return_value=True), \
                 patch.object(hardware, "read_fan_rpms", return_value={"2": 1200}), \
                 patch.object(hardware, "read_nvidia_stats") as stats:
                page = self.page(cls, {"nvidia": True})
                sample = page._sample()
                page._render(sample)
                stats.assert_not_called()
                self.assertTrue(sample["dgpu_suspended"])
                self.assertEqual(sample["fan_rpm"], 1200)
                self.assertEqual(page.temp_cell.value.get_text(), "Idle")

    def test_cardwire_live_switch_does_not_offer_reboot(self):
        page = self.page(gpu.GpuPage, {})
        with patch.object(gpu.GLib, "timeout_add_seconds") as timer, \
             patch.object(Adw, "AlertDialog") as dialog:
            page._on_mode_applied("Hybrid", (True, ""), None)
        dialog.assert_not_called()
        self.assertEqual(timer.call_args.args[0], 2)
        self.assertIn("no logout", page.window.toast.call_args.args[0])

    def test_supergfx_mux_exit_offers_hybrid_before_integrated(self):
        page = self.page(gpu_supergfx.GpuPage, {})
        page.current_mode = "AsusMuxDgpu"
        row = Mock()
        row.get_selected_item.return_value.get_string.return_value = "Integrated"
        with patch.object(hardware, "mode_needs_hybrid_first", return_value=True), \
             patch.object(page, "_offer_hybrid_first") as offer:
            page._on_mode_changed(row, None)
        offer.assert_called_once_with("Integrated")
        self.assertFalse(page._switching)

    def test_supergfx_reboot_and_logout_paths_stay_separate(self):
        page = self.page(gpu_supergfx.GpuPage, {})
        for reboot in (True, False):
            with self.subTest(reboot=reboot), \
                 patch.object(page, "_switch_needs_reboot", return_value=reboot), \
                 patch.object(page, "_ask_to_reboot") as ask, \
                 patch.object(gpu_supergfx.GLib, "timeout_add_seconds") as timer:
                page._on_mode_applied("Hybrid", (True, ""), None)
                self.assertEqual(ask.called, reboot)
                self.assertEqual(timer.called, not reboot)
                if not reboot:
                    self.assertEqual(timer.call_args.args[0], 20)

    def test_system_daemon_states_and_backend_help_text(self):
        for module, key, prefix in ((system, "cardwire", "cardwire"),
                                     (system_supergfx, "supergfxctl", "supergfx")):
            with self.subTest(backend=key):
                page = self.page(module.SystemPage, {key: True, "psr_toggle": True})
                render = getattr(page, "_render_" + prefix)
                value = getattr(page, prefix + "_value")
                enable = getattr(page, prefix + "_enable_row")
                render(None, None, {"has_unit": True, "active": False})
                self.assertEqual(value.get_text(), "stopped")
                self.assertTrue(enable.get_visible())
                render("Hybrid", None)
                self.assertEqual(value.get_text(), "running")
                self.assertFalse(enable.get_visible())
                self.assertIn("Cardwire" if key == "cardwire" else "supergfxctl",
                              page._hardware_summary())
                self.assertEqual(page.psr_row.get_subtitle(), module.PSR_SUBTITLE)
                self.assertEqual(page.psr_row.get_tooltip_text(), module.PSR_TOOLTIP)
                page.caps[key] = False
                render(None, None)
                self.assertEqual(value.get_text(), "not installed")

    def test_system_psr_render_and_apply_keep_backend_subtitles(self):
        for module in (system, system_supergfx):
            with self.subTest(backend=module.__name__):
                page = self.page(module.SystemPage, {"psr_toggle": True})
                page._render_psr(True, False, None)
                self.assertEqual(page.psr_row.get_subtitle(), module.PSR_SUBTITLE)
                self.assertFalse(page.psr_row.get_active())
                self.assertTrue(page.psr_pending_row.get_visible())
                with patch.object(page, "_ask_to_reboot_for_psr") as ask:
                    page._on_psr_set(True, (True, ""), None)
                ask.assert_called_once_with(True)
                self.assertFalse(page._psr_busy)
                self.assertEqual(page.psr_row.get_subtitle(), module.PSR_SUBTITLE)

    def test_system_cardwire_x11_remains_blocked(self):
        page = self.page(system.SystemPage, {"cardwire": True, "cardwire_wayland": False})
        page._render_cardwire("Hybrid", None)
        self.assertEqual(page.cardwire_value.get_text(), "X11 session")
        self.assertFalse(page.cardwire_enable_row.get_visible())

    def test_system_sampling_skips_daemon_state_when_it_answers(self):
        for cls, key, reader in (
                (system.SystemPage, "cardwire", "read_cardwired_state"),
                (system_supergfx.SystemPage, "supergfxctl", "read_supergfxd_state")):
            with self.subTest(backend=key), \
                 patch.object(hardware, "read_gpu_mode", return_value="Hybrid"), \
                 patch.object(hardware, reader) as state, \
                 patch.object(hardware, "read_power_mode", return_value="balanced"), \
                 patch.object(hardware, "read_asusd_state", return_value={}):
                page = self.page(cls, {key: True})
                sample = page._sample()
                page._render(sample)
                state.assert_not_called()
                self.assertEqual(sample["gpu_mode"], "Hybrid")
                self.assertEqual(sample["power_mode"], "balanced")


if __name__ == "__main__":
    unittest.main()
