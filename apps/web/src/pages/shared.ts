import type { AppDataState } from "../hooks/useAppData";
import type { Language, TranslationDictionary } from "../i18n";
import type { DiskPreparationMode, ExternalBackupRun, ExternalDisk, VirtualMachine } from "../types";

export interface PageCommonProps {
  data: AppDataState;
  language: Language;
  t: TranslationDictionary;
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
