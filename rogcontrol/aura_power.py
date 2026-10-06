"""Narrow, stdlib-only ASUS G614PR lighting power bridge.

Installed separately as root-owned code; execute via rogcontrol-helper kbdpower.
Mask bits are boot, awake, sleep, shutdown. No caller-selected paths or packets.
"""
import os
from pathlib import Path
import re
import stat
import sys

MODEL = 'ROG Strix G16 G614PR_G614PR'
HID_ID = '0003:00000B05:000019B6'


def build_packet(keyboard, lightbar):
    """Build the 64-byte 0x5d output report using G-Helper's AuraPowerMessage.

    The G614PR exposes keyboard and lightbar zones. Leave the unused logo,
    lid and rear zone flags enabled (firmware defaults).
    """
    for mask in (keyboard, lightbar):
        if type(mask) is not int or not 0 <= mask <= 15:
            raise ValueError('power masks must be integers from 0 through 15')
    keyb = 0x55 | sum((1 << (bit * 2 + 1)) for bit in range(4) if keyboard & (1 << bit))
    bar = sum(value for bit, value in enumerate((2, 5, 8, 16)) if lightbar & (1 << bit))
    return bytes((0x5d, 0xbd, 1, keyb, bar, 0xff, 0xff, 0xff)) + bytes(56)


def _has_power_report(descriptor):
    """Parse HID short items; require exactly 63 output bytes for report 0x5d."""
    report_id = size = count = 0
    stack = []
    bits = 0
    offset = 0
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        # Long items are not used by this controller; fail closed.
        if prefix == 0xfe:
            return False
        length = (0, 1, 2, 4)[prefix & 3]
        if offset + length > len(descriptor):
            return False
        value = int.from_bytes(descriptor[offset:offset + length], 'little')
        offset += length
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            if tag == 7:
                size = value
            elif tag == 8:
                report_id = value
            elif tag == 9:
                count = value
            elif tag == 10:
                stack.append((report_id, size, count))
            elif tag == 11:
                if not stack:
                    return False
                report_id, size, count = stack.pop()
        elif kind == 0 and tag == 9 and report_id == 0x5d:
            bits += size * count
    return bits == 63 * 8 and not stack


def detect_device(root=Path('/')):
    """Read-only detection; return the single supported hidraw Path or None.

    A filesystem root can be supplied for fixture-based probing. The privileged
    writer always probes the real system and never accepts a root from its CLI.
    """
    root = Path(root)
    try:
        if (root / 'sys/class/dmi/id/product_name').read_text().strip() != MODEL:
            return None
        matches = []
        for entry in sorted((root / 'sys/class/hidraw').glob('hidraw*')):
            if not re.fullmatch(r'hidraw[0-9]+', entry.name):
                continue
            try:
                fields = dict(line.split('=', 1) for line in
                              (entry / 'device/uevent').read_text().splitlines() if '=' in line)
                if fields.get('HID_ID', '').upper() != HID_ID:
                    continue
                if _has_power_report((entry / 'device/report_descriptor').read_bytes()):
                    matches.append(root / 'dev' / entry.name)
            except (OSError, ValueError):
                continue
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError):
        return None


def write_power(keyboard, lightbar):
    """Write once to a validated character device. The shell helper owns locking."""
    packet = build_packet(keyboard, lightbar)
    device = detect_device()
    if device is None:
        raise OSError('keyboard power control is unsupported or its HID interface is unavailable')
    fd = os.open(device, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode):
            raise OSError('keyboard interface is not a character device')
        expected = (Path('/sys/class/hidraw') / device.name / 'dev').read_text().strip()
        if expected != f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' or detect_device() != device:
            raise OSError('keyboard interface changed during validation')
        if os.write(fd, packet) != len(packet):
            raise OSError('incomplete keyboard power report write')
    finally:
        os.close(fd)


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 2 or any(not re.fullmatch(r'(?:[0-9]|1[0-5])', value) for value in args):
        print('usage: aura_power.py <keyboard mask 0..15> <lightbar mask 0..15>', file=sys.stderr)
        return 1
    try:
        write_power(*(int(value) for value in args))
    except (OSError, ValueError) as exc:
        print(f'keyboard power: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
