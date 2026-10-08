"""Small background conveniences, independent of slow fan/profile writes."""

from . import charge_once, config, display_refresh, hardware, keyboard_power


def run(stop_event):
    """Reconcile charging, display and lighting policies every five seconds."""
    lighting = keyboard_power.Controller(reconnect_grace=10)
    while not stop_event.is_set():
        try:
            ok, message = charge_once.tick()
            if not ok:
                hardware.log(message, 'WARN', source='automation',
                             dedupe_key='charge-once')
        except Exception as error:
            hardware.log(f'One-time charging: {error}', 'ERROR',
                         source='automation', dedupe_key='charge-once')
        try:
            cfg = config.load_config()
            connected, kind = hardware.read_power_source()
            source = (('usbc' if kind == 'usb' else 'ac') if connected
                      else 'battery' if connected is False else None)
            display_refresh.tick(cfg, source)
        except Exception as error:
            hardware.log(f'Automatic display refresh: {error}', 'ERROR',
                         source='automation', dedupe_key='display-refresh')
        try:
            cfg = config.load_config()
            ok, message = lighting.tick(cfg, hardware.is_ac_connected())
            if not ok:
                hardware.log(message, 'WARN', source='automation',
                             dedupe_key='keyboard-power')
        except Exception as error:
            hardware.log(f'Lighting power states: {error}', 'ERROR',
                         source='automation', dedupe_key='keyboard-power')
        if stop_event.wait(5):
            break
