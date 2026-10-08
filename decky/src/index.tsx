import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  staticClasses,
  ToggleField,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useCallback, useEffect, useState } from "react";

type CpuCapabilities = {
  boost: boolean;
  max_freq: boolean;
  clock_range_mhz: [number, number] | null;
};

type PluginState = {
  profiles: string[];
  current_profile: string | null;
  cpu: { boost: boolean | null; max_freq_mhz: number };
  capabilities: CpuCapabilities;
};

type RpcResult = { ok: boolean; error?: string; state?: PluginState };

const getState = callable<[], PluginState | RpcResult>("get_state");
const setProfile = callable<[name: string], RpcResult>("set_profile");
const setCpuBoost = callable<[enabled: boolean], RpcResult>("set_cpu_boost");
const setCpuMaxFreq = callable<[mhz: number], RpcResult>("set_cpu_max_freq");
const checkForUpdate = callable<[], RpcResult>("check_for_update");
const installUpdate = callable<[], RpcResult>("install_update");

function isError(value: PluginState | RpcResult): value is RpcResult {
  return "ok" in value && value.ok === false;
}

function Content() {
  const [state, setState] = useState<PluginState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clockValue, setClockValue] = useState(0);
  const [releaseStatus, setReleaseStatus] = useState("");
  const [updateAvailable, setUpdateAvailable] = useState(false);

  const refresh = useCallback(async () => {
    const next = await getState();
    if (isError(next)) {
      setError(next.error || "Could not read ROG Control settings");
      return;
    }
    setState(next);
    setClockValue(next.cpu.max_freq_mhz);
    setError("");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const perform = async (action: () => Promise<RpcResult>) => {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (!result.ok) {
        setError(result.error || "ROG Control could not apply that setting");
        return;
      }
      await refresh();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return (
      <PanelSection title="ROG Control">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()}>
            {error || "Connect to ROG Control"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const range = state.capabilities.clock_range_mhz;
  return (
    <PanelSection title="ROG Control">
      {state.profiles.map((profile) => (
        <PanelSectionRow key={profile}>
          <ButtonItem
            layout="below"
            disabled={busy || profile === state.current_profile}
            onClick={() => void perform(() => setProfile(profile))}
          >
            {profile === state.current_profile ? `✓ ${profile}` : profile}
          </ButtonItem>
        </PanelSectionRow>
      ))}

      {state.capabilities.boost && (
        <PanelSectionRow>
          <ToggleField
            label="CPU Turbo Boost"
            description="Saved to the active ROG Control profile"
            checked={Boolean(state.cpu.boost)}
            disabled={busy}
            onChange={(enabled) =>
              void perform(() => setCpuBoost(enabled))
            }
          />
        </PanelSectionRow>
      )}

      {state.capabilities.max_freq && range && (
        <>
          <PanelSectionRow>
            <SliderField
              label={`CPU clock ceiling${clockValue === 0 ? ": no limit" : `: ${clockValue} MHz`}`}
              min={range[0]}
              max={range[1]}
              step={100}
              value={Math.max(range[0], Math.min(range[1], clockValue || range[1]))}
              showValue
              onChange={(value) => {
                if (!busy) setClockValue(value);
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || clockValue === 0}
              onClick={() => void perform(() => setCpuMaxFreq(clockValue))}
            >
              Apply CPU clock ceiling
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || clockValue === 0}
              onClick={() => void perform(() => setCpuMaxFreq(0))}
            >
              Clear CPU clock ceiling
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}

      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setReleaseStatus("Checking GitHub Releases…");
            void checkForUpdate()
              .then((result) => {
                if (!result.ok) {
                  setReleaseStatus(result.error || "Update check failed");
                  return;
                }
                const update = (result as RpcResult & {
                  available?: boolean;
                  version?: string;
                }).available;
                setUpdateAvailable(Boolean(update));
                const version = (result as RpcResult & { version?: string }).version;
                setReleaseStatus(update ? `Version ${version} available` : "No update available");
              })
              .catch((cause) => setReleaseStatus(String(cause)))
              .finally(() => setBusy(false));
          }}
        >
          Check for plugin updates
        </ButtonItem>
      </PanelSectionRow>
      {updateAvailable && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
          onClick={() => {
            setBusy(true);
            setReleaseStatus("Downloading and installing plugin update…");
              void installUpdate()
                .then((result) => {
                  setReleaseStatus(result.ok ? (result as RpcResult & { message?: string }).message || "Update installed" : result.error || "Update failed");
                  if (result.ok) setUpdateAvailable(false);
                })
                .catch((cause) => setReleaseStatus(String(cause)))
                .finally(() => setBusy(false));
            }}
          >
            Update plugin
          </ButtonItem>
        </PanelSectionRow>
      )}
      {releaseStatus && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled>{releaseStatus}</ButtonItem>
        </PanelSectionRow>
      )}

      {error && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()}>
            {error}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "ROG Control",
  titleView: <div className={staticClasses.Title}>ROG Control</div>,
  content: <Content />,
  icon: <span>🎮</span>,
}));
