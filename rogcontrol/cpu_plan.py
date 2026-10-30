"""Pure CPU profile planning, shared by UI and background hardware callers.

This module builds helper commands without probing or changing hardware.
"""

# The steps a CPU apply can make, in the only order that works. This is a
# hardware constraint, not a preference: writing cpufreq's ``boost`` refreshes
# every policy and takes ``scaling_max_freq`` back up to hardware maximum with
# it, so a clock cap written before boost is silently undone. The same order
# is spelled out again in app.py's whole-profile apply and in
# rogcontrol-apply.py, which is why it is worth having one tested definition
# of it here.
#
# ``fwreset`` is first and is the only optional-by-request one -- see
# cpu_apply_plan. The floor goes after the ceiling for a second, separate
# reason: cpuclock has to pull scaling_min_freq down on any policy whose
# floor would sit above the new ceiling, so a floor written first can be
# lowered by the very next step.
#
# EVERY step cpu_apply_plan can emit belongs here, and every one of them
# needs a row, a save entry and a label on the CPU page. A step that reaches
# the hardware with no save entry is written on every Apply and never stored,
# and the profile comes back without it each time the page reloads. That is
# what this tuple is checked against.
CPU_APPLY_STEPS = ("thermal_policy", "fwreset", "limits", "boost", "epp", "clock", "minclock")


