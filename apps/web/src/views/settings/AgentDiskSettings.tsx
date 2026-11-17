import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { getAgentAndDiskSettings, saveAgentAndDiskSettings } from "../../api";
import type { TranslationDictionary } from "../../i18n";
import { useToast } from "../../ui/toast";
import { parseMinSize } from "./settingsModel";

const input = "w-24 rounded-lg border border-solid border-border-strong bg-bg px-3 py-[7px] text-right text-sm text-fg outline-none focus:border-accent";
const primary = "flex w-fit items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Phase 5g, Settings: "Update the agents automatically" (on by default) and
 * the minimum disk size (smaller disks are rejected, approval still possible).
 */
export function AgentDiskSettings({ t }: { t: TranslationDictionary }) {
  const text = t.settingsV2;
  const { toast } = useToast();
  const [autoUpdate, setAutoUpdate] = useState(true);
  const [minSize, setMinSize] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getAgentAndDiskSettings().then(
      (values) => {
        setAutoUpdate(values.autoUpdate);
        setMinSize(String(values.minSizeGib));
      },
      () => setMinSize("32"),
    );
  }, []);
  const size = parseMinSize(minSize);

  async function save() {
    if (size === null) return;
    setBusy(true);
    try {
      await saveAgentAndDiskSettings(autoUpdate, size);
      toast({ tone: "success", title: text.saved });
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="agent-disk-settings" className="flex flex-col gap-3.5 rounded-2xl border border-solid border-border bg-surface p-[22px]" data-settings="agents-disks">
      <h2 id="agent-disk-settings" className="m-0 text-[15px] font-semibold">{text.title}</h2>
      <label className="flex items-start gap-2.5 text-[13px]">
        <input type="checkbox" className="m-0 mt-0.5" checked={autoUpdate} onChange={(event) => setAutoUpdate(event.target.checked)} />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium text-fg">{text.autoUpdate}</span>
          <span className="text-muted">{text.autoUpdateHint}</span>
        </span>
      </label>
      <label className="flex flex-wrap items-center gap-2.5 text-[13px]">
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-medium text-fg">{text.minSize}</span>
          <span className="text-muted">{text.minSizeHint}</span>
        </span>
        <input className={input} inputMode="numeric" value={minSize} onChange={(event) => setMinSize(event.target.value)} />
      </label>
      {size === null && minSize !== "" ? <span className="text-xs text-danger-text">{text.minSizeInvalid}</span> : null}
      <button type="button" className={primary} disabled={size === null || busy} onClick={() => void save()}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {text.save}
      </button>
    </section>
  );
}
