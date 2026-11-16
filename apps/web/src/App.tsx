import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { getExternalBackupPreview, AUTH_EXPIRED_EVENT } from "./api";
import { AuthProvider, useAuth } from "./AuthContext";
import { LoginPage } from "./pages/LoginPage";
import { SETUP_PATH, reconfigureStep, setupRedirect } from "./setup/logic";
import { AppShellV2, LegacyPage } from "./shell/AppShellV2";
import { NoticeToasts } from "./shell/NoticeToasts";
import { DashboardView } from "./views/dashboard/DashboardView";
import { DisksView } from "./views/disks/DisksView";
import { ActivityView } from "./views/activity/ActivityView";
import { PlanningView } from "./views/planning/PlanningView";
import { IntegrationsView } from "./views/integrations/IntegrationsView";
import { ResourcesView } from "./views/resources/ResourcesView";
import { ProxmoxJobsSection } from "./views/resources/ProxmoxJobsSection";
import { expectRunModal, openRunModal } from "./runs/openRun";
import { ConfirmModal } from "./components/ConfirmModal";
import { ErrorBanner } from "./components/ErrorBanner";
import { LoadingBlock } from "./components/LoadingBlock";
import { useAppData } from "./hooks/useAppData";
import { translations, type Language } from "./i18n";
import { NotificationsPage } from "./pages/NotificationsPage";
import { SettingsPage } from "./pages/SettingsPage";
import type { ExternalDisk } from "./types";

interface ConfirmState {
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  tone: "danger" | "warning" | "info";
  onConfirm: () => void;
  extra?: ReactNode;
}

