import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, staticClasses, ToggleField } from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";
type State = { protocol_version?: number; profiles: string[]; current_profile: string | null; cpu: { boost: boolean | null; max_freq_mhz: number }; gpu?: { clock_limit_mhz: number | null }; capabilities: { boost: boolean; max_freq: boolean; clock_range_mhz: [number, number] | null; gpu_clock_range_mhz?: [number, number] | null } };
type Telemetry = { cpu_temp: number | null; gpu_temp: number | null; gpu_status?: string; cpu_usage?: number | null; cpu_clock_mhz?: number | null; cpu_power_w?: number | null; gpu_usage?: number | null; gpu_clock_mhz?: number | null; gpu_power_w?: number | null; fans: Record<string, number | null>; current_profile?: string | null };
type Result = { ok: boolean; error?: string; message?: string; available?: boolean; version?: string };
const getInstalledVersion = callable<[], Result>("get_installed_version");
const getState = callable<[], State | Result>("get_state");
const getTelemetry = callable<[], Telemetry | Result>("get_telemetry");
const setProfile = callable<[string], Result>("set_profile");
const setCpuBoost = callable<[boolean], Result>("set_cpu_boost");
const setCpuMaxFreq = callable<[number], Result>("set_cpu_max_freq");
const setGpuClockLimit = callable<[number], Result>("set_gpu_clock_limit");
const checkForUpdate = callable<[], Result>("check_for_update");
const installUpdate = callable<[], Result>("install_update");
const isResult = (v: State | Telemetry | Result): v is Result => "ok" in v;
const reading = (v: number | null | undefined, unit: string, decimals = 0) => v == null || !Number.isFinite(v) ? "Unavailable" : `${v.toFixed(decimals)} ${unit}`;
function ProcessorCard({ name, temperature, usage, clock, power, sleeping, stale }: {
  name: string; temperature?: number | null; usage?: number | null;
  clock?: number | null; power?: number | null; sleeping?: boolean; stale: boolean;
}) {
  const validUsage = usage != null && Number.isFinite(usage);
  const metric = (label: string, value: string) => <div style={{ minWidth: 0 }}>
    <div style={{ fontSize: "11px", color: "#b8c2d0", marginBottom: "3px" }}>{label}</div>
    <div style={{ fontSize: "14px", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{sleeping ? "—" : value}</div>
  </div>;
  return <div style={{ border: "1px solid rgba(255,255,255,0.1)", borderRadius: "10px", padding: "12px", background: "rgba(0,0,0,0.18)", opacity: stale ? 0.6 : 1 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "10px" }}>
      <span style={{ fontSize: "15px", fontWeight: 700 }}>{name}</span>
      <span style={{ color: sleeping ? "#b8c2d0" : "#e3edf8", fontSize: "14px", fontVariantNumeric: "tabular-nums" }}>{sleeping ? "Sleeping" : reading(temperature, "°C")}</span>
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 16px" }}>
      {metric("Usage", reading(usage, "%"))}
      {metric(name === "CPU" ? "Peak core clock" : "Core clock", reading(clock, "MHz"))}
      {metric(name === "CPU" ? "Package power" : "GPU power", reading(power, "W", 1))}
      {metric("Status", sleeping ? "Sleeping" : stale ? "Stale" : [temperature, usage, clock, power].some(v => v != null && Number.isFinite(v)) ? "Live" : "Unavailable")}
    </div>
    <div aria-hidden="true" style={{ height: "3px", borderRadius: "2px", marginTop: "12px", background: "rgba(255,255,255,0.1)", overflow: "hidden" }}>
      <div style={{ height: "100%", width: `${sleeping || !validUsage ? 0 : Math.max(0, Math.min(100, usage!))}%`, background: name === "CPU" ? "#66c0f4" : "#a5d78b", transition: "width 0.3s ease" }} />
    </div>
  </div>;
}
function Content() {
  const [state, setState] = useState<State | null>(null);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [sampleAt, setSampleAt] = useState<number | null>(null);
  const [sampleError, setSampleError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [feedback, setFeedback] = useState("profile");
  const [error, setError] = useState("");
  const [cpuClock, setCpuClock] = useState(0);
  const [gpuClock, setGpuClock] = useState(0);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  const profile = useRef<string | null>(null);
  const busyRef = useRef(false);
  const previousGpuStatus = useRef<string | undefined>(undefined);
  const refresh = useCallback(async () => {
    const value = await getState();
    if (isResult(value)) throw new Error(value.error || "Could not connect to ROG Control");
    setState(value); profile.current = value.current_profile;
    setCpuClock(value.cpu.max_freq_mhz); setGpuClock(value.gpu?.clock_limit_mhz || 0);
  }, []);
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
    void getInstalledVersion().then((result) => setVersion(result.ok ? result.version || null : null)).catch(() => setVersion(null));
  }, [refresh]);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const started = Date.now();
      try {
        const value = await getTelemetry();
        if (isResult(value)) throw new Error(value.error || "Could not read sensors");
        if (!active) return;
        setTelemetry(value); setSampleAt(Date.now()); setSampleError("");
        const woke = previousGpuStatus.current === "sleeping" && value.gpu_status === "available";
        previousGpuStatus.current = value.gpu_status;
        if (!busyRef.current && (woke || (value.current_profile !== undefined && value.current_profile !== profile.current))) await refresh();
      } catch (e) { if (active) setSampleError(String(e)); }
      if (active) timer = setTimeout(() => void poll(), Math.max(0, 1000 - (Date.now() - started)));
    };
    void poll(); const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { active = false; clearTimeout(timer); clearInterval(clock); };
  }, [refresh]);
  const perform = async (pending: string, action: () => Promise<Result>, success: string, section = "profile") => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setFeedback(section); setMessage(pending); setError("");
    try { const result = await action(); if (!result.ok) throw new Error(result.error || "Setting failed"); await refresh(); setMessage(success); }
    catch (e) { setMessage(""); setError(String(e)); try { await refresh(); } catch { /* retain action error */ } }
    finally { busyRef.current = false; setBusy(false); }
  };
  if (!state) return <PanelSection title="ROG Control"><PanelSectionRow><ButtonItem layout="below" onClick={() => void refresh().then(() => setError("")).catch((e) => setError(String(e)))}>{error || "Connecting to ROG Control…"} — retry</ButtonItem></PanelSectionRow></PanelSection>;
  const cpu = state.capabilities.clock_range_mhz; const gpu = state.capabilities.gpu_clock_range_mhz;
  const age = sampleAt == null ? null : Math.max(0, Math.floor((now - sampleAt) / 1000));
  return <>
    <PanelSection title="Live hardware"><PanelSectionRow><div style={{ padding: "4px 0", width: "100%" }}>
      <div style={{ display: "grid", gap: "10px" }}>
        <ProcessorCard name="CPU" temperature={telemetry?.cpu_temp} usage={telemetry?.cpu_usage} clock={telemetry?.cpu_clock_mhz} power={telemetry?.cpu_power_w} stale={Boolean(sampleError) || age != null && age > 5} />
        <ProcessorCard name="GPU" temperature={telemetry?.gpu_temp} usage={telemetry?.gpu_usage} clock={telemetry?.gpu_clock_mhz} power={telemetry?.gpu_power_w} sleeping={telemetry?.gpu_status === "sleeping"} stale={Boolean(sampleError) || age != null && age > 5} />
        <div style={{ borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "10px" }}>
          <div style={{ color: "#b8c2d0", fontSize: "11px", marginBottom: "6px" }}>FAN SPEEDS</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "8px", fontSize: "12px", fontVariantNumeric: "tabular-nums" }}>
            {[ ["CPU", "1"], ["GPU", "2"], ["Mid", "3"] ].map(([label, id]) => <div key={id}><div style={{ color: "#b8c2d0", marginBottom: "3px" }}>{label}</div>{reading(telemetry?.fans?.[id], "RPM")}</div>)}
          </div>
        </div>
      </div>
      <div role="status" style={{ color: "#b8c2d0", fontSize: "11px", marginTop: "10px", overflowWrap: "anywhere" }}>{sampleError ? `Readings unavailable · ${age == null ? "No sample" : `Last sample ${age}s ago`}` : age == null ? "Waiting for readings…" : `${age > 5 ? "Stale · " : "Updated "}${age}s ago`}</div>
      {sampleError && <div style={{ color: "#b8c2d0", fontSize: "11px", marginTop: "4px", overflowWrap: "anywhere" }}>{sampleError}</div>}
    </div></PanelSectionRow></PanelSection>
    <PanelSection title="Profile"><PanelSectionRow><DropdownItem label="Active profile" description={state.protocol_version !== 2 ? "Update ROG Control to enable profile feedback." : undefined} disabled={busy || !state.profiles.length || state.protocol_version !== 2} selectedOption={state.current_profile} rgOptions={state.profiles.map((name) => ({ label: name, data: name }))} onChange={(v) => { if (v.data !== state.current_profile) void perform(`Applying ${v.data}…`, () => setProfile(v.data), telemetry?.gpu_status === "sleeping" ? `Profile ${v.data} applied; GPU settings may wait for wake` : `Profile ${v.data} applied`); }} /></PanelSectionRow>
      {feedback === "profile" && (message || error) && <PanelSectionRow><div style={{ padding: "4px 0" }}>{error || message}</div></PanelSectionRow>}</PanelSection>
    <PanelSection title="CPU">
      {feedback === "cpu" && (message || error) && <PanelSectionRow><div style={{ padding: "4px 0" }}>{error || message}</div></PanelSectionRow>}
      {state.capabilities.boost && <PanelSectionRow><ToggleField label="Turbo Boost" checked={Boolean(state.cpu.boost)} disabled={busy} onChange={(value) => void perform("Applying CPU boost…", () => setCpuBoost(value), "CPU boost applied", "cpu")} /></PanelSectionRow>}
      {state.capabilities.max_freq && cpu && <>
        <PanelSectionRow><SliderField label="Clock ceiling" description={cpuClock ? `${cpuClock} MHz` : "No limit"} min={cpu[0]} max={cpu[1]} step={100} disabled={busy} value={Math.max(cpu[0], Math.min(cpu[1], cpuClock || cpu[1]))} onChange={setCpuClock} /></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={busy || !cpuClock} onClick={() => void perform("Applying CPU ceiling…", () => setCpuMaxFreq(cpuClock), "CPU ceiling applied", "cpu")}>Apply CPU ceiling</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => void perform("Clearing CPU ceiling…", () => setCpuMaxFreq(0), "CPU ceiling cleared", "cpu")}>No CPU limit</ButtonItem></PanelSectionRow>
      </>}
    </PanelSection>
    <PanelSection title="GPU">
      {feedback === "gpu" && (message || error) && <PanelSectionRow><div style={{ padding: "4px 0" }}>{error || message}</div></PanelSectionRow>}
      {gpu ? <>
        <PanelSectionRow><SliderField label="Clock ceiling" description={gpuClock && gpuClock < gpu[1] ? `${gpuClock} MHz` : "No limit"} min={gpu[0]} max={gpu[1]} step={15} disabled={busy} value={Math.max(gpu[0], Math.min(gpu[1], gpuClock || gpu[1]))} onChange={setGpuClock} /></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={busy || !gpuClock} onClick={() => void perform("Applying GPU ceiling…", () => setGpuClockLimit(gpuClock), "GPU ceiling applied and saved", "gpu")}>Apply GPU ceiling</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => void perform("Clearing GPU ceiling…", () => setGpuClockLimit(0), "GPU ceiling cleared", "gpu")}>No GPU limit</ButtonItem></PanelSectionRow>
      </> : <PanelSectionRow><div style={{ padding: "4px 0" }}>{telemetry?.gpu_status === "sleeping" ? "Wake the GPU, then refresh controls." : "GPU clock control unavailable. Refresh when GPU access is restored."}</div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => void perform("Refreshing…", async () => ({ ok: true }), "Controls refreshed", "gpu")}>Refresh controls</ButtonItem></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Updates"><PanelSectionRow><div style={{ opacity: 0.75, padding: "4px 0" }}>Installed: {version ? `v${version}` : "Unavailable"}</div></PanelSectionRow>{feedback === "updates" && (message || error) && <PanelSectionRow><div style={{ padding: "4px 0" }}>{error || message}</div></PanelSectionRow>}<PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => {
      busyRef.current = true; setBusy(true); setFeedback("updates"); setMessage("Checking for updates…"); setError("");
      void checkForUpdate().then((r) => { if (!r.ok || r.error) throw new Error(r.error || "Update check failed"); setUpdateAvailable(Boolean(r.available)); setMessage(r.available ? `Version ${r.version} available` : "No update available"); }).catch((e) => { setMessage(""); setError(String(e)); }).finally(() => { busyRef.current = false; setBusy(false); });
    }}>Check for updates</ButtonItem></PanelSectionRow>
    {updateAvailable && <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => {
      busyRef.current = true; setBusy(true); setFeedback("updates"); setMessage("Installing update…"); setError("");
      void installUpdate().then((r) => { if (!r.ok) throw new Error(r.error || "Update failed"); setUpdateAvailable(false); setMessage(r.message || "Update installed. Reload Decky to activate it."); }).catch((e) => { setMessage(""); setError(String(e)); }).finally(() => { busyRef.current = false; setBusy(false); });
    }}>Install update</ButtonItem></PanelSectionRow>}</PanelSection>
  </>;
}
export default definePlugin(() => ({ name: "ROG Control", titleView: <div className={staticClasses.Title}>ROG Control</div>, content: <Content />, icon: <span>🎮</span> }));
