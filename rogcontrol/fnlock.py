"""Optional in-process Fn Lock. No device discovery or background threads here."""

# Linux input-event-codes.h values keep preferences usable without python-evdev.
F_KEYS = tuple(range(59, 69)) + (87, 88)
MODIFIERS = frozenset((29, 97, 42, 54, 56, 100, 125, 126))
SUPER_KEYS = frozenset((125, 126))
TARGETS = {
    'key:113': 'Mute', 'key:114': 'Volume down', 'key:115': 'Volume up',
    'key:229': 'Keyboard brightness down', 'key:230': 'Keyboard brightness up',
    'key:99': 'Screenshot', 'key:224': 'Display brightness down',
    'key:225': 'Display brightness up', 'key:227': 'Switch display',
    'key:530': 'Toggle touchpad', 'key:142': 'Sleep', 'key:247': 'Airplane mode',
    'key:164': 'Play / pause', 'key:165': 'Previous track', 'key:163': 'Next track',
    'action:toggle': 'Show / hide ROG Control', 'action:profile': 'Cycle profile',
    'action:aura': 'Cycle lighting mode', 'action:brightness_up': 'Lighting brightness up',
    'action:brightness_down': 'Lighting brightness down', 'action:speed_up': 'Lighting speed up',
    'action:speed_down': 'Lighting speed down', 'action:none': 'Do nothing',
}
DEFAULT_MAP = dict(zip(F_KEYS, (
    'key:113', 'key:229', 'key:230', 'action:aura', 'action:profile', 'key:99',
    'key:224', 'key:225', 'key:227', 'key:530', 'key:142', 'key:247')))
SCAN_BINDINGS = {0x38: 'rog', 0x8b: 'rog', 0xb3: 'aura', 0xae: 'performance'}
KEY_BINDINGS = {148: 'rog', 190: 'rog', 202: 'aura', 203: 'performance'}


