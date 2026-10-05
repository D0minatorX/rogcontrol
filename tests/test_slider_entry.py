"""Exact numeric input shares the slider's limits and user-change contract."""
import unittest
from unittest.mock import Mock
from rogcontrol.ui import Adw, Gdk
from rogcontrol.widgets.slider_row import SliderRow


class SliderEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def row(self, **kwargs):
        row = SliderRow(settle_ms=0, **kwargs)
        self.addCleanup(row._on_destroy, None)
        return row

    def enter(self, row, text):
        row.value_entry.set_text(text)
        row.value_entry.emit('activate')

    def test_typing_waits_for_accept_and_then_emits_once(self):
        row = self.row(minimum=10, maximum=150, unit='W')
        changed = Mock(); row.connect('changed', changed)
        row.value_entry.set_text('85')
        self.assertEqual(row.get_value(), 10)
        changed.assert_not_called()
        row.value_entry.emit('activate')
        self.assertEqual(row.get_value(), 85)
        changed.assert_called_once_with(row, 85)
        self.assertEqual(row.get_display_value(), '85 W')

    def test_range_steps_negative_values_and_decimals(self):
        for options, typed, expected in (
            ({'minimum': -1000, 'maximum': 1000, 'step': 50}, '−125', -100),
            ({'minimum': 0.4, 'maximum': 5.4, 'step': .1, 'digits': 1}, '3.26', 3.3),
            ({'minimum': 10, 'maximum': 150}, '999', 150),
            ({'minimum': 10, 'maximum': 150}, '-50', 10),
        ):
            with self.subTest(typed=typed):
                row = self.row(**options)
                self.enter(row, typed)
                self.assertAlmostEqual(row.get_value(), expected)
                self.assertFalse(row.input_error.get_visible())

    def test_invalid_input_never_changes_the_setting(self):
        row = self.row()
        row.set_value(40)
        changed = Mock();row.connect('changed', changed)
        for invalid in ('', '-', 'oops', 'nan', 'inf', '1e999'):
            with self.subTest(invalid=invalid):
                self.enter(row, invalid)
                self.assertEqual(row.get_value(), 40)
                self.assertTrue(row.input_error.get_visible())
                changed.assert_not_called()
        self.enter(row, '50')
        self.assertFalse(row.input_error.get_visible())
        changed.assert_called_once_with(row, 50)

    def test_profile_load_and_slider_keep_entry_in_sync(self):
        row = self.row(unit='MHz')
        changed = Mock();row.connect('changed', changed)
        row.set_value(30)
        self.assertEqual(row.value_entry.get_text(), '30')
        changed.assert_not_called()
        row.value_entry.set_text('99')
        row.set_value(30)  # Discard must clear typing even if adjustment is unchanged.
        self.assertEqual(row.value_entry.get_text(), '30')
        changed.assert_not_called()
        row.get_adjustment().set_value(60)
        self.assertEqual(row.value_entry.get_text(), '60')
        changed.assert_called_once_with(row, 60)

    def test_escape_cancels_and_focus_leave_accepts(self):
        row = self.row()
        row.set_value(30)
        row.value_entry.set_text('80')
        self.assertTrue(row._on_entry_key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(row.value_entry.get_text(), '30')
        row.value_entry.set_text('75')
        row._on_entry_focus_leave(None)
        self.assertEqual(row.get_value(), 75)

    def test_named_keyboard_levels_remain_visible_beside_numbers(self):
        from rogcontrol.pages.keyboard import _LevelRow
        row = _LevelRow({0: 'Off', 1: 'Low', 2: 'Medium', 3: 'Full'},
                        minimum=0, maximum=3, settle_ms=0)
        self.addCleanup(row._on_destroy, None)
        self.enter(row, '2')
        self.assertEqual(row.value_entry.get_text(), '2')
        self.assertEqual(row._unit_label.get_text(), 'Medium')
        self.enter(row, 'invalid')
        self.assertIn('0 to 3', row.input_error.get_text())

    def test_runtime_limits_are_used_for_typed_values(self):
        row = self.row(minimum=0, maximum=1000, step=25)
        row.get_adjustment().set_upper(200)
        self.enter(row, '500')
        self.assertEqual(row.get_value(), 200)


if __name__ == '__main__':
    unittest.main()
