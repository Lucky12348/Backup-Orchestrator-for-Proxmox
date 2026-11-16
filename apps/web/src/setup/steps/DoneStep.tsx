import { useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCircle2, Circle, Loader2, PartyPopper, XCircle } from "lucide-react";

import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { Progress } from "../../components/ui/progress";
import { wizardApi, type SetupAgentStatus, type SetupProgress } from "../api";
import { diskPageUrl } from "../../liveDisks";
import type { WizardLanguage } from "../logic";
import { SummaryList, useText } from "../shared";
import { LiveDisks } from "./LiveDisks";

type TaskState = "pending" | "running" | "ok" | "failed";

/** Step 5 — summary, first inventory sync with progress, then the dashboard. */
export function DoneStep({
  progress,
  language,
  onFinish,
}: {
  progress: SetupProgress;
  language: WizardLanguage;
  onFinish: (path?: string) => void;
}) {
  const text = useText();
  const [agent, setAgent] = useState<SetupAgentStatus | null>(null);
  const [tasks, setTasks] = useState<{ pve: TaskState; pbs: TaskState }>({ pve: "pending", pbs: "pending" });
  const [counts, setCounts] = useState<{ guests: number; snapshots: number } | null>(null);
  const started = useRef(false);

  useEffect(() => {
    wizardApi.agent().then(setAgent).catch(() => setAgent(null));
    if (started.current) return; // StrictMode double effect
    started.current = true;
    void (async () => {
      let guests = 0;
      let snapshots = 0;
      setTasks({ pve: "running", pbs: "pending" });
      try {
        const pve = await wizardApi.syncProxmox();
        guests = pve.synced_vms_count + pve.synced_cts_count;
        setTasks({ pve: "ok", pbs: "running" });
      } catch {
        setTasks({ pve: "failed", pbs: "running" });
      }
      try {
        const pbs = await wizardApi.syncPbs();
        snapshots = pbs.total_snapshots_seen;
        setTasks((current) => ({ ...current, pbs: "ok" }));
      } catch {
        setTasks((current) => ({ ...current, pbs: "failed" }));
      }
      setCounts({ guests, snapshots });
    })();
  }, []);

  const finished = [tasks.pve, tasks.pbs].filter((state) => state === "ok" || state === "failed").length;
  const failed = tasks.pve === "failed" || tasks.pbs === "failed";
  const pve = progress.data.pve;
  const pbs = progress.data.pbs;

  return (
    <>
      <CardHeader>
        <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-full bg-success/15 text-success">
          <PartyPopper className="h-5 w-5" />
        </div>
        <CardTitle>{text.done.title}</CardTitle>
        <CardDescription>{text.done.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <SummaryList
          rows={[
            [text.done.agent, agent?.hostname ?? "—"],
            [text.done.pve, pve ? `${pve.node}${pve.pve_version ? ` · ${pve.pve_version}` : ""}` : "—"],
            [text.done.pbs, pbs?.api_url ? `${pbs.api_url.replace(/^https:\/\//, "").replace(/\/api2\/json$/, "")}${pbs.version ? ` · ${pbs.version}` : ""}` : "—"],
            [text.done.datastore, pbs?.datastore ? <code key="d" className="font-mono text-[13px]">{pbs.datastore}</code> : "—"],
          ]}
        />
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium">{text.done.syncTitle}</p>
          <Progress value={(finished / 2) * 100} />
          <ul className="flex flex-col gap-2 text-sm">
            <TaskLine label={text.done.syncPve} state={tasks.pve} />
            <TaskLine label={text.done.syncPbs} state={tasks.pbs} />
          </ul>
          {counts && !failed ? <p className="text-[13px] text-muted">{text.done.syncDone(counts.guests, counts.snapshots)}</p> : null}
          {failed ? <Alert variant="warning" title={text.done.syncFailed} /> : null}
        </div>
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium">{text.done.plugDisk}</p>
          <LiveDisks language={language} onExamine={(diskId) => onFinish(diskPageUrl(diskId))} />
        </div>
      </CardContent>
      <CardFooter className="justify-end">
        {/* Always available: a disk can be plugged in later. */}
        <Button variant={counts ? "default" : "secondary"} onClick={() => onFinish("/")}>
          {text.done.dashboard}
          <ArrowRight />
        </Button>
      </CardFooter>
    </>
  );
}

function TaskLine({ label, state }: { label: string; state: TaskState }) {
  const icon =
    state === "ok" ? (
      <CheckCircle2 className="h-4 w-4 text-success" />
    ) : state === "failed" ? (
      <XCircle className="h-4 w-4 text-danger" />
    ) : state === "running" ? (
      <Loader2 className="h-4 w-4 animate-spin text-accent" />
    ) : (
      <Circle className="h-4 w-4 text-faint" />
    );
  return (
    <li className="flex items-center gap-2.5">
      {icon}
      <span className={state === "pending" ? "text-faint" : "text-fg"}>{label}</span>
    </li>
  );
}