def preferences(cfg):
    """Return a complete validated Fn Lock preference dictionary."""
    raw = cfg.get('fn_lock', {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    mapping = {str(code): target for code, target in DEFAULT_MAP.items()}
    supplied = raw.get('map', {})
    if isinstance(supplied, dict):
        for code in mapping:
            target = supplied.get(code)
            if isinstance(target, str) and target in TARGETS:
                mapping[code] = target
    return {'enabled': raw.get('enabled') is True,
            'media': raw.get('media') if isinstance(raw.get('media'), bool) else True,
            'map': mapping}


class EventRemapper:
    """Event state machine, independent of evdev and physical device access."""

    def __init__(self, settings, emit, on_action, on_toggle):
        self.emit = emit
        self.on_action = on_action
        self.on_toggle = on_toggle
        self.pressed = {}
        self.modifiers = set()
        self.output_held = {}
        self.scan = None
        self.update(settings)

    def update(self, settings):
        self.settings = preferences({'fn_lock': settings})
        self.bindings_enabled = isinstance(settings, dict) and settings.get('bindings_enabled') is True

    def feed(self, event):
        typ, code, value = event.type, event.code, event.value
        if typ == 0 and code == 3:  # SYN_DROPPED: never replay an incomplete stream.
            self.scan = None
            raise OSError('Keyboard events were lost; Fn Lock stopped')
        if typ == 4 and code == 4:  # Hold MSC_SCAN until its key is resolved.
            self.scan = value
            return
        if typ != 1:
            if self.scan is not None:
                self.emit(4, 4, self.scan)
                self.scan = None
            self.emit(typ, code, value)
            return
        scan, self.scan = self.scan, None
        if value == 1 and code not in self.pressed:
            binding = SCAN_BINDINGS.get(scan) or KEY_BINDINGS.get(code)
            target = f'key:{code}'
            if binding and self.bindings_enabled:
                target = f'action:binding:{binding}'
            elif self.settings['enabled'] and code == 60 and self.modifiers & SUPER_KEYS:
                target = 'toggle'
            elif self.settings['enabled'] and self.settings['media'] and not self.modifiers:
                target = self.settings['map'].get(str(code), target)
            self.pressed[code] = target
            if target == 'toggle':
                self.settings['media'] = not self.settings['media']
                self.on_toggle(self.settings['media'])
            elif target.startswith('action:') and target != 'action:none':
                self.on_action(target[7:])
        target = self.pressed.get(code, f'key:{code}')
        if code in MODIFIERS:
            if value == 1:
                self.modifiers.add(code)
            elif value == 0:
                self.modifiers.discard(code)
        if target.startswith('key:'):
            out = int(target[4:])
            if out == code and scan is not None:
                self.emit(4, 4, scan)
            # Multiple physical keys may share one output key. Release only
            # after the final source releases, avoiding an artificial key-up.
            if value == 1:
                count = self.output_held.get(out, 0)
                self.output_held[out] = count + 1
                if not count:
                    self.emit(1, out, 1)
            elif value == 0:
                count = self.output_held.get(out, 0)
                if count > 1:
                    self.output_held[out] = count - 1
                else:
                    self.output_held.pop(out, None)
                    self.emit(1, out, 0)
            else:
                self.emit(1, out, value)
        if value == 0:
            self.pressed.pop(code, None)

    def close(self):
        try:
            for code in self.output_held:
                self.emit(1, code, 0)
            self.emit(0, 0, 0)
        finally:
            self.output_held.clear()
            self.pressed.clear()
            self.modifiers.clear()
            self.scan = None


class Remapper:
    """Exclusively grab one already-open device and replay through evdev UInput.

    The caller owns the physical device and must close it after this wrapper.
    Construction rejects held keys, and failures always release the grab.
    """

    def __init__(self, device, settings, on_action, on_toggle):
        import evdev  # Optional dependency, never imported for configuration/UI.
        self.device = device
        self.virtual = None
        self.engine = None
        self.grabbed = False
        if device.active_keys():
            raise OSError('Release all keyboard keys before enabling Fn Lock')

        class CapabilityDevice(evdev.InputDevice):
            # from_device requires InputDevice instances. This adapter borrows
            # capabilities only and must never own/close the real descriptor.
            def __init__(self):
                self.ff_effects_count = device.ff_effects_count

            def capabilities(self):
                caps = {typ: list(values) for typ, values in device.capabilities().items()}
                keys = set(caps.get(1, []))
                keys.update(int(target[4:]) for target in TARGETS if target.startswith('key:'))
                caps[1] = sorted(keys)
                return caps

            def __del__(self):
                pass

        try:
            self.virtual = evdev.UInput.from_device(CapabilityDevice(), name='ROG Control virtual keyboard')
            # Recheck after virtual-device setup, which may take time.
            if device.active_keys():
                raise OSError('Release all keyboard keys before enabling Fn Lock')
            device.grab()
            self.grabbed = True
            if device.active_keys():
                raise OSError('Release all keyboard keys before enabling Fn Lock')
            self.engine = EventRemapper(settings, self.virtual.write, on_action, on_toggle)
        except BaseException:
            self.close()
            raise

    def feed(self, event):
        if self.engine is not None:
            self.engine.feed(event)

    def read_feedback(self):
        """Forward compositor LED updates (Caps/Num Lock) to the real keyboard."""
        for event in self.virtual.read():
            if event.type == 17:  # EV_LED
                self.device.write(event.type, event.code, event.value)
                self.device.syn()

    def update(self, settings):
        if self.engine is not None:
            self.engine.update(settings)

    def close(self):
        try:
            if self.engine is not None:
                engine, self.engine = self.engine, None
                engine.close()
        finally:
            try:
                if self.grabbed:
                    self.grabbed = False
                    self.device.ungrab()
            finally:
                if self.virtual is not None:
                    virtual, self.virtual = self.virtual, None
                    virtual.close()
