import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Radar, RefreshCw, ShieldCheck } from "lucide-react";

import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import { RadioCard, RadioGroup } from "../../components/ui/radio-group";
import {
  wizardApi,
  WizardError,
  type ManualCommands,
  type PbsCandidates,
  type PbsDatastore,
  type PbsSummary,
} from "../api";
import { suggestedPbsUrl } from "../logic";
import { CommandBlock, ErrorAlert, Field, Fingerprint, ManualSection, useText } from "../shared";

interface PbsStepProps {
  saved: PbsSummary | undefined;
  onSaved: (summary: PbsSummary) => void;
  onBack: () => void;
  onNext: () => void;
}

/**
 * Step 4 — pick the PBS VM, verify the certificate fingerprint (compared by
 * the operator in PBS), type the root@pam password once: the app creates
 * pbo@pbs!pbo and its ACLs, then the source datastore is chosen.
 */
export function PbsStep({ saved, onSaved, onBack, onNext }: PbsStepProps) {
  const text = useText();
  const [candidates, setCandidates] = useState<PbsCandidates | null>(null);
  const [candidatesError, setCandidatesError] = useState<unknown>(null);
  const [vmid, setVmid] = useState<number | null>(saved?.vmid ?? null);
  const [manualVmid, setManualVmid] = useState("");
  const [url, setUrl] = useState(saved?.api_url ? saved.api_url.replace(/\/api2\/json$/, "") : "");
  const [urlEdited, setUrlEdited] = useState(Boolean(saved?.api_url));
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [password, setPassword] = useState("");
  const [datastores, setDatastores] = useState<PbsDatastore[]>((saved?.datastores ?? []).map((name) => ({ name })));
  const [connected, setConnected] = useState<PbsSummary | null>(saved?.token_id ? saved : null);
  const [datastore, setDatastore] = useState(saved?.datastore ?? "");
  const [chosen, setChosen] = useState(saved?.datastore ?? "");
  const [busy, setBusy] = useState<"candidates" | "verify" | "connect" | "datastore" | "refresh" | null>("candidates");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    wizardApi
      .pbsCandidates()
      .then((result) => {
        setCandidates(result);
        setVmid((current) => {
          const selected = current ?? result.suggested_vmid;
          if (!urlEdited) setUrl(suggestedPbsUrl(result.vms, selected));
          return selected;
        });
      })
      .catch(setCandidatesError)
      .finally(() => setBusy(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const effectiveVmid = candidates?.vms.length ? vmid : Number.parseInt(manualVmid, 10) || null;

  function selectVm(value: string) {
    const id = Number(value);
    setVmid(id);
    if (!urlEdited && candidates) setUrl(suggestedPbsUrl(candidates.vms, id));
    resetTrust();
  }

  function resetTrust() {
    setFingerprint(null);
    setConfirmed(false);
  }

  async function verify() {
    setBusy("verify");
    setError(null);
    resetTrust();
    try {
      const result = await wizardApi.pbsFingerprint(url.trim());
      setFingerprint(result.fingerprint);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function connect(event: FormEvent) {
    event.preventDefault();
    if (!fingerprint || !confirmed) return;
    if (!password) {
      setError(new WizardError("password_required", "", 0));
      return;
    }
    setBusy("connect");
    setError(null);
    try {
      const result = await wizardApi.provisionPbs({
        api_url: url.trim(),
        fingerprint,
        fingerprint_confirmed: confirmed,
        password,
        pbs_vm_id: effectiveVmid,
      });
      const summary: PbsSummary = {
        api_url: result.api_url,
        fingerprint: result.fingerprint,
        version: result.version,
        token_id: result.token_id,
        vmid: result.vmid,
        datastores: result.datastores.map((store) => store.name),
      };
      setConnected(summary);
      setDatastores(result.datastores);
      setChosen(result.datastores.length === 1 ? result.datastores[0].name : "");
      onSaved(summary);
    } catch (err) {
      setError(err);
    } finally {
      setPassword(""); // emptied after every attempt: never kept longer than the request
      setBusy(null);
    }
  }

  // PBS caches its ACLs for a few seconds: a datastore can appear a bit later.
  async function refreshDatastores() {
    setBusy("refresh");
    setError(null);
    try {
      const result = await wizardApi.pbsDatastores();
      setDatastores(result.datastores);
      if (result.datastores.length === 1) setChosen(result.datastores[0].name);
      if (connected) onSaved({ ...connected, datastores: result.datastores.map((store) => store.name) });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function useDatastore() {
    if (!chosen) return;
    setBusy("datastore");
    setError(null);
    try {
      await wizardApi.selectDatastore(chosen);
      setDatastore(chosen);
      onSaved({ ...(connected ?? {}), datastore: chosen });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  function startOver() {
    setConnected(null);
    setDatastore("");
    setChosen("");
    resetTrust();
  }

  const ready = Boolean(connected && datastore);

  return (
    <>
      <CardHeader>
        <CardTitle>{text.pbs.title}</CardTitle>
        <CardDescription>{text.pbs.description}</CardDescription>
      </CardHeader>
      <CardContent>
        {connected ? (
          <div className="flex flex-col gap-4 animate-fade-in">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium text-success">
                <CheckCircle2 className="h-5 w-5" />
                {text.pbs.connected(connected.version ?? "")}
              </div>
              <button
                type="button"
                className="text-[13px] text-muted underline-offset-4 hover:text-fg hover:underline"
                onClick={startOver}
              >
                {text.pbs.redo}
              </button>
            </div>
            <div className="flex flex-col gap-2">
              <Label>{text.pbs.datastore}</Label>
              <p className="text-[13px] text-faint">{text.pbs.datastoreHint}</p>
              {datastores.length === 0 ? (
                <div className="flex flex-col gap-3">
                  <Alert variant="warning" title={text.pbs.noDatastore}>
                    <p>{text.pbs.noDatastoreHint}</p>
                  </Alert>
                  <Button
                    variant="secondary"
                    className="self-start"
                    onClick={() => void refreshDatastores()}
                    disabled={busy !== null}
                  >
                    <RefreshCw className={busy === "refresh" ? "animate-spin" : undefined} />
                    {busy === "refresh" ? text.pbs.refreshing : text.pbs.refreshDatastores}
                  </Button>
                </div>
              ) : (
                <RadioGroup value={chosen} onValueChange={setChosen}>
                  {datastores.map((store) => (
                    <RadioCard key={store.name} value={store.name}>
                      <span className="flex min-w-0 flex-1 items-center justify-between gap-3">
                        <span className="font-mono text-sm">{store.name}</span>
                        {datastore === store.name ? <CheckCircle2 className="h-4 w-4 text-success" /> : null}
                      </span>
                    </RadioCard>
                  ))}
                </RadioGroup>
              )}
              {datastores.length > 0 && chosen !== datastore ? (
                <Button variant="secondary" className="self-start" onClick={() => void useDatastore()} disabled={!chosen || busy !== null}>
                  {busy === "datastore" ? <Loader2 className="animate-spin" /> : null}
                  {text.pbs.useDatastore}
                </Button>
              ) : null}
            </div>
            <ErrorAlert error={error} />
          </div>
        ) : (
          <form className="flex flex-col gap-5" onSubmit={connect}>
            <div className="flex flex-col gap-2">
              <Label>{text.pbs.vmLabel}</Label>
              {busy === "candidates" ? (
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {text.loading}
                </div>
              ) : candidates && candidates.vms.length > 0 ? (
                <RadioGroup value={vmid ? String(vmid) : ""} onValueChange={selectVm} className="max-h-72 overflow-y-auto pr-1">
                  {candidates.vms.map((vm) => (
                    <RadioCard key={vm.vmid} value={String(vm.vmid)}>
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium">{vm.name || `VM ${vm.vmid}`}</span>
                          {vm.pbs_port_open ? (
                            <Badge variant="success">
                              <Radar className="h-3 w-3" />
                              {text.pbs.detected}
                            </Badge>
                          ) : vm.name_match ? (
                            <Badge variant="accent">{text.pbs.nameMatch}</Badge>
                          ) : null}
                        </span>
                        <span className="text-[13px] text-faint">
                          {vm.vmid} · {vm.status}
                          {vm.addresses.length ? ` · ${vm.addresses.join(", ")}` : ""}
                        </span>
                      </span>
                    </RadioCard>
                  ))}
                </RadioGroup>
              ) : (
                <>
                  {candidatesError ? <ErrorAlert error={candidatesError} /> : <p className="text-sm text-muted">{text.pbs.noVms}</p>}
                  <Field
                    label={text.pbs.vmIdLabel}
                    inputMode="numeric"
                    placeholder="110"
                    value={manualVmid}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setManualVmid(event.target.value.replace(/\D/g, ""))}
                  />
                </>
              )}
            </div>

            <div className="flex items-end gap-2">
              <Field
                className="flex-1"
                label={text.pbs.url}
                placeholder="192.168.1.20"
                value={url}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  setUrl(event.target.value);
                  setUrlEdited(true);
                  resetTrust();
                }}
                hint={text.pbs.urlHint}
              />
              <Button
                type="button"
                variant="secondary"
                className="mb-[26px]"
                onClick={() => void verify()}
                disabled={!url.trim() || busy !== null}
              >
                {busy === "verify" ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
                {busy === "verify" ? text.pbs.verifying : text.pbs.verify}
              </Button>
            </div>

            {fingerprint ? (
              <div className="flex flex-col gap-3 rounded-lg border border-border bg-subtle p-4 animate-fade-in">
                <div className="flex flex-col gap-1.5">
                  <p className="text-sm font-medium">{text.pbs.fingerprint}</p>
                  <Fingerprint value={fingerprint} />
                  <p className="text-[13px] text-faint">{text.pbs.fingerprintHint}</p>
                </div>
                <label className="flex cursor-pointer items-start gap-2.5 text-sm">
                  <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(value === true)} />
                  {text.pbs.fingerprintMatches}
                </label>
              </div>
            ) : null}

            {fingerprint && confirmed ? (
              <div className="flex flex-col gap-3 animate-fade-in">
                <Field
                  label={text.pbs.password}
                  type="password"
                  autoComplete="off"
                  autoFocus
                  value={password}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setPassword(event.target.value)}
                  hint={text.pbs.passwordHint}
                />
                {/* Only disabled while a request runs: after a failure the field is emptied
                    and the operator retries with the same button. */}
                <Button type="submit" className="self-start" disabled={busy !== null}>
                  {busy === "connect" ? <Loader2 className="animate-spin" /> : null}
                  {busy === "connect" ? text.pbs.connecting : text.pbs.connect}
                </Button>
              </div>
            ) : null}
            <ErrorAlert error={error} />
          </form>
        )}

        {!connected ? (
          <ManualSection title={text.manual.toggle}>
            <ManualPbs
              vmid={effectiveVmid}
              defaultUrl={url}
              onSaved={(summary) => {
                setConnected(summary);
                setDatastore(summary.datastore ?? "");
                setChosen(summary.datastore ?? "");
                setDatastores((summary.datastores ?? []).map((name) => ({ name })));
                onSaved(summary);
              }}
            />
          </ManualSection>
        ) : null}
      </CardContent>
      <CardFooter>
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft />
          {text.back}
        </Button>
        <Button onClick={onNext} disabled={!ready}>
          {text.continue}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}

function ManualPbs({
  vmid,
  defaultUrl,
  onSaved,
}: {
  vmid: number | null;
  defaultUrl: string;
  onSaved: (summary: PbsSummary) => void;
}) {
  const text = useText();
  const [commands, setCommands] = useState<ManualCommands | null>(null);
  const [form, setForm] = useState({ api_url: defaultUrl, token_id: "pbo@pbs!pbo", token_secret: "", fingerprint: "", datastore: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [observed, setObserved] = useState<string | null>(null);

  useEffect(() => {
    wizardApi.manualCommands().then(setCommands).catch(setError);
  }, []);

  const update = (key: keyof typeof form) => (event: ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setObserved(null);
    try {
      const probe = await wizardApi.testPbs(form);
      if (probe.trust_required) {
        setObserved(probe.fingerprint);
        return;
      }
      const names = (probe.datastores ?? []).map((store) => store.name);
      if (!names.includes(form.datastore.trim())) {
        throw new WizardError("unknown_datastore", names.join(", "), 400);
      }
      await wizardApi.savePbs({ ...form, datastore: form.datastore.trim(), mode: "api", pbs_vm_id: vmid });
      onSaved({
        api_url: probe.api_url,
        fingerprint: probe.fingerprint,
        version: probe.version,
        token_id: form.token_id,
        vmid,
        datastores: names,
        datastore: form.datastore.trim(),
        manual: true,
      });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={submit}>
      <p className="text-sm text-muted">{text.manual.introPbs}</p>
      {commands ? <CommandBlock lines={commands.pbs} /> : null}
      <Field label={text.manual.url} placeholder="https://192.168.1.20:8007" value={form.api_url} onChange={update("api_url")} required />
      <Field label={text.manual.tokenId} value={form.token_id} onChange={update("token_id")} required />
      <Field label={text.manual.secret} type="password" autoComplete="off" value={form.token_secret} onChange={update("token_secret")} required />
      <Field label={text.manual.fingerprint} placeholder="AB:CD:…" value={form.fingerprint} onChange={update("fingerprint")} required />
      <Field label={text.manual.datastore} placeholder="main" value={form.datastore} onChange={update("datastore")} required />
      {observed ? <Alert variant="warning" title={text.manual.trustRequired(observed)} /> : null}
      <ErrorAlert error={error} />
      <Button type="submit" variant="secondary" className="self-start" disabled={busy}>
        {busy ? <Loader2 className="animate-spin" /> : null}
        {busy ? text.manual.saving : text.manual.save}
      </Button>
    </form>
  );
}
