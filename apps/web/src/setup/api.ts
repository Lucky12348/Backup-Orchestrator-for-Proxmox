// API calls of the first-run wizard. Errors are WizardError {code, message}:
// the UI maps codes to explanations, never shows raw JSON.
import { parseApiError, type WizardStep } from "./logic";

export class WizardError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...((init?.headers as Record<string, string>) ?? {}) },
    });
  } catch {
    throw new WizardError("network", "", 0);
  }
  if (!response.ok) {
    const detail = parseApiError(response.status, await response.text());
    throw new WizardError(detail.code, detail.message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export interface SetupStatus {
  needs_setup: boolean;
  proxmox_configured: boolean;
  pbs_configured: boolean;
  setup_completed: boolean;
  current_step: WizardStep;
}

export interface PveSummary {
  node: string;
  pve_version?: string;
  api_url: string;
  fingerprint: string;
  token_id: string;
  vm_count?: number;
  ct_count?: number;
  manual?: boolean;
}

export interface PbsSummary {
  api_url?: string;
  fingerprint?: string;
  version?: string;
  token_id?: string;
  vmid?: number | null;
  datastores?: string[];
  datastore?: string;
  manual?: boolean;
}

export interface SetupProgress {
  current_step: WizardStep;
  completed: boolean;
  data: { pve?: PveSummary; pbs?: PbsSummary; [key: string]: unknown };
}

export interface SetupAgentStatus {
  registered: boolean;
  connected: boolean;
  hostname?: string | null;
  base_url?: string | null;
  last_seen_at?: string | null;
}

export interface AgentPairing {
  code: string;
  expires_at: string;
  install_commands: string[];
  app_url: string;
  app_fingerprint: string | null;
}

export interface PbsCandidate {
  vmid: number;
  name: string;
  node: string;
  status: string;
  addresses: string[];
  pbs_port_open: boolean;
  name_match: boolean;
  score: number;
  suggested_url: string | null;
}

export interface PbsCandidates {
  node: string;
  suggested_vmid: number | null;
  vms: PbsCandidate[];
}

export interface PbsDatastore {
  name: string;
  comment?: string;
  mount_status?: string | null;
}

export interface PbsProvisionResult {
  api_url: string;
  fingerprint: string;
  version: string;
  token_id: string;
  vmid: number | null;
  datastores: PbsDatastore[];
}

export interface ManualCommands {
  pve: string[];
  pbs: string[];
  pve_token_id: string[];
  pbs_token_id: string[];
}

export interface ProbeResult {
  api_url: string;
  version: string;
  fingerprint: string;
  trust_required: boolean;
  nodes: string[];
  datastores?: PbsDatastore[];
}

export const wizardApi = {
  status: () => call<SetupStatus>("/setup/status"),
  progress: () => call<SetupProgress>("/setup/progress"),
  setStep: (step: Exclude<WizardStep, "admin" | "done">) => call<SetupProgress>("/setup/progress", json("PUT", { step })),
  complete: () => call<SetupProgress>("/setup/complete", json("POST")),
  agent: () => call<SetupAgentStatus>("/setup/agent"),
  pairing: () => call<AgentPairing>("/agents/pairing", json("POST")),
  manualCommands: () => call<ManualCommands>("/setup/manual-commands"),
  provisionPve: () => call<PveSummary>("/setup/pve/provision", json("POST")),
  testPve: (body: { api_url: string; token_id: string; token_secret: string; fingerprint: string }) =>
    call<ProbeResult>("/setup/proxmox/test", json("POST", body)),
  savePve: (body: { api_url: string; token_id: string; token_secret: string; fingerprint: string; node_name: string }) =>
    call<unknown>("/setup/proxmox", json("PUT", body)),
  pbsCandidates: () => call<PbsCandidates>("/setup/pbs/candidates"),
  pbsFingerprint: (api_url: string) => call<{ api_url: string; fingerprint: string }>("/setup/pbs/fingerprint", json("POST", { api_url })),
  provisionPbs: (body: { api_url: string; fingerprint: string; fingerprint_confirmed: boolean; password: string; pbs_vm_id: number | null }) =>
    call<PbsProvisionResult>("/setup/pbs/provision", json("POST", body)),
  selectDatastore: (datastore: string) => call<{ datastore: string }>("/setup/pbs/datastore", json("PUT", { datastore })),
  testPbs: (body: { api_url: string; token_id: string; token_secret: string; fingerprint: string }) =>
    call<ProbeResult>("/setup/pbs/test", json("POST", body)),
  savePbs: (body: {
    api_url: string;
    token_id: string;
    token_secret: string;
    fingerprint: string;
    datastore: string;
    mode: "api";
    pbs_vm_id: number | null;
  }) => call<unknown>("/setup/pbs", json("PUT", body)),
  syncProxmox: () => call<{ synced_vms_count: number; synced_cts_count: number; total_seen: number }>("/integrations/proxmox/sync", json("POST")),
  syncPbs: () => call<{ matched_vms: number; matched_cts: number; total_snapshots_seen: number }>("/integrations/pbs/sync", json("POST")),
};
