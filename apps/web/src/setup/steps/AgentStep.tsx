import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCircle2, Loader2, RefreshCw } from "lucide-react";

import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { wizardApi, type AgentPairing, type SetupAgentStatus } from "../api";
import { formatCountdown, secondsUntil } from "../logic";
import { CommandBlock, ErrorAlert, Fingerprint, useText } from "../shared";

const POLL_MS = 3000;

/**
 * Step 2 — the pairing code is generated automatically, the two commands are
 * shown in a selectable block (no copy button: uBlock "ClickFix"), and the
 * screen waits live for the agent's first heartbeat.
 */
export function AgentStep({ onNext }: { onNext: () => void }) {
  const text = useText();
  const [status, setStatus] = useState<SetupAgentStatus | null>(null);
  const [pairing, setPairing] = useState<AgentPairing | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [now, setNow] = useState(() => Date.now());
  const [generating, setGenerating] = useState(false);
  // Agent already connected when the step opened (reload, back): do not
  // generate a code unless the operator asks to reinstall.
  const [wantsInstall, setWantsInstall] = useState(false);
  const firstStatus = useRef(true);

  const generate = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      setPairing(await wizardApi.pairing());
      setWantsInstall(true);
    } catch (err) {
      setError(err);
    } finally {
      setGenerating(false);
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const current = await wizardApi.agent();
        if (stopped) return;
        setStatus(current);
        if (firstStatus.current) {
          firstStatus.current = false;
          if (!current.connected) void generate();
        }
      } catch (err) {
        if (!stopped) setError(err);
      }
      if (!stopped) timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [generate]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const remaining = pairing ? secondsUntil(pairing.expires_at, now) : 0;
  const expired = Boolean(pairing) && remaining === 0;
  const connected = Boolean(status?.connected);
  // A registration made with the current code shows up before its first heartbeat.
  const showInstall = Boolean(pairing) && (!connected || wantsInstall);

  return (
    <>
      <CardHeader>
        <CardTitle>{text.agent.title}</CardTitle>
        <CardDescription>{text.agent.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorAlert error={error} />
        {showInstall && pairing ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted">{text.agent.howTo}</p>
            {expired ? (
              <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border px-4 py-4">
                <p className="text-sm text-muted">{text.agent.expired}</p>
                <Button variant="secondary" size="sm" onClick={() => void generate()} disabled={generating}>
                  <RefreshCw className={generating ? "animate-spin" : undefined} />
                  {text.agent.regenerate}
                </Button>
              </div>
            ) : (
              <>
                <CommandBlock lines={pairing.install_commands} />
                <div className="flex items-center justify-between text-[13px] text-faint">
                  <span>{text.agent.selectHint}</span>
                  <span className="tabular-nums">{text.agent.expiresIn(formatCountdown(remaining))}</span>
                </div>
              </>
            )}
            {pairing.app_fingerprint ? (
              <div className="flex flex-col gap-1.5">
                <p className="text-sm font-medium">{text.agent.fingerprint}</p>
                <Fingerprint value={pairing.app_fingerprint} />
                <p className="text-[13px] text-faint">{text.agent.fingerprintHint}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        <div
          className={
            connected
              ? "flex items-center gap-3 rounded-lg border border-success/30 bg-success/10 px-4 py-3"
              : "flex items-center gap-3 rounded-lg border border-border bg-subtle px-4 py-3"
          }
          aria-live="polite"
        >
          {connected ? (
            <CheckCircle2 className="h-5 w-5 text-success" />
          ) : (
            <Loader2 className="h-5 w-5 animate-spin text-muted" />
          )}
          <span className="text-sm">
            {connected
              ? text.agent.connected(status?.hostname ?? "")
              : status?.registered && !expired
                ? text.agent.registered
                : text.agent.waiting}
          </span>
        </div>
        {connected && !pairing ? (
          <button
            type="button"
            className="self-start text-[13px] text-muted underline-offset-4 hover:text-fg hover:underline"
            onClick={() => void generate()}
          >
            {text.agent.reinstall}
          </button>
        ) : null}
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={onNext} disabled={!connected}>
          {text.continue}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}
