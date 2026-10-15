.pragma library
// Original GTK help text, preserved from commit 1cc2c8a.
var titles = {
    "Turbo boost": "Off pins every core at its base clock. Worth trying if the fans surge at idle: the EC reads the raw hottest core, and a boost spike hits 85–90 °C for a few milliseconds even while the reported temperature sits near 57 °C — enough to send the fans to the top of the curve.",
    "GPU PowerMizer": "GPU clock behavior for this profile · Applies immediately",
    "Start on boot": "Start the tray and background services at login; keep the window closed",
    "Boot sound": "ASUS firmware plays a short chime through the speakers when the laptop powers on, before any operating system is running. This switch is the firmware's own setting for it — the same one Armoury Crate writes under Windows — so it is not part of a profile and does not change when you switch one. Writing it needs root, so it goes through this app's privileged helper, and it is remembered so the boot-apply service can put it back if a firmware reset loses it.",
    "Panel overdrive": "Panel overdrive drives each pixel transition past its target and lets it settle back, so the display changes state faster and fast motion smears less. It is a trade, not a free improvement: pushing a transition too far shows up as overshoot — a pale ghost leading a moving edge — and how visible that is depends on the panel. Leave it on if motion looks cleaner to you and off if the ghosting does not. Like the boot sound this is the firmware's own setting, so it is not part of a profile and does not change when you switch one. Writing it needs root, so it goes through this app's privileged helper, and it is remembered so the boot-apply service can put it back if a firmware reset loses it.",
    "Check automatically": "Whether this app asks GitHub for a newer release on its own. Never by default -- an update always stays a Check click away either way.",
    "Brightness": "The backlight the keys are lit at, which is separate from their colour. At Off the keyboard is dark whatever effect is chosen below.",
    "Effect": "Applied through rogauracore, which talks to the ASUS Aura controller directly. Effects the firmware animates itself keep running after this app exits; Ambient does not, because it needs the screen.",
    "Speed": "How fast the chosen animation runs.",
    "Flash on charger change": "Both directions: a blink on plug in and a blink on unplug. Unplugging is the half worth noticing — a cable knocked out of the socket is otherwise invisible until the battery is low.",
    "On external power": "Seconds · 0 = off",

}
var tuning = {
    "stapm": "The ceiling the chip settles at once the short-term windows have expired — the limit that decides how hard it runs indefinitely.",
    "fast": "The ceiling for bursts of a few seconds at a time, before the slow and sustained windows take over.",
    "slow": "The ceiling between the fast burst window and the sustained STAPM limit.",
    "temp": "The Tctl temperature the chip throttles itself to hold. Lower backs off sooner and runs quieter.",
    "pl1": "The ceiling the chip settles at once the short-term window has expired — Intel's equivalent of STAPM.",
    "pl2": "The ceiling for short bursts before PL1 takes over — Intel's equivalent of the fast limit.",
    "limits_enabled": "Whether this profile sets the chip's power limits at all.\n\nUnticking it and pressing Apply hands the limits back to the firmware: the firmware is told to reselect its own power table, so the BIOS numbers are what runs from then on. Nothing here is written afterwards either — the background service stops re-asserting these limits too, so they stay the firmware's.\n\nThe governor still follows the profile. Only the power limits are given up; the energy preference, turbo boost and the clock ceiling and floor are still this profile's.\n\nIt covers the Curve Optimizer as well. ryzenadj takes the four limits and the undervolt as a single call, so there is no sending one without the other.\n\nThe firmware reselecting its power table drops the custom fan curves, exactly as changing the power mode does. They are re-applied for you.",
    "thermal_policy": "ASUS firmware power table for this profile. Default (0) uses balanced firmware settings, Overboost (1) selects performance, and Silent (2) selects the quiet table. Follow OS power mode keeps the profile's existing OS mode behavior. Changing this setting also updates the OS power mode and reapplies custom fan curves.",
    "coall": "All-core undervolt. Negative runs cooler and often slightly faster, because the chip has more thermal headroom to boost.\n\nToo negative freezes the machine under load — this laptop locked solid at −20. Move two or three counts at a time and test under load before going further. 0 is stock.",
    "max_freq": "A hard ceiling on the core clock. The cores still idle right down below it; this only stops them going above it. At the top of the range no limit is applied at all.\n\nGreyed out while turbo boost is off: boost off already pins every core at its base clock, so nothing is written here until it is back on.",
    "min_freq": "A floor under the core clock: how far down the cores are allowed to drop while they have work to do. Raise it for snappier response on light load, at the cost of idle power. At the bottom of the range no floor is applied and the driver's own resting minimum stands.\n\nIt is a floor on what the kernel asks for, not a guarantee. Under a load heavy enough to pin the package at its STAPM limit the chip runs below it anyway — it cannot spend watts it has not got, and no clock setting changes that. Raise STAPM if that is what you are hitting.",
    "clock_limit": "A ceiling, not a target — the GPU still idles and boosts freely below it, and this raises no power or thermal limit.\n\nLower it to cut heat and noise. The top of the slider means Default: no limit is applied at all.",
    "clock_offset": "A genuine overclock when positive: this raises the voltage/frequency curve, so the card draws more power and runs hotter at the same clock — unlike the clock ceiling, which cannot do that.\n\nIncrease in small steps and test; too much causes crashes or graphical corruption.",
    "mem_clock_offset": "A genuine overclock when positive: this raises the voltage/frequency curve, so the card draws more power and runs hotter at the same clock — unlike the clock ceiling, which cannot do that.\n\nIncrease in small steps and test; too much causes crashes or graphical corruption.",
    "voltage_boost": "Experimental NVIDIA driver control that gives GPU Boost up to 100% more voltage headroom. 0% is stock.\n\nThis can increase heat and power use and can make the GPU unstable. Increase it only in small steps and test thoroughly.",
    "dyn_boost": "Extra power the firmware may shift from the CPU to the GPU under load. Higher favours the GPU in games; lower leaves more headroom for the CPU. The range is fixed by the firmware.",
    "temp_target": "The temperature the GPU aims to hold before it starts reducing clocks. Lower runs cooler and quieter but throttles sooner. The range is fixed by the firmware."
}
function forTitle(title) { return titles[title] || '' }
function forTuning(key, rows) {
    if (key === 'watts') {
        var row = rows.find(item => item.key === key)
        return row ? 'The board power the card is allowed to draw. This card reports ' + row.min + '–' + row.max + ' W.' : ''
    }
    return tuning[key] || ''
}