// Inner app — rendered only when authenticated
function AuthenticatedApp() {
  // Language: English by default, per account + localStorage (AuthContext).
  const { markSessionExpired, language, setLanguage } = useAuth();
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  // Read at confirm-time by handleExternalBackupRequest; a ref (not state) so
  // toggling the checkbox doesn't re-render/recreate the modal's onConfirm closure.
  const autoEjectAfterSuccessRef = useRef(true);

  // Listen for auth-expired events from api.ts
  useEffect(() => {
    const handler = () => markSessionExpired();
    window.addEventListener(AUTH_EXPIRED_EVENT, handler);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handler);
  }, [markSessionExpired]);

  const {
    data,
    loading,
    error,
    notices,
    isSaving,
    proxmoxSyncing,
    pbsSyncing,
    pbsInventoryByVmId,
    load,
    refresh,
    dismissNotice,
    mutateAssetIgnore,
    mutateBackupJobSelection,
    runProxmoxSync,
    runPBSSync,
    startExternalBackup,
    ejectDisk,
    cleanupActivityRuns,
  } = useAppData();

  const t = translations[language];
  // Phase 6c: the source datastore was deleted on PBS (last datastore check).
  const missingDatastore =
    data?.datastoreHealth?.checked && data.datastoreHealth.source?.state === "missing" ? data.datastoreHealth.source.name : null;
  const runsKey = data
    ? [
        ...data.externalBackupRuns.map((run) => `${run.id}:${run.status}`),
        ...data.scheduledBackupRuns.map((run) => `p${run.id}:${run.status}`),
        `e${data.scheduledBackupEvents.length}`,
      ].join(",")
    : "";

  function handleLanguageChange(nextLanguage: Language) {
    setLanguage(nextLanguage);
  }


  function openConfirm(nextState: ConfirmState) { setConfirmState(nextState); }
  function closeConfirm() { setConfirmState(null); }

  async function handleExternalBackupRequest(disk: ExternalDisk) {
    if (!disk.trusted) {
      openConfirm({
        title: t.confirmExternalBackupTitle,
        description: t.externalBackupBlockedUntrusted,
        confirmLabel: t.dismiss, cancelLabel: t.cancel, tone: "warning",
        onConfirm: closeConfirm,
      });
      return;
    }
    if (!disk.connected) {
      openConfirm({
        title: t.confirmExternalBackupTitle,
        description: t.externalBackupBlockedDisconnected,
        confirmLabel: t.dismiss, cancelLabel: t.cancel, tone: "warning",
        onConfirm: closeConfirm,
      });
      return;
    }
    try {
      const preview = await getExternalBackupPreview(disk.id);
      const modeLabel = preview.mode === "dedicated" ? t.externalBackupDedicatedMode : t.externalBackupCoexistenceMode;
      const preserveText = preview.preserves_existing_data ? t.externalBackupPreservesData : t.externalBackupUsesDedicatedPath;
      const preparationWarning = disk.prepared_as_pbs_datastore ? t.externalBackupReuseWarning : t.externalBackupDestructiveWarning;
      const loopSizeText = preview.mode === "coexistence" && preview.loop_image_size_gb !== null
        ? `${t.externalBackupLoopSize}: ${preview.loop_image_size_gb} GiB.` : null;
      const loopWarningText = preview.loop_image_size_warning ? t.externalBackupLoopSizeWarning : null;
      autoEjectAfterSuccessRef.current = true;
      openConfirm({
        title: t.confirmExternalBackupTitle,
        description: [
          `${t.confirmExternalBackupDescription}`,
          `${t.diskName}: ${disk.display_name}`,
          `${t.externalBackupMode}: ${modeLabel}`,
          `${t.externalBackupTargetPath}: ${preview.target_path}`,
          `${t.externalBackupPBSHandoff}`,
          `${t.externalBackupPBSExclusive}`,
          preparationWarning, preserveText, loopSizeText, loopWarningText,
        ].filter(Boolean).join(" "),
        confirmLabel: t.externalBackupAction,
        cancelLabel: t.cancel,
        tone: "info",
        extra: (
          <label className="checkbox-field">
            <input
              defaultChecked
              onChange={(event) => { autoEjectAfterSuccessRef.current = event.target.checked; }}
              type="checkbox"
            />
            <span>{t.externalBackupAutoEject}</span>
          </label>
        ),
        onConfirm: () => {
          closeConfirm();
          // Long action: follow it in the live run modal.
          void startExternalBackup(disk.id, t.externalBackupSummary, autoEjectAfterSuccessRef.current).then((run) => {
            if (run) openRunModal(run.id);
          });
        },
      });
    } catch (previewError) {
      openConfirm({
        title: t.confirmExternalBackupTitle,
        description: previewError instanceof Error ? previewError.message : t.error,
        confirmLabel: t.dismiss, cancelLabel: t.cancel, tone: "warning",
        onConfirm: closeConfirm,
      });
    }
  }

  function handleDiskEjectRequest(disk: ExternalDisk) {
    openConfirm({
      title: t.ejectDiskTitle,
      description: [`${t.diskName}: ${disk.display_name}`, t.ejectDiskConfirmation].join(" "),
      confirmLabel: t.ejectDiskAction,
      cancelLabel: t.cancel,
      tone: "warning",
      onConfirm: () => {
        closeConfirm();
        expectRunModal(); // the eject run id arrives over SSE
        void ejectDisk(disk.id, t.ejectDiskSuccess);
      },
    });
  }

  if (loading) {
    return (
      <div className="centered-shell">
        <LoadingBlock label={t.loading} />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="centered-shell">
        <section className="panel-card panel-narrow">
          <h1 className="page-title">{t.error}</h1>
          {error ? <ErrorBanner dismissLabel={t.dismiss} message={error} /> : null}
          <button className="action-button" onClick={() => void load()} type="button">
            {t.retry}
          </button>
        </section>
      </div>
    );
  }

  return (
    <>
      <AppShellV2 language={language} onLanguageChange={handleLanguageChange} t={t} disks={data.disks} vms={data.vms}>
        {/* B26: no global banner, every notice is a toast. */}
        <NoticeToasts notices={notices} onShown={dismissNotice} t={t} />

        <Routes>
          <Route
            element={
              <DashboardView
                overview={data.overview}
                disks={data.disks}
                runs={data.externalBackupRuns}
                events={data.scheduledBackupEvents}
                scheduledRuns={data.scheduledBackupRuns}
                agentStatus={data.agentStatus}
                agentConfigured={data.agentStatus.last_heartbeat_at !== null}
                pveConnected={data.proxmoxStatus.connected}
                pbsConnected={data.pbsStatus.connected}
                language={language}
                t={t}
                onChanged={refresh}
                missingDatastore={missingDatastore}
              />
            }
            path="/"
          />
          <Route
            element={
              <>
                <ResourcesView
                  vms={data.vms}
                  jobs={data.proxmoxBackupJobs}
                  language={language}
                  t={t}
                  onIgnore={(vm, ignored) => mutateAssetIgnore(vm, ignored)}
                  onJobSelection={(jobId, vmids) => mutateBackupJobSelection(jobId, vmids)}
                />
                <div className="mt-8">
                  <ProxmoxJobsSection jobs={data.proxmoxBackupJobs} vms={data.vms} language={language} t={t} onChanged={refresh} />
                </div>
              </>
            }
            path="/assets"
          />
          <Route
            element={
              <DisksView
                disks={data.disks}
                runs={data.externalBackupRuns}
                language={language}
                t={t}
                onBackup={(disk) => void handleExternalBackupRequest(disk)}
                onEject={handleDiskEjectRequest}
                isStarting={(diskId) => isSaving(`external-backup-${diskId}`)}
                isEjecting={(diskId) => isSaving(`disk-eject-${diskId}`)}
                onChanged={refresh}
                datastoreHealth={data.datastoreHealth}
              />
            }
            path="/disks"
          />
          <Route
            element={
              <PlanningView
                events={data.scheduledBackupEvents}
                disks={data.disks}
                vms={data.vms}
                defaultDatastore={data.pbsStatus.datastore ?? ""}
                language={language}
                t={t}
                refreshKey={runsKey}
                onChanged={refresh}
              />
            }
            path="/planning"
          />
          <Route
            element={
              <IntegrationsView
                language={language}
                t={t}
                onSync={(key) => (key === "pve" ? runProxmoxSync(t.proxmoxSyncSummary) : runPBSSync(t.pbsSyncSummary))}
                syncing={(key) => (key === "pve" ? proxmoxSyncing : pbsSyncing)}
                missingDatastore={missingDatastore}
                onDatastoreChanged={refresh}
              />
            }
            path="/integrations"
          />
          <Route
            element={
              <ActivityView
                language={language}
                t={t}
                refreshKey={runsKey}
                cleaning={isSaving("activity-cleanup")}
                onCleanup={() => cleanupActivityRuns(10, t.activityCleanupSummary)}
              />
            }
            path="/activity"
          />
          <Route element={<LegacyPage><NotificationsPage language={language} t={t} /></LegacyPage>} path="/notifications" />
          <Route element={<LegacyPage><SettingsPage t={t} /></LegacyPage>} path="/settings" />
        </Routes>

      <ConfirmModal
        cancelLabel={confirmState?.cancelLabel ?? t.cancel}
        confirmLabel={confirmState?.confirmLabel ?? t.confirm}
        description={confirmState?.description ?? ""}
        extra={confirmState?.extra}
        onCancel={closeConfirm}
        onConfirm={() => confirmState?.onConfirm()}
        open={confirmState !== null}
        title={confirmState?.title ?? ""}
        tone={confirmState?.tone ?? "warning"}
      />
      </AppShellV2>
    </>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}

