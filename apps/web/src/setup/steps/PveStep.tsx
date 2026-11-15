import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Zap } from "lucide-react";

import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { wizardApi, WizardError, type ManualCommands, type PveSummary } from "../api";
import { CommandBlock, ErrorAlert, Field, ManualSection, SummaryList, useText } from "../shared";

interface PveStepProps {
  saved: PveSummary | undefined;
  onSaved: (summary: PveSummary) => void;
  onBack: () => void;
  onNext: () => void;
}

/** Step 3 — "Connect automatically": the host agent creates pbo@pve!pbo. */
export function PveStep({ saved, onSaved, onBack, onNext }: PveStepProps) {
  const text = useText();
  const [summary, setSummary] = useState<PveSummary | undefined>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function provision() {
    setBusy(true);
    setError(null);
    try {
      const result = await wizardApi.provisionPve();
      setSummary(result);
      onSaved(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <CardHeader>
        <CardTitle>{text.pve.title}</CardTitle>
        <CardDescription>{text.pve.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorAlert error={error} />
        {summary ? (
          <div className="flex flex-col gap-3 animate-fade-in">
            <div className="flex items-center gap-2 text-sm font-medium text-success">
              <CheckCircle2 className="h-5 w-5" />
              {text.pve.connected}
            </div>
            <SummaryList
              rows={[
                [text.pve.node, summary.node],
                ...(summary.pve_version ? ([[text.pve.version, summary.pve_version]] as [string, string][]) : []),
                ...(summary.vm_count !== undefined
                  ? ([[text.pve.guests, text.pve.guestCount(summary.vm_count, summary.ct_count ?? 0)]] as [string, string][])
                  : []),
                [text.pve.token, <code key="t" className="font-mono text-[13px]">{summary.token_id}</code>],
              ]}
            />
            <button
              type="button"
              className="self-start text-[13px] text-muted underline-offset-4 hover:text-fg hover:underline"
              onClick={() => void provision()}
              disabled={busy}
            >
              {text.pve.redo}
            </button>
          </div>
        ) : (
          <Button size="lg" className="self-start" onClick={() => void provision()} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Zap />}
            {busy ? text.pve.working : text.pve.auto}
          </Button>
        )}
        <ManualSection title={text.manual.toggle}>
          <ManualPve
            onSaved={(result) => {
              setSummary(result);
              onSaved(result);
            }}
          />
        </ManualSection>
      </CardContent>
      <CardFooter>
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft />
          {text.back}
        </Button>
        <Button onClick={onNext} disabled={!summary}>
          {text.continue}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}

function ManualPve({ onSaved }: { onSaved: (summary: PveSummary) => void }) {
  const text = useText();
  const [commands, setCommands] = useState<ManualCommands | null>(null);
  const [form, setForm] = useState({ api_url: "", token_id: "pbo@pve!pbo", token_secret: "", fingerprint: "", node_name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [observed, setObserved] = useState<string | null>(null);

  useEffect(() => {
    wizardApi.manualCommands().then(setCommands).catch(setError);
  }, []);

  const update = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setObserved(null);
    try {
      const probe = await wizardApi.testPve(form);
      if (probe.trust_required) {
        setObserved(probe.fingerprint);
        return;
      }
      const node = form.node_name.trim() || probe.nodes[0] || "";
      await wizardApi.savePve({ ...form, node_name: node });
      onSaved({ node, pve_version: probe.version, api_url: probe.api_url, fingerprint: probe.fingerprint, token_id: form.token_id, manual: true });
    } catch (err) {
      setError(err instanceof WizardError ? err : new WizardError("generic", String(err), 0));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={submit}>
      <p className="text-sm text-muted">{text.manual.intro}</p>
      {commands ? <CommandBlock lines={commands.pve} /> : null}
      <Field label={text.manual.url} placeholder="https://192.168.1.10:8006" value={form.api_url} onChange={update("api_url")} required />
      <Field label={text.manual.tokenId} value={form.token_id} onChange={update("token_id")} required />
      <Field label={text.manual.secret} type="password" autoComplete="off" value={form.token_secret} onChange={update("token_secret")} required />
      <Field label={text.manual.fingerprint} placeholder="AB:CD:…" value={form.fingerprint} onChange={update("fingerprint")} required />
      <Field label={text.manual.node} placeholder="pve" value={form.node_name} onChange={update("node_name")} />
      {observed ? <Alert variant="warning" title={text.manual.trustRequired(observed)} /> : null}
      <ErrorAlert error={error} />
      <Button type="submit" variant="secondary" className="self-start" disabled={busy}>
        {busy ? <Loader2 className="animate-spin" /> : null}
        {busy ? text.manual.saving : text.manual.save}
      </Button>
    </form>
  );
}
