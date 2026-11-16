import type { AppDataState } from "../hooks/useAppData";
import type { Language, TranslationDictionary } from "../i18n";
import type { DiskPreparationMode, ExternalBackupRun, ExternalDisk, VirtualMachine } from "../types";

export interface PageCommonProps {
  data: AppDataState;
  language: Language;
  t: TranslationDictionary;
}

export interface AssetPageProps extends PageCommonProps {
  pbsInventoryByVmId: Map<number, { protected: boolean; last_backup_at: string | null }>;
  isSaving: (key: string) => boolean;
  onAssetIgnoreChange: (vm: VirtualMachine, ignored: boolean) => void;
  onBackupJobSelectionChange: (jobId: string, selectedVmids: number[]) => void;
  onRefresh: () => Promise<void>;
  /** Phase 6b: only the "Proxmox backup jobs" section (the table is ResourcesView). */
  jobsOnly?: boolean;
}

export interface DiskActionRequest {
  disk: ExternalDisk;
  field: "trusted" | "dedicated_backup_disk";
  value: boolean;
}

export interface DiskPreparationSubmitPayload {
  mode: DiskPreparationMode;
  mountBasePath?: string;
  confirmDestructive: boolean;
}

export interface SettingsPageProps {
  t: TranslationDictionary;
}

export interface PlanningRowVm extends VirtualMachine {
  protectedState: boolean;
}