def cpu_apply_plan(values, caps=None):
    """The helper calls one CPU apply makes, as ``[(step, args), ...]``.

    Pure: no hardware, no widgets, no subprocess -- it turns a set of wanted
    values plus what the machine can do into the exact argument lists to hand
    to :func:`run_helper`, in order. The CPU page and the tests both use it,
    so the order the page applies in is the order that is tested.

    ``values`` uses the config's own units and names: ``stapm``/``fast``/
    ``slow`` in milliwatts, ``temp`` in degrees, ``coall``, ``boost`` as a
    bool, ``epp`` as a name, ``max_freq`` in kHz with 0 meaning "no ceiling",
    ``min_freq`` in kHz with 0 meaning "no floor", and an optional ASUS
    ``thermal_policy`` integer 0, 1, or 2. ``pl1``/``pl2`` are in
    watts, Intel's equivalent of stapm/fast, sent only when
    ``caps["cpu_power_limits"]`` is "ppt" or "rapl" rather than "ryzenadj".

    A step is left out when the machine cannot do it or the values say
    nothing about it -- a missing key means the caller has no opinion, and
    forcing a default would make every profile carry one.

    ``boost`` being explicitly false, on a machine that has a boost control,
    also drops the ``clock`` step: the cores are pinned at base clock by the
    boost write itself, so a ceiling on top of that is not a limit the
    profile is applying.

    ``limits_enabled`` is the exception to that: it is the CPU page's
    checkbox, false drops the whole ryzenadj step, and missing means true so
    that profiles predating it keep their limits. ``reset_to_firmware`` is
    the other: true adds a step before power limits that makes the firmware reselect its
    own power table, and it is one-shot -- only a caller that has just
    watched the checkbox go off sets it.
    """
    caps = caps or {}
    plan = []
    policy = values.get("thermal_policy")
    if caps.get("throttle_policy") and type(policy) is int and policy in (0, 1, 2):
        plan.append(("thermal_policy", ("cpupolicy", policy)))
    # Only when the caller asks for it: hand the limits back to
    # the firmware. See THROTTLE_POLICY_PATH -- ryzenadj has no reset, so
    # unticking the checkbox has to make the firmware reselect its own power
    # table or the last limits this app wrote keep running until a reboot.
    #
    # ``reset_to_firmware`` is opt-in and one-shot, set by the CPU page on
    # the tick-to-untick transition and by nothing else. It is deliberately
    # NOT derived from ``limits_enabled`` being false: the enforcer builds
    # this plan every 60 seconds, and deriving it would poke the EC -- and
    # drop the fan curves with it -- once a minute for as long as a profile
    # had its limits off.
    #
    # Before the cpufreq steps, not after. The EC reselecting its table can
    # take the energy preference with it, so the EPP and clock writes below
    # go last and leave the governor where the profile wants it, which is
    # the half of "off" that stays this app's business.
    if values.get("reset_to_firmware") and caps.get("fw_power_reset"):
        plan.append(("fwreset", ("cpufwlimits",)))
    # ``limits_enabled`` is the CPU page's checkbox, and it gates the whole
    # ryzenadj call: the helper's ``cpu`` action takes the four limits and
    # the Curve Optimizer offset as one set, so there is no way to send part
    # of it. Off means this profile leaves the chip's power limits to the
    # firmware, and every caller of this plan -- the page, the enforcer's
    # 60-second pass, the tray apply and the hotkey cycler -- stops writing
    # them, which is the only way "off" can mean anything: a limit the page
    # skips and the enforcer re-asserts a minute later is not off.
    #
    # Missing means on, and so does a null: profiles written before the
    # checkbox existed have no such key, and an upgrade must not silently
    # stop applying their limits. Only an explicit false turns it off.
    limits_enabled = values.get("limits_enabled")
    if limits_enabled is None:
        limits_enabled = True
    if caps.get("ryzenadj") and limits_enabled and all(
            key in values for key in ("stapm", "fast", "slow", "temp")):
        plan.append(("limits", ("cpu", values["stapm"], values["fast"],
                                values["slow"], values["temp"],
                                values.get("coall", 0))))
    # Intel: the same "limits" step, a different call. caps["cpu_power_limits"]
    # only ever reads "ppt"/"rapl" here -- it resolves to "ryzenadj" first
    # whenever that backend is available, so this branch is never reached on
    # an AMD machine and the block above is untouched. Same step name as the
    # ryzenadj branch, on purpose: CPU_APPLY_STEPS, STEP_ROWS, STEP_SAVES and
    # STEP_LABELS all key on "limits" and need no branch of their own for it.
    #
    # pl1/pl2 rather than stapm/fast/slow/temp -- Intel has no equivalent of
    # the fast/slow windows or the temperature target (that is an SMU
    # setting), so only the two watts values are sent.
    elif (caps.get("cpu_power_limits") in ("ppt", "rapl") and limits_enabled
          and all(key in values for key in ("pl1", "pl2"))):
        plan.append(("limits", ("cpuppt", values["pl1"], values["pl2"])))
    if "boost" in values and caps.get("cpu_boost"):
        plan.append(("boost", ("cpuboost", 1 if values["boost"] else 0)))
    if values.get("epp") and caps.get("cpu_epp"):
        plan.append(("epp", ("cpuepp", values["epp"])))
    # Last, after boost. 0 means "no ceiling" and still has to be written, or
    # a cap from a previous profile survives the switch.
    #
    # Skipped entirely when turbo boost is off: with boost off every core is
    # already pinned at its base clock, so the ceiling is not a limit this
    # profile is applying and the CPU page greys the row to say so. Nothing
    # stale is left behind by skipping it -- the boost write above is what
    # pins the cores, and it takes scaling_max_freq back to the hardware's
    # own value on every policy as it goes.
    #
    # A missing ``boost`` key means the caller has no opinion on boost, not
    # that boost is off, so the ceiling is still written for it: only an
    # explicit false drops the step. So does a machine with no boost control
    # -- nothing pinned those cores at base clock there, whatever the profile
    # happens to hold, so the ceiling is the only thing capping them.
    boost_on = True
    if caps.get("cpu_boost") and "boost" in values:
        boost_on = bool(values["boost"])
    if "max_freq" in values and caps.get("cpu_clock") and boost_on:
        plan.append(("clock", ("cpuclock", values["max_freq"] or "max")))
    # The floor, after the ceiling. Same cpufreq policies, same "0 means no
    # limit and still has to be written" rule.
    #
    # This was in the tree once, taken out, and is back deliberately, so the
    # measurement that took it out is worth writing down. A 2.4 GHz floor was
    # set and held on every sample through ten minutes of 4K GPU plus all-core
    # CPU load, and the cores ran at 2.0 GHz throughout: the package was
    # pinned at its 45 W STAPM limit, and scaling_min_freq is a floor on what
    # the kernel REQUESTS, not a grant of power the SMU has not got. That
    # reading is correct and it is also the one workload where no cpufreq
    # write of any kind can do anything -- the conclusion drawn from it, that
    # the control does nothing, was too broad.
    #
    # What the floor does do, on this machine, now: on amd-pstate it lands in
    # the CPPC request as min_perf, and outside the power-limited case the
    # hardware holds it. It is visible in sysfs without setting anything --
    # amd-pstate rests policy->min at amd_pstate_lowest_nonlinear_freq
    # (1 492 514 kHz here), and cores that are awake but unloaded read exactly
    # that, not the 421 798 kHz hardware minimum. Raising it raises the clock
    # those cores sit at, which is what the control is for: latency and
    # responsiveness on light and medium load, where the package has watts to
    # spare.
    #
    # The other half of why it looked dead is a plain bug, and it is fixed by
    # this line existing here rather than in four hand-copied applies: the
    # enforcer's 60-second pass writes cpuboost every cycle, a boost write
    # refreshes every policy and resets the floor with the ceiling, and the
    # pass then re-asserted the ceiling only. A floor set from the page
    # survived at most a minute.
    if "min_freq" in values and caps.get("cpu_clock"):
        plan.append(("minclock", ("cpuminclock", values["min_freq"] or "min")))
    return plan
