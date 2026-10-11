"""Installer feature reports use the same capabilities as the app."""

import unittest
from unittest.mock import patch

from rogcontrol import feature_report

from rogcontrol.feature_report import feature_rows


class FeatureReportTests(unittest.TestCase):
    def test_each_boolean_app_capability_is_reported(self):
        caps = {
            "fan_curve": True,
            "cpu_power_limits": "rapl",
            "cpu_epp": ["balance_power"],
            "gpu_power_limit": False,
            "cpu_vendor": "intel",
            "aura_id": "1234:5678",
        }
        rows = {key: available for key, _label, available in feature_rows(caps)}
        self.assertEqual(rows, {
            "fan_curve": True,
            "cpu_power_limits": True,
            "cpu_epp": True,
            "gpu_power_limit": False,
        })

    def test_new_capabilities_are_included_without_updating_installer(self):
        rows = feature_rows({"future_feature": True})
        self.assertEqual(rows, [("future_feature", "Future feature", True)])

    def test_live_display_probe_reports_detected_rates_and_current(self):
        with patch('rogcontrol.display_refresh.get_refresh_rates',
                   return_value=([60.0, 165.0], 165.0)):
            key, label, available = feature_report.display_refresh_row()
        self.assertEqual(key, 'display_refresh')
        self.assertTrue(available)
        self.assertIn('60 Hz, 165 Hz', label)
        self.assertIn('current: 165 Hz', label)

    def test_live_display_failure_reports_reason_without_breaking_protocol(self):
        with patch('rogcontrol.display_refresh.get_refresh_rates',
                   side_effect=ValueError('Mirrored panel|unsafe\nlayout')):
            key, label, available = feature_report.display_refresh_row()
        self.assertEqual(key, 'display_refresh')
        self.assertFalse(available)
        self.assertIn('Mirrored panel', label)
        self.assertIn('unsafe layout', label)
        self.assertNotIn('|', label)
        self.assertNotIn('\n', label)

    def test_display_without_detected_modes_is_not_reported_supported(self):
        with patch('rogcontrol.display_refresh.get_refresh_rates',
                   return_value=([], None)):
            _key, label, available = feature_report.display_refresh_row()
        self.assertFalse(available)
        self.assertIn('No supported refresh rates', label)

    def test_installer_feature_detection_includes_live_display_probe(self):
        with patch('rogcontrol.hardware.detect_capabilities',
                   return_value={'nvidia': False, 'nvidia_settings': False}), \
                patch('rogcontrol.hardware.probe_gpu_tuning_capabilities', return_value={}), \
                patch('rogcontrol.graphics_backend.selected_backend', return_value='supergfxctl'), \
                patch('rogcontrol.display_refresh.get_refresh_rates',
                      return_value=([60.0, 165.0], 60.0)):
            rows = feature_report.detect_feature_rows()
        display = next(row for row in rows if row[0] == 'display_refresh')
        self.assertTrue(display[2])
        self.assertIn('165 Hz', display[1])


if __name__ == "__main__":
    unittest.main()