// The wizard (Tailwind + shadcn/ui, Geist) is its own chunk: the other pages
// never load its CSS or fonts unless the setup is running.
const SetupWizard = lazy(() => import("./setup/SetupWizard").then((module) => ({ default: module.SetupWizard })));

function AppInner() {
  const { status, setupCompleted, markSetupCompleted, language } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // We need t for LoginPage — use default language here
  const t = translations[language];

  const loading = (
    <div className="centered-shell">
      <LoadingBlock label={t.loading} />
    </div>
  );
  const wizard = (
    <Suspense fallback={loading}>
      <SetupWizard
        onFinished={(path) => {
          markSetupCompleted();
          navigate(path ?? "/", { replace: true });
        }}
      />
    </Suspense>
  );

  if (status === "loading") {
    return loading;
  }
  if (status === "needs-setup") {
    return location.pathname === SETUP_PATH ? wizard : <Navigate to={SETUP_PATH} replace />;
  }
  if (status !== "authenticated") {
    return <LoginPage t={t} />;
  }
  const reconfigure = setupCompleted ? reconfigureStep(location.pathname) : null;
  if (reconfigure) {
    return (
      <Suspense fallback={loading}>
        <SetupWizard reconfigure={reconfigure} onFinished={(path) => navigate(path ?? "/integrations", { replace: true })} />
      </Suspense>
    );
  }
  const redirect = setupRedirect(location.pathname, setupCompleted);
  if (redirect) {
    return <Navigate to={redirect} replace />;
  }
  if (setupCompleted === false) {
    return wizard;
  }

  return <AuthenticatedApp />;
}
