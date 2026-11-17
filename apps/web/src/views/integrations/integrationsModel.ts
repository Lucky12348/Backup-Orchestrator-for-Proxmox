// Integrations page (Phase 6b): card state, agents split. Pure (node:test).

export type IntegrationState = "ok" | "down" | "unconfigured" | "checking";

export interface HealthLike {
  ok: boolean;
  code: string | null;
}

export function integrationState(health: HealthLike | undefined, checking: boolean): IntegrationState {
  if (checking || !health) return "checking";
  if (health.ok) return "ok";
  return health.code === "not_configured" ? "unconfigured" : "down";
}

export interface AgentLike {
  id: number;
  revoked_at: string | null;
}

/** Only the active agent(s) are shown; the revoked ones go into the history. */
export function splitAgents<T extends AgentLike>(agents: readonly T[]): { active: T[]; revoked: T[] } {
  return {
    active: agents.filter((agent) => !agent.revoked_at),
    revoked: agents.filter((agent) => agent.revoked_at).sort((a, b) => (b.revoked_at ?? "").localeCompare(a.revoked_at ?? "")),
  };
}

// --- Agent self-update (Phase 5g) ---------------------------------------------------

export type UpdateState = "up_to_date" | "outdated" | "updating" | "failed" | "too_old" | "unknown";

export const UPDATE_TONE: Record<UpdateState, "ok" | "warn" | "err" | "run" | "off"> = {
  up_to_date: "ok",
  outdated: "warn",
  updating: "run",
  failed: "err",
  too_old: "warn",
  unknown: "off",
};

/** "Update now": an agent that can update itself and is not up to date (a failed one may retry). */
export function canUpdateNow(state: UpdateState): boolean {
  return state === "outdated" || state === "failed" || state === "unknown";
}

export function shortBuild(build: string | null | undefined): string {
  return (build ?? "").slice(0, 8);
}
