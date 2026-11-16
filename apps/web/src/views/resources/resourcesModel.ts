// Resources page (Phase 6b): VM/CT table, protection state, filters, bulk
// job selection. Pure (node:test).

export type Protection = "protected" | "unprotected" | "ignored" | "infrastructure";
export const PROTECTIONS: readonly Protection[] = ["protected", "unprotected", "ignored", "infrastructure"];

export interface VmLike {
  id: number;
  name: string;
  vm_type: string;
  external_id: string | null;
  node_name: string | null;
  runtime_status: string | null;
  size_gb: number;
  last_backup_at: string | null;
  last_external_copy_at?: string | null;
  ignored: boolean;
  ignore_reason: string | null;
}

export interface JobLike {
  job_id: string;
  enabled: boolean;
  storage: string | null;
  selection_mode: string;
  selected_vmids: number[];
  supported: boolean;
}

/** Same rule as the coverage (services/overview.py): a PBS backup = protected. */
export function protectionOf(vm: VmLike): Protection {
  if (vm.ignored) return vm.ignore_reason === "infrastructure" ? "infrastructure" : "ignored";
  return vm.last_backup_at ? "protected" : "unprotected";
}

export function vmid(vm: VmLike): number | null {
  const value = Number(vm.external_id);
  return Number.isInteger(value) && value > 0 ? value : null;
}

/** Jobs that back this VM up (explicit selection, or a job for "all" guests). */
export function jobsFor(vm: VmLike, jobs: readonly JobLike[]): JobLike[] {
  const id = vmid(vm);
  return jobs.filter((job) => job.selection_mode === "all" || (id !== null && job.selected_vmids.includes(id)));
}

export interface ResourceFilters {
  query: string;
  type: "" | "vm" | "ct";
  protection: "" | Protection;
  node: string;
}

const compact = (value: string | null | undefined) => (value ?? "").toLowerCase();

export function filterResources<T extends VmLike>(vms: readonly T[], filters: ResourceFilters): T[] {
  const needle = compact(filters.query.trim());
  return vms
    .filter((vm) => !filters.type || (filters.type === "ct" ? vm.vm_type === "ct" : vm.vm_type !== "ct"))
    .filter((vm) => !filters.protection || protectionOf(vm) === filters.protection)
    .filter((vm) => !filters.node || vm.node_name === filters.node)
    .filter((vm) => !needle || compact(vm.name).includes(needle) || compact(vm.external_id).includes(needle))
    .sort((a, b) => (vmid(a) ?? 0) - (vmid(b) ?? 0) || a.name.localeCompare(b.name));
}

export function protectionCounts(vms: readonly VmLike[]): Record<Protection, number> {
  const counts = { protected: 0, unprotected: 0, ignored: 0, infrastructure: 0 };
  for (const vm of vms) counts[protectionOf(vm)] += 1;
  return counts;
}

export function nodesOf(vms: readonly VmLike[]): string[] {
  return [...new Set(vms.map((vm) => vm.node_name).filter((node): node is string => Boolean(node)))].sort();
}

/** A job PBO can change (explicit VM selection, supported by the API). */
export function editableJobs(jobs: readonly JobLike[]): JobLike[] {
  return jobs.filter((job) => job.supported && job.selection_mode !== "all");
}

/** New selection of a job after a bulk "add" / "remove" of VM ids. */
export function nextSelection(job: JobLike, ids: readonly number[], action: "add" | "remove"): number[] {
  const current = new Set(job.selected_vmids);
  for (const id of ids) {
    if (action === "add") current.add(id);
    else current.delete(id);
  }
  return [...current].sort((a, b) => a - b);
}
