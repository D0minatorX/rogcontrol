"""The power bridge rejects unknown hardware before any device write."""
import importlib.util
import os
import stat
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'rogcontrol/aura_power.py'

class AuraPowerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.exists(), 'Aura power bridge must be implemented')
        spec = importlib.util.spec_from_file_location('aura_power_under_test', MODULE)
        self.power = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.power)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put('sys/class/dmi/id/product_name', 'ROG Strix G16 G614PR_G614PR\n')

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def device(self, name='hidraw3', descriptor=bytes.fromhex('855d7508953f9102'), identity='0003:00000B05:000019B6'):
        self.put(f'sys/class/hidraw/{name}/device/uevent', f'HID_ID={identity}\n')
        self.put(f'sys/class/hidraw/{name}/device/report_descriptor', descriptor)

    def test_packet_each_independent_state_and_preserved_zones(self):
        for mask, keyb, bar in [(0,0x55,0),(1,0x57,2),(2,0x5d,5),(4,0x75,8),(8,0xd5,16),(15,0xff,31)]:
            with self.subTest(mask=mask):
                self.assertEqual(self.power.build_packet(mask,mask), bytes([0x5d,0xbd,1,keyb,bar,255,255,255])+bytes(56))
        self.assertEqual(self.power.build_packet(0,15)[3:5], bytes([0x55,31]))

    def test_masks_reject_unbounded_non_integer_and_bool(self):
        for bad in [-1,16,1000000,'1',True,None,1.0]:
            for masks in [(bad,0),(0,bad)]:
                with self.subTest(masks=masks), self.assertRaises(ValueError):
                    self.power.build_packet(*masks)

    def test_selects_matching_output_interface_only(self):
        self.device('hidraw0', bytes.fromhex('855d7508953fb102'))
        self.device()
        self.assertEqual(self.power.detect_device(self.root), self.root / 'dev/hidraw3')

    def test_rejects_wrong_model_identity_size_and_truncated_descriptor(self):
        for desc, identity in [(bytes.fromhex('855d750895409102'),'0003:00000B05:000019B6'),
                               (bytes.fromhex('855d7508953f9102'),'0003:0000046D:000019B6'),
                               (bytes.fromhex('855d7508953f910226'),'0003:00000B05:000019B6')]:
            self.device(descriptor=desc, identity=identity)
            self.assertIsNone(self.power.detect_device(self.root))
        self.device()
        self.put('sys/class/dmi/id/product_name','ROG Other')
        self.assertIsNone(self.power.detect_device(self.root))

    def test_ambiguous_devices_are_rejected(self):
        self.device('hidraw1')
        self.device('hidraw2')
        self.assertIsNone(self.power.detect_device(self.root))

    def test_invalid_cli_cannot_supply_a_device_path(self):
        for args in [('0','0','/dev/hidraw0'),('/dev/hidraw0','0'),('16','0'),('-1','0'),('','0')]:
            result = subprocess.run(['/usr/bin/python3','-I',str(MODULE),*args], capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn(b'Traceback',result.stderr)

    def test_regular_file_is_never_written_as_device(self):
        path = self.put('dev/hidraw3', b'unchanged')
        with patch.object(self.power,'detect_device',return_value=path):
            with self.assertRaises(OSError):
                self.power.write_power(15,15)
        self.assertEqual(path.read_bytes(),b'unchanged')

    def test_symlink_device_is_rejected_without_touching_target(self):
        target = self.put('target', b'unchanged')
        link = self.root / 'hidraw3'
        link.symlink_to(target)
        with patch.object(self.power, 'detect_device', return_value=link):
            with self.assertRaises(OSError):
                self.power.write_power(15, 15)
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_validated_write_uses_one_full_report_and_closes_on_short_write(self):
        # The file stands in for the device; only its hardware identity is mocked.
        target = self.put('hidraw3', b'')
        info = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(240, 3))
        with patch.object(self.power, 'detect_device', return_value=target), \
             patch.object(self.power.os, 'fstat', return_value=info), \
             patch.object(self.power.Path, 'read_text', return_value='240:3'):
            self.power.write_power(2, 0)
            self.assertEqual(target.read_bytes(), bytes.fromhex('5dbd015d00ffffff') + bytes(56))
            opened = []
            real_open = os.open
            def open_file(*args):
                fd = real_open(*args)
                opened.append(fd)
                return fd
            with patch.object(self.power.os, 'open', side_effect=open_file), \
                 patch.object(self.power.os, 'write', return_value=8):
                with self.assertRaisesRegex(OSError, 'incomplete'):
                    self.power.write_power(2, 0)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_helper_refuses_rgb_write_when_locking_unavailable(self):
        fake = self.put('bin/rogauracore', '#!/bin/sh\necho DEVICE_WRITE\n')
        fake.chmod(0o755)
        (fake.parent / 'id').symlink_to('/usr/bin/id')
        for args in [('kbdrgb', 'single_static', '1', '2', '3'), ('kbdrgb_brightness', '2')]:
            with self.subTest(args=args):
                result = subprocess.run(['/bin/bash', str(MODULE.parent / 'rogcontrol-helper'), *args],
                                        env={**os.environ, 'PATH': str(fake.parent),
                                             'ROGCONTROL_HELPER_LOCK_DIR': str(self.root)},
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'DEVICE_WRITE', result.stdout)
                self.assertIn(b'lock', result.stderr)

    def test_missing_device_never_opens_anything(self):
        with patch.object(self.power,'detect_device',return_value=None), patch.object(self.power.os,'open',side_effect=AssertionError('unexpected open')):
            with self.assertRaises(OSError):
                self.power.write_power(0,0)

if __name__ == '__main__':
    unittest.main()
